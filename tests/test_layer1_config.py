"""
Layer 1 — Config tests.

Tests cover:
- Settings: loads from .env, singleton, required fields, derived constants
- Supabase client: connects, queries, marketplace UUID lookup and cache
- DB helpers: write_sync_log writes and can be read back
"""

import asyncio
import time

import pytest
import pytest_asyncio

from src.config.settings import Settings, get_settings, settings
from src.config.supabase_client import (
    _marketplace_uuid_cache,
    close_supabase,
    get_marketplace_uuid,
    get_supabase,
)
from src.config.db_helpers import write_sync_log


# ─── Settings ────────────────────────────────────────────────────────────────


class TestSettings:
    def test_loads_without_error(self) -> None:
        assert isinstance(settings, Settings)

    def test_singleton(self) -> None:
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_supabase_url_is_set(self) -> None:
        assert settings.SUPABASE_URL.startswith("https://")

    def test_supabase_service_key_is_set(self) -> None:
        assert len(settings.SUPABASE_SERVICE_KEY) > 20

    def test_anthropic_key_is_set(self) -> None:
        assert settings.ANTHROPIC_API_KEY.startswith("sk-ant-")

    def test_telegram_bot_token_is_set(self) -> None:
        assert ":" in settings.TELEGRAM_BOT_TOKEN

    def test_marketplace_ca_default(self) -> None:
        assert settings.SP_API_MARKETPLACE_CA == "A2EUQ1WTGCTBG2"

    def test_marketplace_us_default(self) -> None:
        assert settings.SP_API_MARKETPLACE_US == "ATVPDKIKX0DER"

    def test_approval_expiry_hours(self) -> None:
        assert settings.APPROVAL_EXPIRY_HOURS == 24

    def test_cost_budget(self) -> None:
        assert settings.COST_BUDGET_MONTHLY_USD == 20.0

    def test_is_production_false_in_dev(self) -> None:
        assert settings.ENVIRONMENT == "development"
        assert settings.is_production is False

    def test_derived_urls(self) -> None:
        assert settings.SP_API_BASE_URL == "https://sellingpartnerapi-na.amazon.com"
        assert settings.ADS_API_BASE_URL == "https://advertising-api.amazon.com"
        assert settings.LWA_TOKEN_URL == "https://api.amazon.com/auth/o2/token"


# ─── Supabase Client ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestSupabaseClient:
    async def test_client_connects(self, db) -> None:
        """get_supabase() returns a working client."""
        assert db is not None

    async def test_client_is_singleton(self, db) -> None:
        """Two calls to get_supabase() return the same object."""
        db2 = await get_supabase()
        assert db is db2

    async def test_can_query_products_table(self, db) -> None:
        """Basic SELECT from products returns rows with expected shape."""
        result = await db.table("products").select("id, sku").limit(5).execute()
        assert len(result.data) > 0
        assert "sku" in result.data[0]
        assert "id" in result.data[0]

    async def test_products_table_has_30_skus(self, db) -> None:
        """All 30 SKUs are seeded in the products table."""
        result = await db.table("products").select("sku").execute()
        assert len(result.data) == 30

    async def test_products_have_required_fields(self, db) -> None:
        """Every product has the fields the sync jobs depend on."""
        result = await db.table("products").select("sku, asin, landed_cost, amazon_price").execute()
        for row in result.data:
            assert row["sku"], "SKU must not be empty"
            assert row["asin"], "ASIN must not be empty"
            assert row["landed_cost"] is not None
            assert row["amazon_price"] is not None

    async def test_marketplace_uuid_lookup(self, db) -> None:
        """Resolves Amazon CA marketplace code to a UUID."""
        _marketplace_uuid_cache.clear()
        uuid = await get_marketplace_uuid("A2EUQ1WTGCTBG2")
        assert len(uuid) == 36
        assert uuid.count("-") == 4

    async def test_marketplace_uuid_cache(self, db) -> None:
        """Second call returns cached result without hitting DB."""
        _marketplace_uuid_cache.clear()
        uuid1 = await get_marketplace_uuid("A2EUQ1WTGCTBG2")
        assert "A2EUQ1WTGCTBG2" in _marketplace_uuid_cache
        uuid2 = await get_marketplace_uuid("A2EUQ1WTGCTBG2")
        assert uuid1 == uuid2

    async def test_concurrent_calls_return_same_client(self) -> None:
        """Concurrent calls to get_supabase() all return the same instance (lock works)."""
        clients = await asyncio.gather(*[get_supabase() for _ in range(10)])
        assert len(set(id(c) for c in clients)) == 1

    async def test_marketplaces_table_has_ca(self, db) -> None:
        """Canada marketplace is seeded in the marketplaces table."""
        result = await (
            db.table("marketplaces")
            .select("marketplace_id, name")
            .eq("marketplace_id", "A2EUQ1WTGCTBG2")
            .execute()
        )
        assert len(result.data) == 1
        assert result.data[0]["marketplace_id"] == "A2EUQ1WTGCTBG2"


# ─── DB Helpers ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestDbHelpers:
    async def test_write_sync_log_success(self, db) -> None:
        """write_sync_log inserts a success row readable from the DB."""
        start = time.monotonic()
        await write_sync_log(
            db,
            sync_type="test_layer1",
            status="success",
            records=42,
            start_time=start,
            started_at="2026-04-01T00:00:00+00:00",
        )

        result = await (
            db.table("sync_log")
            .select("sync_type, status, records_synced, duration_ms")
            .eq("sync_type", "test_layer1")
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )

        assert len(result.data) == 1
        row = result.data[0]
        assert row["status"] == "success"
        assert row["records_synced"] == 42
        assert row["duration_ms"] >= 0

        await db.table("sync_log").delete().eq("sync_type", "test_layer1").execute()

    async def test_write_sync_log_failure(self, db) -> None:
        """write_sync_log stores error_message when status is failed."""
        start = time.monotonic()
        await write_sync_log(
            db,
            sync_type="test_layer1_err",
            status="failed",
            records=0,
            start_time=start,
            started_at="2026-04-01T00:00:00+00:00",
            error="Something went wrong",
        )

        result = await (
            db.table("sync_log")
            .select("status, error_message")
            .eq("sync_type", "test_layer1_err")
            .limit(1)
            .execute()
        )

        assert result.data[0]["status"] == "failed"
        assert result.data[0]["error_message"] == "Something went wrong"

        await db.table("sync_log").delete().eq("sync_type", "test_layer1_err").execute()

    async def test_write_sync_log_duration_positive(self, db) -> None:
        """duration_ms is always non-negative."""
        start = time.monotonic()
        await write_sync_log(
            db,
            sync_type="test_layer1_dur",
            status="success",
            records=0,
            start_time=start,
            started_at="2026-04-01T00:00:00+00:00",
        )

        result = await (
            db.table("sync_log")
            .select("duration_ms")
            .eq("sync_type", "test_layer1_dur")
            .limit(1)
            .execute()
        )

        assert result.data[0]["duration_ms"] >= 0

        await db.table("sync_log").delete().eq("sync_type", "test_layer1_dur").execute()
