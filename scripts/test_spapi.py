"""
Standalone SP-API credential test.

Tests each authentication step in isolation so you can see exactly where
a failure occurs.

Usage:
    python scripts/test_spapi.py

Steps tested:
    1. Load credentials from .env
    2. Exchange refresh_token → LWA access_token
    3. AWS STS AssumeRole → temporary credentials
    4. SigV4-signed GET /sellers/v1/marketplaceParticipations
"""

import asyncio
import hashlib
import hmac
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import boto3
import httpx
from dotenv import dotenv_values

# ── Step 1: Load .env ─────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print("Step 1: Loading credentials from .env")
print("=" * 60)

env_path = Path(__file__).parent.parent / ".env"
if not env_path.exists():
    print(f"  FAIL: .env file not found at {env_path}")
    sys.exit(1)

env = dotenv_values(env_path)

required = [
    "SP_API_CLIENT_ID",
    "SP_API_CLIENT_SECRET",
    "SP_API_REFRESH_TOKEN",
    "SP_API_AWS_ACCESS_KEY",
    "SP_API_AWS_SECRET_KEY",
    "SP_API_ROLE_ARN",
    "SP_API_MARKETPLACE_CA",
]

missing = [k for k in required if not env.get(k)]
if missing:
    print(f"  FAIL: Missing required variables: {', '.join(missing)}")
    sys.exit(1)

CLIENT_ID      = env["SP_API_CLIENT_ID"]
CLIENT_SECRET  = env["SP_API_CLIENT_SECRET"]
REFRESH_TOKEN  = env["SP_API_REFRESH_TOKEN"]
AWS_ACCESS_KEY = env["SP_API_AWS_ACCESS_KEY"]
AWS_SECRET_KEY = env["SP_API_AWS_SECRET_KEY"]
ROLE_ARN       = env["SP_API_ROLE_ARN"]
MARKETPLACE_ID = env["SP_API_MARKETPLACE_CA"]

print(f"  OK — client_id    : {CLIENT_ID[:20]}...")
print(f"  OK — refresh_token: {REFRESH_TOKEN[:20]}...")
print(f"  OK — aws_key      : {AWS_ACCESS_KEY[:10]}...")
print(f"  OK — role_arn     : {ROLE_ARN}")
print(f"  OK — marketplace  : {MARKETPLACE_ID}")


# ── Step 2: LWA token exchange ────────────────────────────────────────────────

async def get_lwa_token() -> str:
    print("\n" + "=" * 60)
    print("Step 2: Exchanging refresh_token → LWA access_token")
    print("=" * 60)
    print(f"  POST https://api.amazon.com/auth/o2/token")

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                "https://api.amazon.com/auth/o2/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": REFRESH_TOKEN,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.ConnectError as e:
        print(f"  FAIL: Network error — {e}")
        sys.exit(1)
    except httpx.TimeoutException:
        print("  FAIL: Request timed out after 15s")
        sys.exit(1)

    if response.status_code != 200:
        print(f"  FAIL: HTTP {response.status_code}")
        try:
            body = response.json()
            print(f"  Error type   : {body.get('error', 'unknown')}")
            print(f"  Error detail : {body.get('error_description', response.text)}")
        except Exception:
            print(f"  Raw response : {response.text[:500]}")
        sys.exit(1)

    data = response.json()
    token = data.get("access_token", "")
    expires_in = data.get("expires_in", "?")

    print(f"  OK — access_token : {token[:30]}...")
    print(f"  OK — expires_in   : {expires_in}s")
    return token


# ── Step 3: AWS STS AssumeRole ────────────────────────────────────────────────

def get_aws_credentials() -> tuple[str, str, str]:
    print("\n" + "=" * 60)
    print("Step 3: AWS STS AssumeRole → temporary credentials")
    print("=" * 60)
    print(f"  Role ARN: {ROLE_ARN}")

    try:
        sts = boto3.client(
            "sts",
            aws_access_key_id=AWS_ACCESS_KEY,
            aws_secret_access_key=AWS_SECRET_KEY,
            region_name="us-east-1",
        )
        response = sts.assume_role(
            RoleArn=ROLE_ARN,
            RoleSessionName="habib-os-spapi-test",
            DurationSeconds=900,  # 15 min — minimum for test
        )
    except Exception as e:
        # boto3 raises botocore.exceptions.ClientError
        error_code = getattr(getattr(e, "response", {}).get("Error", {}), "get", lambda k: None)("Code")
        print(f"  FAIL: STS AssumeRole failed — {e}")
        print()
        print("  Common causes:")
        print("    - IAM user doesn't have sts:AssumeRole permission on the role")
        print("    - Role trust policy doesn't allow this IAM user")
        print("    - Wrong AWS_ACCESS_KEY / AWS_SECRET_KEY")
        sys.exit(1)

    creds = response["Credentials"]
    access_key    = creds["AccessKeyId"]
    secret_key    = creds["SecretAccessKey"]
    session_token = creds["SessionToken"]
    expiry        = creds["Expiration"]

    print(f"  OK — temp_access_key : {access_key[:15]}...")
    print(f"  OK — session_token   : {session_token[:20]}...")
    print(f"  OK — expires_at      : {expiry}")
    return access_key, secret_key, session_token


# ── Step 4: SigV4-signed SP-API request ──────────────────────────────────────

def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _sigv4_auth_header(
    method: str,
    url: str,
    lwa_token: str,
    aws_access_key: str,
    aws_secret_key: str,
    aws_session_token: str,
) -> dict[str, str]:
    """Build the full set of headers required for a SigV4-signed SP-API request."""
    parsed = urlparse(url)
    host   = parsed.netloc
    uri    = parsed.path or "/"
    query  = parsed.query   # already URL-encoded query string (empty for this call)

    now        = datetime.now(timezone.utc)
    amz_date   = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    region     = "us-east-1"
    service    = "execute-api"

    headers = {
        "accept":                "application/json",
        "content-type":         "application/json",
        "host":                  host,
        "x-amz-access-token":   lwa_token,
        "x-amz-date":           amz_date,
        "x-amz-security-token": aws_session_token,
    }

    canonical_headers = "".join(
        f"{k}:{v.strip()}\n" for k, v in sorted(headers.items())
    )
    signed_headers_str = ";".join(sorted(headers))

    payload_hash = hashlib.sha256(b"").hexdigest()  # empty body for GET

    canonical_request = "\n".join([
        method.upper(),
        uri,
        query,
        canonical_headers,
        signed_headers_str,
        payload_hash,
    ])

    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    # Derive signing key
    k_date    = _sign(("AWS4" + aws_secret_key).encode(), date_stamp)
    k_region  = _sign(k_date, region)
    k_service = _sign(k_region, service)
    k_signing = _sign(k_service, "aws4_request")

    signature = hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={aws_access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers_str}, Signature={signature}"
    )
    return headers


async def call_marketplace_participations(
    lwa_token: str,
    aws_access_key: str,
    aws_secret_key: str,
    aws_session_token: str,
) -> None:
    url = "https://sellingpartnerapi-na.amazon.com/sellers/v1/marketplaceParticipations"

    print("\n" + "=" * 60)
    print("Step 4: GET /sellers/v1/marketplaceParticipations")
    print("=" * 60)
    print(f"  URL: {url}")

    headers = _sigv4_auth_header(
        method="GET",
        url=url,
        lwa_token=lwa_token,
        aws_access_key=aws_access_key,
        aws_secret_key=aws_secret_key,
        aws_session_token=aws_session_token,
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(url, headers=headers)
    except httpx.ConnectError as e:
        print(f"  FAIL: Network error — {e}")
        sys.exit(1)
    except httpx.TimeoutException:
        print("  FAIL: Request timed out after 30s")
        sys.exit(1)

    rate_limit = response.headers.get("x-amzn-ratelimit-limit", "N/A")
    request_id = response.headers.get("x-amzn-requestid", "N/A")
    print(f"  HTTP status  : {response.status_code}")
    print(f"  Request ID   : {request_id}")
    print(f"  Rate limit   : {rate_limit} req/s")

    if response.status_code != 200:
        print(f"\n  FAIL: HTTP {response.status_code}")
        try:
            body = response.json()
            errors = body.get("errors", [body])
            for err in errors:
                print(f"  Error code   : {err.get('code', 'unknown')}")
                print(f"  Error message: {err.get('message', response.text[:300])}")
        except Exception:
            print(f"  Raw response : {response.text[:500]}")
        print()
        print("  Common causes:")
        if response.status_code == 403:
            print("    - LWA token invalid or expired")
            print("    - SigV4 signature wrong (check region/service constants)")
            print("    - Role doesn't have the right SP-API permissions")
        elif response.status_code == 401:
            print("    - Refresh token revoked or for wrong application")
        sys.exit(1)

    data = response.json()

    print(f"\n  OK — Full response:")
    print()
    print(json.dumps(data, indent=2, ensure_ascii=False))

    # Pretty summary
    payload = data.get("payload", [])
    print()
    print("  Summary:")
    print(f"    Marketplaces returned: {len(payload)}")
    for item in payload:
        mp = item.get("marketplace", {})
        part = item.get("participation", {})
        print(f"    • {mp.get('id')} — {mp.get('name')} "
              f"({'active' if part.get('isParticipating') else 'inactive'})")


# ── Run ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    lwa_token = await get_lwa_token()
    access_key, secret_key, session_token = get_aws_credentials()
    await call_marketplace_participations(lwa_token, access_key, secret_key, session_token)

    print("\n" + "=" * 60)
    print("All steps passed. SP-API credentials are working correctly.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
