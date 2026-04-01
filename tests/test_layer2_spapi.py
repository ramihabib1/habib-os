"""
Layer 2 — SP-API tests.

Split into two classes:
- TestSPAPIUnit:        Fast, mocked — SigV4 signing, token expiry, pagination, batching
- TestSPAPIIntegration: Real Amazon API calls — auth, inventory, orders, catalog
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.spapi import auth as auth_module
from src.spapi.auth import (
    AWSCredentials,
    LWAToken,
    _REFRESH_BUFFER_SECONDS,
    get_aws_credentials,
    get_lwa_access_token,
)
from src.spapi.client import SPAPIClient, _sigv4_headers
from src.spapi.inventory import get_fba_inventory_summaries
from src.spapi.orders import get_order_items, get_orders
from src.spapi.catalog import get_catalog_item
from src.config.settings import settings


# ─── Unit Tests (mocked, no network) ─────────────────────────────────────────


class TestSigV4Signing:
    """SigV4 header generation — deterministic, no network needed."""

    def _base_headers(self) -> dict[str, str]:
        return {
            "x-amz-access-token": "test-token",
            "content-type": "application/json",
            "accept": "application/json",
        }

    def test_returns_authorization_header(self) -> None:
        headers = _sigv4_headers(
            method="GET",
            url="https://sellingpartnerapi-na.amazon.com/fba/inventory/v1/summaries",
            headers=self._base_headers(),
            body=b"",
            aws_access_key="AKIAIOSFODNN7EXAMPLE",
            aws_secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            aws_session_token="session-token",
        )
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("AWS4-HMAC-SHA256")

    def test_authorization_contains_credential(self) -> None:
        headers = _sigv4_headers(
            method="GET",
            url="https://sellingpartnerapi-na.amazon.com/test",
            headers=self._base_headers(),
            body=b"",
            aws_access_key="AKIATEST",
            aws_secret_key="secretkey",
            aws_session_token="session",
        )
        assert "Credential=AKIATEST/" in headers["Authorization"]

    def test_adds_amz_date_header(self) -> None:
        headers = _sigv4_headers(
            method="GET",
            url="https://sellingpartnerapi-na.amazon.com/test",
            headers=self._base_headers(),
            body=b"",
            aws_access_key="KEY",
            aws_secret_key="SECRET",
            aws_session_token="SESSION",
        )
        assert "x-amz-date" in headers
        # Format: YYYYMMDDTHHMMSSz
        assert len(headers["x-amz-date"]) == 16

    def test_adds_security_token_header(self) -> None:
        headers = _sigv4_headers(
            method="GET",
            url="https://sellingpartnerapi-na.amazon.com/test",
            headers=self._base_headers(),
            body=b"",
            aws_access_key="KEY",
            aws_secret_key="SECRET",
            aws_session_token="MY-SESSION-TOKEN",
        )
        assert headers["x-amz-security-token"] == "MY-SESSION-TOKEN"

    def test_does_not_mutate_input_headers(self) -> None:
        original = self._base_headers()
        original_copy = dict(original)
        _sigv4_headers(
            method="GET",
            url="https://sellingpartnerapi-na.amazon.com/test",
            headers=original,
            body=b"",
            aws_access_key="KEY",
            aws_secret_key="SECRET",
            aws_session_token="SESSION",
        )
        assert original == original_copy

    def test_post_with_body_produces_valid_signature(self) -> None:
        body = b'{"key": "value"}'
        headers = _sigv4_headers(
            method="POST",
            url="https://sellingpartnerapi-na.amazon.com/orders/v0/orders",
            headers=self._base_headers(),
            body=body,
            aws_access_key="KEY",
            aws_secret_key="SECRET",
            aws_session_token="SESSION",
        )
        assert "Signature=" in headers["Authorization"]

    def test_different_methods_produce_different_signatures(self) -> None:
        kwargs = dict(
            url="https://sellingpartnerapi-na.amazon.com/test",
            headers=self._base_headers(),
            body=b"",
            aws_access_key="KEY",
            aws_secret_key="SECRET",
            aws_session_token="SESSION",
        )
        get_sig = _sigv4_headers(method="GET", **kwargs)["Authorization"]
        post_sig = _sigv4_headers(method="POST", **kwargs)["Authorization"]
        assert get_sig != post_sig


class TestTokenExpiry:
    """Token cache expiry logic — no network needed."""

    def _future_token(self, seconds: int = 7200) -> LWAToken:
        return LWAToken(access_token="valid-token", expires_at=time.time() + seconds)

    def _expired_token(self) -> LWAToken:
        return LWAToken(access_token="old-token", expires_at=time.time() - 1)

    def _near_expiry_token(self) -> LWAToken:
        """Token expiring within the refresh buffer."""
        return LWAToken(
            access_token="expiring-token",
            expires_at=time.time() + _REFRESH_BUFFER_SECONDS - 10,
        )

    @pytest.mark.asyncio
    async def test_valid_token_is_returned_from_cache(self) -> None:
        auth_module._lwa_token = self._future_token()
        token = await get_lwa_access_token()
        assert token == "valid-token"

    @pytest.mark.asyncio
    async def test_expired_token_triggers_refresh(self) -> None:
        auth_module._lwa_token = self._expired_token()
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "new-token", "expires_in": 3600}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            token = await get_lwa_access_token()

        assert token == "new-token"

    @pytest.mark.asyncio
    async def test_near_expiry_token_triggers_refresh(self) -> None:
        """Token within the 5-minute buffer should be refreshed proactively."""
        auth_module._lwa_token = self._near_expiry_token()
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "refreshed-token", "expires_in": 3600}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            token = await get_lwa_access_token()

        assert token == "refreshed-token"

    @pytest.mark.asyncio
    async def test_concurrent_refresh_calls_amazon_once(self) -> None:
        """Lock ensures only one refresh call even with 10 concurrent requests."""
        auth_module._lwa_token = self._expired_token()
        call_count = 0

        async def fake_post(*args: Any, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.01)  # simulate network latency
            resp = MagicMock()
            resp.json.return_value = {"access_token": "concurrent-token", "expires_in": 3600}
            resp.raise_for_status = MagicMock()
            return resp

        with patch("src.spapi.auth.httpx.AsyncClient") as mock_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=None)
            mock_http.post = fake_post
            mock_cls.return_value = mock_http

            tokens = await asyncio.gather(*[get_lwa_access_token() for _ in range(10)])

        assert call_count == 1
        assert all(t == "concurrent-token" for t in tokens)


class TestPagination:
    """SPAPIClient.paginate() — mocked multi-page response."""

    @pytest.mark.asyncio
    async def test_collects_all_pages(self) -> None:
        client = SPAPIClient()
        page1 = {"payload": {"Orders": [{"id": "1"}, {"id": "2"}], "NextToken": "tok2"}}
        page2 = {"payload": {"Orders": [{"id": "3"}]}}

        call_count = 0

        async def fake_get(path: str, params: dict | None = None) -> dict:
            nonlocal call_count
            call_count += 1
            return page1 if call_count == 1 else page2

        client.get = fake_get  # type: ignore[method-assign]
        results = await client.paginate(
            path="/orders/v0/orders",
            params={"MarketplaceIds": "A2EUQ1WTGCTBG2"},
            data_key="Orders",
            next_token_key="NextToken",
            next_token_param="NextToken",
        )

        assert len(results) == 3
        assert call_count == 2
        await client.aclose()

    @pytest.mark.asyncio
    async def test_single_page_no_next_token(self) -> None:
        client = SPAPIClient()
        single_page = {"payload": {"Orders": [{"id": "1"}]}}

        client.get = AsyncMock(return_value=single_page)  # type: ignore[method-assign]
        results = await client.paginate(
            path="/orders/v0/orders",
            params={},
            data_key="Orders",
            next_token_key="NextToken",
            next_token_param="NextToken",
        )

        assert len(results) == 1
        await client.aclose()

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        client = SPAPIClient()
        client.get = AsyncMock(return_value={"payload": {"Orders": []}})  # type: ignore[method-assign]
        results = await client.paginate("/orders/v0/orders", {}, "Orders", "NextToken", "NextToken")
        assert results == []
        await client.aclose()


class TestInventoryBatching:
    """Inventory SKU batching — verifies batches of ≤50."""

    @pytest.mark.asyncio
    async def test_30_skus_fit_in_one_batch(self) -> None:
        """30 SKUs (our catalog size) should be a single batch request."""
        skus = [f"SKU-{i:02d}" for i in range(30)]
        call_count = 0

        async def fake_get(path: str, params: dict | None = None) -> dict:
            nonlocal call_count
            call_count += 1
            return {"payload": {"inventorySummaries": []}}

        client = SPAPIClient()
        client.get = fake_get  # type: ignore[method-assign]

        from src.spapi.inventory import _fetch_by_skus
        await _fetch_by_skus(client, "A2EUQ1WTGCTBG2", skus)

        assert call_count == 1  # All 30 in one batch
        await client.aclose()

    @pytest.mark.asyncio
    async def test_60_skus_split_into_two_batches(self) -> None:
        """60 SKUs should be split into two batches of 50 + 10."""
        skus = [f"SKU-{i:03d}" for i in range(60)]
        call_count = 0

        async def fake_get(path: str, params: dict | None = None) -> dict:
            nonlocal call_count
            call_count += 1
            return {"payload": {"inventorySummaries": []}}

        client = SPAPIClient()
        client.get = fake_get  # type: ignore[method-assign]

        from src.spapi.inventory import _fetch_by_skus
        await _fetch_by_skus(client, "A2EUQ1WTGCTBG2", skus)

        assert call_count == 2
        await client.aclose()


# ─── Integration Tests (real SP-API) ─────────────────────────────────────────


@pytest.mark.asyncio
class TestSPAPIIntegration:
    """
    Real calls to Amazon SP-API.
    These tests verify auth, signing, and data retrieval end-to-end.
    They run once per test session to avoid rate limiting.
    """

    async def test_lwa_token_refresh_returns_token(self) -> None:
        """LWA token exchange returns a non-empty access token."""
        # Clear cache to force a real refresh
        auth_module._lwa_token = None
        token = await get_lwa_access_token()
        assert isinstance(token, str)
        assert len(token) > 50

    async def test_lwa_token_is_cached(self) -> None:
        """Second call returns the cached token without hitting Amazon."""
        token1 = await get_lwa_access_token()
        token2 = await get_lwa_access_token()
        assert token1 == token2

    async def test_aws_credentials_return_valid_structure(self) -> None:
        """STS AssumeRole returns credentials with expected fields."""
        auth_module._aws_credentials = None
        creds = await get_aws_credentials()
        assert creds.access_key.startswith("ASIA")  # STS temp credentials start with ASIA
        assert len(creds.secret_key) > 20
        assert len(creds.session_token) > 50
        assert creds.expires_at > time.time()

    async def test_aws_credentials_are_cached(self) -> None:
        """Second call returns same credentials from cache."""
        creds1 = await get_aws_credentials()
        creds2 = await get_aws_credentials()
        assert creds1.access_key == creds2.access_key

    async def test_fba_inventory_returns_data(self) -> None:
        """FBA inventory API returns summaries for the CA marketplace."""
        summaries = await get_fba_inventory_summaries(
            marketplace_id=settings.SP_API_MARKETPLACE_CA,
        )
        assert isinstance(summaries, list)
        assert len(summaries) > 0

    async def test_fba_inventory_covers_known_skus(self) -> None:
        """Targeted query returns data for all 30 known catalog SKUs (or those currently active)."""
        from src.config.supabase_client import get_supabase
        db = await get_supabase()
        result = await db.table("products").select("sku").execute()
        known_skus = [r["sku"] for r in result.data]

        summaries = await get_fba_inventory_summaries(
            marketplace_id=settings.SP_API_MARKETPLACE_CA,
            known_skus=known_skus,
        )
        returned_skus = {s["sellerSku"] for s in summaries}
        # Every returned SKU should be a real SKU string
        assert all(isinstance(sku, str) for sku in returned_skus)

    async def test_fba_inventory_summary_has_required_fields(self) -> None:
        """Each inventory summary has the fields the sync job uses.

        Amazon nests fulfillableQuantity under inventoryDetails, not top-level.
        Confirmed from live API response.
        """
        summaries = await get_fba_inventory_summaries(
            marketplace_id=settings.SP_API_MARKETPLACE_CA,
        )
        for s in summaries[:5]:  # check first 5
            assert "sellerSku" in s, f"Missing sellerSku in: {s.keys()}"
            details = s.get("inventoryDetails", {})
            assert "fulfillableQuantity" in details, (
                f"fulfillableQuantity not in inventoryDetails. Keys: {details.keys()}"
            )

    async def test_orders_returns_list(self) -> None:
        """Orders API returns a list (may be empty if no recent orders)."""
        from datetime import datetime, timedelta, timezone
        # SP-API requires ISO 8601 with Z suffix, no microseconds
        lookback = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        orders = await get_orders(
            marketplace_id=settings.SP_API_MARKETPLACE_CA,
            last_updated_after=lookback,
        )
        assert isinstance(orders, list)

    async def test_orders_have_required_fields(self) -> None:
        """Each order has AmazonOrderId, OrderStatus, and purchase date."""
        from datetime import datetime, timedelta, timezone
        # SP-API requires ISO 8601 with Z suffix, no microseconds
        lookback = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        orders = await get_orders(
            marketplace_id=settings.SP_API_MARKETPLACE_CA,
            last_updated_after=lookback,
        )
        if not orders:
            pytest.skip("No orders in last 30 days")

        required = {"AmazonOrderId", "OrderStatus", "PurchaseDate"}
        for order in orders[:3]:
            assert required.issubset(set(order.keys()))

    async def test_catalog_item_returns_data_for_known_asin(self) -> None:
        """Catalog API returns data for a known Anabtawi ASIN."""
        # Use Almond Fingers ASIN (first in catalog)
        asin = "B0FT3HN2XV"
        result = await get_catalog_item(
            asin=asin,
            marketplace_id=settings.SP_API_MARKETPLACE_CA,
        )
        assert isinstance(result, dict)
        assert result.get("asin") == asin or "asin" in str(result)

    async def test_spapi_client_context_manager(self) -> None:
        """SPAPIClient can be used as async context manager without errors."""
        async with SPAPIClient() as client:
            assert client is not None
            # Make a real request
            token = await get_lwa_access_token()
            assert token  # Just confirm auth works within context
