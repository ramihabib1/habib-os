"""
Base SP-API HTTP client with AWS SigV4 signing.

All SP-API modules inherit from or instantiate SPAPIClient.
Handles:
  - LWA access token injection
  - AWS SigV4 request signing
  - Exponential backoff with jitter (tenacity)
  - Rate limit header logging
  - Pagination via nextToken
  - Persistent httpx client (reuses TCP connections across requests)
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from src.config.settings import settings
from src.spapi.auth import get_aws_credentials, get_lwa_access_token
from src.utils.logging import get_logger

logger = get_logger(__name__)

_REGION = "us-east-1"
_SERVICE = "execute-api"


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _get_signing_key(secret_key: str, date_stamp: str) -> bytes:
    k_date = _sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _sign(k_date, _REGION)
    k_service = _sign(k_region, _SERVICE)
    k_signing = _sign(k_service, "aws4_request")
    return k_signing


def _sigv4_headers(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    aws_access_key: str,
    aws_secret_key: str,
    aws_session_token: str,
) -> dict[str, str]:
    """
    Build SigV4-signed headers for a request.
    Returns a new headers dict with Authorization + x-amz-* fields added.
    Does not mutate the input headers dict.
    """
    parsed = urlparse(url)
    host = parsed.netloc
    uri = parsed.path or "/"
    query = parsed.query

    now = datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    signed = {
        **headers,
        "host": host,
        "x-amz-date": amz_date,
        "x-amz-security-token": aws_session_token,
    }

    canonical_headers = "".join(
        f"{k.lower()}:{v.strip()}\n"
        for k, v in sorted(signed.items())
    )
    signed_headers_str = ";".join(sorted(k.lower() for k in signed))

    payload_hash = hashlib.sha256(body).hexdigest()

    canonical_request = "\n".join([
        method.upper(),
        uri,
        query,
        canonical_headers,
        signed_headers_str,
        payload_hash,
    ])

    credential_scope = f"{date_stamp}/{_REGION}/{_SERVICE}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        credential_scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signing_key = _get_signing_key(aws_secret_key, date_stamp)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    signed["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={aws_access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers_str}, Signature={signature}"
    )
    return signed


class SPAPIClient:
    """
    Async SP-API client. Instantiate once per job run.
    Reuses a single httpx.AsyncClient for all requests (connection pooling).
    Use as an async context manager to ensure the client is properly closed:

        async with SPAPIClient() as client:
            data = await client.get("/fba/inventory/v1/summaries", ...)
    """

    def __init__(self, marketplace_id: str | None = None) -> None:
        self.base_url = settings.SP_API_BASE_URL
        self.marketplace_id = marketplace_id or settings.SP_API_MARKETPLACE_CA
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=30)

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def __aenter__(self) -> SPAPIClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        wait=wait_exponential_jitter(initial=1, max=30),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Execute a signed SP-API request.

        Args:
            method: HTTP verb (GET, POST, PUT, PATCH, DELETE)
            path:   SP-API path e.g. "/fba/inventory/v1/summaries"
            params: Query string parameters
            body:   JSON request body (POST/PUT)

        Returns:
            Parsed JSON response body.
        """
        lwa_token = await get_lwa_access_token()
        aws_creds = await get_aws_credentials()

        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        body_bytes = json.dumps(body).encode() if body else b""

        headers: dict[str, str] = {
            "x-amz-access-token": lwa_token,
            "content-type": "application/json",
            "accept": "application/json",
        }

        signed_headers = _sigv4_headers(
            method=method,
            url=url,
            headers=headers,
            body=body_bytes,
            aws_access_key=aws_creds.access_key,
            aws_secret_key=aws_creds.secret_key,
            aws_session_token=aws_creds.session_token,
        )

        response = await self._http.request(
            method=method,
            url=url,
            headers=signed_headers,
            content=body_bytes,
        )

        rate_limit = response.headers.get("x-amzn-ratelimit-limit")
        if rate_limit:
            logger.debug("sp_api_rate_limit", path=path, limit=rate_limit)

        if response.status_code == 429:
            logger.warning("sp_api_rate_limited", path=path)
            response.raise_for_status()

        response.raise_for_status()
        return response.json()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await self.request("POST", path, body=body)

    async def paginate(
        self,
        path: str,
        params: dict[str, Any],
        data_key: str,
        next_token_key: str = "nextToken",
        next_token_param: str = "nextToken",
    ) -> list[dict[str, Any]]:
        """
        Iterate through all pages of a paginated SP-API endpoint.

        Args:
            path:             API path
            params:           Initial query params
            data_key:         Key in response payload containing the list of items
            next_token_key:   Key in response payload containing the next page token
            next_token_param: Query param name to pass the token on next request

        Returns:
            Flat list of all items across all pages.
        """
        results: list[dict[str, Any]] = []
        page_params = dict(params)

        while True:
            response = await self.get(path, params=page_params)
            payload = response.get("payload", response)

            items = payload.get(data_key, [])
            results.extend(items)

            next_token = payload.get(next_token_key)
            if not next_token:
                break

            page_params = {next_token_param: next_token}
            logger.debug("sp_api_paginating", path=path, items_so_far=len(results))

        return results
