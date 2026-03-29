"""
Amazon SP-API authentication.

Two layers:
  1. LWA (Login With Amazon) — exchanges refresh_token → access_token (1hr)
  2. AWS STS AssumeRole — exchanges IAM credentials → short-lived session credentials

Both tokens are cached in memory and auto-refreshed before expiry.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.config.settings import settings
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Safety margin: refresh 5 minutes before actual expiry
_REFRESH_BUFFER_SECONDS = 300


@dataclass
class LWAToken:
    access_token: str
    expires_at: float  # unix timestamp


@dataclass
class AWSCredentials:
    access_key: str
    secret_key: str
    session_token: str
    expires_at: float  # unix timestamp


# Module-level caches
_lwa_token: LWAToken | None = None
_aws_credentials: AWSCredentials | None = None


async def get_lwa_access_token() -> str:
    """
    Return a valid LWA access token, refreshing if expired or missing.
    Caches in module-level variable to minimise token requests.
    """
    global _lwa_token

    now = time.time()
    if _lwa_token and _lwa_token.expires_at - _REFRESH_BUFFER_SECONDS > now:
        return _lwa_token.access_token

    logger.info("lwa_token_refresh")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            settings.LWA_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": settings.SP_API_REFRESH_TOKEN,
                "client_id": settings.SP_API_CLIENT_ID,
                "client_secret": settings.SP_API_CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()

    _lwa_token = LWAToken(
        access_token=data["access_token"],
        expires_at=now + data.get("expires_in", 3600),
    )
    logger.info("lwa_token_refreshed", expires_in=data.get("expires_in"))
    return _lwa_token.access_token


def get_aws_credentials_sync() -> AWSCredentials:
    """
    Return valid AWS STS AssumeRole credentials, refreshing if expired.
    Uses boto3 (sync) — called once and cached.
    """
    global _aws_credentials

    now = time.time()
    if _aws_credentials and _aws_credentials.expires_at - _REFRESH_BUFFER_SECONDS > now:
        return _aws_credentials

    logger.info("aws_sts_assume_role")

    import boto3

    sts = boto3.client(
        "sts",
        aws_access_key_id=settings.SP_API_AWS_ACCESS_KEY,
        aws_secret_access_key=settings.SP_API_AWS_SECRET_KEY,
        region_name="us-east-1",
    )
    response = sts.assume_role(
        RoleArn=settings.SP_API_ROLE_ARN,
        RoleSessionName="habib-os-spapi",
        DurationSeconds=3600,
    )
    creds = response["Credentials"]

    _aws_credentials = AWSCredentials(
        access_key=creds["AccessKeyId"],
        secret_key=creds["SecretAccessKey"],
        session_token=creds["SessionToken"],
        expires_at=now + 3600,
    )
    logger.info("aws_sts_credentials_refreshed")
    return _aws_credentials
