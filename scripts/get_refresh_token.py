"""
One-time script: complete the LWA OAuth flow to obtain a refresh_token.

Usage:
  python scripts/get_refresh_token.py

Steps:
  1. Script prints an authorization URL
  2. Open it in your browser and log in to Seller Central
  3. Amazon redirects to the callback URL with ?code=xxx&state=xxx
  4. Paste the redirect URL (or just the code= value) when prompted
  5. Script exchanges the code for a refresh_token and prints it
  6. Copy the refresh_token into your .env file

You only need to run this once. The refresh_token does not expire.
"""

import asyncio
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

# Minimal .env loading without importing settings (settings requires all vars to be set)
from pathlib import Path
from dotenv import dotenv_values

env = dotenv_values(Path(__file__).parent.parent / ".env")

CLIENT_ID = env.get("SP_API_CLIENT_ID") or input("SP_API_CLIENT_ID: ").strip()
CLIENT_SECRET = env.get("SP_API_CLIENT_SECRET") or input("SP_API_CLIENT_SECRET: ").strip()

# Use a local redirect URI for desktop flows
REDIRECT_URI = "https://localhost"

AUTH_URL = "https://sellercentral.amazon.ca/apps/authorize/consent"
TOKEN_URL = "https://api.amazon.com/auth/o2/token"


def build_auth_url() -> str:
    params = {
        "application_id": CLIENT_ID,
        "state": "habib-os-setup",
        "version": "beta",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        response.raise_for_status()
        return response.json()["refresh_token"]


async def main() -> None:
    print("\n=== SP-API OAuth — Get Refresh Token ===\n")
    print("1. Open this URL in your browser and authorize the app:\n")
    print(f"   {build_auth_url()}\n")
    print("2. After authorizing, you'll be redirected to https://localhost?...")
    print("   Copy the full redirect URL (or just the 'code' parameter value).\n")

    raw = input("Paste redirect URL or code: ").strip()

    # Extract code from URL if a full URL was pasted
    if raw.startswith("http"):
        parsed = urlparse(raw)
        qs = parse_qs(parsed.query)
        code = qs.get("spapi_oauth_code", qs.get("code", [None]))[0]
    else:
        code = raw

    if not code:
        print("❌ Could not extract authorization code. Aborting.")
        sys.exit(1)

    print(f"\nExchanging code: {code[:10]}...")
    refresh_token = await exchange_code(code)

    print("\n✅ Success! Add this to your .env file:\n")
    print(f"SP_API_REFRESH_TOKEN={refresh_token}\n")


if __name__ == "__main__":
    asyncio.run(main())
