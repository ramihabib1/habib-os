"""
Amazon Advertising API client.

Separate from SP-API: different base URL, different OAuth client,
different profile-based auth header.

Handles:
  - Separate LWA token flow for Advertising API credentials
  - Campaign / ad group / keyword structure sync
  - Async report requests (POST → poll → download)
"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from src.config.settings import settings
from src.utils.logging import get_logger

logger = get_logger(__name__)

_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
_REFRESH_BUFFER = 300


@dataclass
class AdsToken:
    access_token: str
    expires_at: float  # unix timestamp


# Module-level token cache and lock (separate from SP-API token)
_ads_token: AdsToken | None = None
_ads_lock = asyncio.Lock()


async def _get_ads_access_token() -> str:
    """
    Get LWA access token for the Advertising API (separate client credentials).
    Thread-safe: uses asyncio.Lock to prevent duplicate refresh calls.
    """
    global _ads_token

    now = time.time()
    if _ads_token and _ads_token.expires_at - _REFRESH_BUFFER > now:
        return _ads_token.access_token

    async with _ads_lock:
        now = time.time()
        if _ads_token and _ads_token.expires_at - _REFRESH_BUFFER > now:
            return _ads_token.access_token

        logger.info("ads_token_refresh")
        async with httpx.AsyncClient() as client:
            response = await client.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": settings.ADS_API_REFRESH_TOKEN,
                    "client_id": settings.ADS_API_CLIENT_ID,
                    "client_secret": settings.ADS_API_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()

        _ads_token = AdsToken(
            access_token=data["access_token"],
            expires_at=now + data.get("expires_in", 3600),
        )
        logger.info("ads_token_refreshed")

    return _ads_token.access_token


def _ads_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Amazon-Advertising-API-ClientId": settings.ADS_API_CLIENT_ID,
        "Amazon-Advertising-API-Scope": settings.ADS_API_PROFILE_ID,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


class AdsAPIClient:
    """Async Advertising API client."""

    def __init__(self) -> None:
        self.base_url = settings.ADS_API_BASE_URL

    async def get(self, path: str, params: dict | None = None) -> Any:
        token = await _get_ads_access_token()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                f"{self.base_url}{path}",
                headers=_ads_headers(token),
                params=params,
            )
            response.raise_for_status()
            return response.json()

    async def post(self, path: str, body: dict) -> Any:
        token = await _get_ads_access_token()
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base_url}{path}",
                headers=_ads_headers(token),
                content=json.dumps(body),
            )
            response.raise_for_status()
            return response.json()

    # ── Campaign structure ────────────────────────────────────────────────────

    async def list_campaigns(self) -> list[dict]:
        """Fetch all Sponsored Products campaigns."""
        data = await self.get("/v2/sp/campaigns", params={"stateFilter": "enabled,paused,archived"})
        return data if isinstance(data, list) else data.get("campaigns", [])

    async def list_ad_groups(self, campaign_id: str | None = None) -> list[dict]:
        params: dict = {"stateFilter": "enabled,paused,archived"}
        if campaign_id:
            params["campaignId"] = campaign_id
        data = await self.get("/v2/sp/adGroups", params=params)
        return data if isinstance(data, list) else data.get("adGroups", [])

    async def list_keywords(self, ad_group_id: str | None = None) -> list[dict]:
        params: dict = {"stateFilter": "enabled,paused,archived"}
        if ad_group_id:
            params["adGroupId"] = ad_group_id
        data = await self.get("/v2/sp/keywords", params=params)
        return data if isinstance(data, list) else data.get("keywords", [])

    # ── Report requests ───────────────────────────────────────────────────────

    async def request_keyword_report(self, report_date: str) -> str:
        """
        Request a keyword-level performance report for a given date (YYYYMMDD).
        Returns the reportId for polling.
        """
        response = await self.post("/v2/sp/keywords/report", {
            "reportDate": report_date,
            "metrics": (
                "campaignId,adGroupId,keywordId,keywordText,matchType,"
                "impressions,clicks,cost,attributedSales14d,attributedConversions14d"
            ),
        })
        return response["reportId"]

    async def wait_for_report(self, report_id: str, max_wait_seconds: int = 300) -> str:
        """
        Poll until report is ready. Returns the download URL.
        Raises TimeoutError if not ready within max_wait_seconds.
        """
        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            status = await self.get(f"/v2/reports/{report_id}")
            if status["status"] == "SUCCESS":
                return status["location"]
            if status["status"] == "FAILURE":
                raise RuntimeError(f"Report {report_id} failed: {status}")
            await asyncio.sleep(15)

        raise TimeoutError(f"Report {report_id} not ready after {max_wait_seconds}s")

    async def download_report(self, url: str) -> list[dict]:
        """Download and decompress a gzipped JSON report."""
        token = await _get_ads_access_token()
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url, headers=_ads_headers(token))
            response.raise_for_status()

        with gzip.open(io.BytesIO(response.content), "rt", encoding="utf-8") as f:
            return json.load(f)

    async def get_profiles(self) -> list[dict]:
        """List all advertising profiles — used once to get ADS_API_PROFILE_ID."""
        return await self.get("/v2/profiles")
