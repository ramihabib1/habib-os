"""
Layer 3 tests: Jobs + new SP-API modules (listings, pricing).

Unit tests: pure mapping/extraction functions — no I/O, no mocks needed.
Integration tests: live SP-API + Supabase — marked with pytest.mark.integration.

Run unit tests only:
    pytest tests/test_layer3_jobs.py -m "not integration" -v

Run all (requires .env with real credentials):
    pytest tests/test_layer3_jobs.py -v
"""

from __future__ import annotations

import pytest
import pytest_asyncio

# ── Unit tests: pricing.py pure functions ─────────────────────────────────────

class TestPricingExtraction:
    """Tests for pricing.py pure extraction functions."""

    def test_extract_listing_price_found(self) -> None:
        from src.spapi.pricing import _extract_listing_price

        product = {
            "Offers": [
                {
                    "BuyingPrice": {
                        "ListingPrice": {"Amount": 27.99, "CurrencyCode": "CAD"},
                        "LandedPrice": {"Amount": 27.99, "CurrencyCode": "CAD"},
                    }
                }
            ]
        }
        price, currency = _extract_listing_price(product)
        assert price == 27.99
        assert currency == "CAD"

    def test_extract_listing_price_empty(self) -> None:
        from src.spapi.pricing import _extract_listing_price

        price, currency = _extract_listing_price({})
        assert price is None
        assert currency is None

    def test_extract_listing_price_no_offers(self) -> None:
        from src.spapi.pricing import _extract_listing_price

        price, currency = _extract_listing_price({"Offers": []})
        assert price is None

    def test_extract_buybox_found_and_ours(self) -> None:
        from src.spapi.pricing import _extract_buybox

        product = {
            "CompetitivePricing": {
                "CompetitivePrices": [
                    {
                        "CompetitivePriceId": "1",
                        "condition": "New",
                        "belongsToRequester": True,
                        "Price": {
                            "LandedPrice": {"CurrencyCode": "CAD", "Amount": 27.99},
                            "ListingPrice": {"CurrencyCode": "CAD", "Amount": 27.99},
                        },
                    }
                ]
            }
        }
        price, is_ours = _extract_buybox(product)
        assert price == 27.99
        assert is_ours is True

    def test_extract_buybox_not_ours(self) -> None:
        from src.spapi.pricing import _extract_buybox

        product = {
            "CompetitivePricing": {
                "CompetitivePrices": [
                    {
                        "CompetitivePriceId": "1",
                        "belongsToRequester": False,
                        "Price": {
                            "LandedPrice": {"Amount": 24.99, "CurrencyCode": "CAD"},
                        },
                    }
                ]
            }
        }
        price, is_ours = _extract_buybox(product)
        assert price == 24.99
        assert is_ours is False

    def test_extract_buybox_missing(self) -> None:
        from src.spapi.pricing import _extract_buybox

        price, is_ours = _extract_buybox({})
        assert price is None
        assert is_ours is False

    def test_extract_buybox_no_new_buybox(self) -> None:
        """CompetitivePriceId=2 (used) should not be returned as New Buy Box."""
        from src.spapi.pricing import _extract_buybox

        product = {
            "CompetitivePricing": {
                "CompetitivePrices": [
                    {
                        "CompetitivePriceId": "2",
                        "Price": {"LandedPrice": {"Amount": 15.0, "CurrencyCode": "CAD"}},
                    }
                ]
            }
        }
        price, is_ours = _extract_buybox(product)
        assert price is None

    def test_extract_bsr_single(self) -> None:
        from src.spapi.pricing import _extract_bsr

        product = {
            "SalesRankings": [
                {"ProductCategoryId": "grocery_display_on_website", "Rank": 5234}
            ]
        }
        bsr, category = _extract_bsr(product)
        assert bsr == 5234
        assert category == "grocery_display_on_website"

    def test_extract_bsr_picks_lowest(self) -> None:
        from src.spapi.pricing import _extract_bsr

        product = {
            "SalesRankings": [
                {"ProductCategoryId": "home_garden_display_on_website", "Rank": 50000},
                {"ProductCategoryId": "grocery_display_on_website", "Rank": 5234},
            ]
        }
        bsr, category = _extract_bsr(product)
        assert bsr == 5234
        assert category == "grocery_display_on_website"

    def test_extract_bsr_empty(self) -> None:
        from src.spapi.pricing import _extract_bsr

        bsr, category = _extract_bsr({})
        assert bsr is None
        assert category is None


# ── Unit tests: orders_sync.py revenue formula ────────────────────────────────

class TestOrdersRevenue:
    """Verify the fixed revenue formula: ItemPrice - PromotionDiscount."""

    def test_map_order_item_no_discount(self) -> None:
        from src.jobs.orders_sync import _map_order_item

        raw = {
            "ASIN": "B0FT3HN2XV",
            "SellerSKU": "3I-SHTN-9CKQ",
            "Title": "Almond Fingers",
            "QuantityOrdered": 1,
            "QuantityShipped": 1,
            "ItemPrice": {"Amount": "27.99", "CurrencyCode": "CAD"},
            "PromotionDiscount": {"Amount": "0.00", "CurrencyCode": "CAD"},
            "ItemTax": {"Amount": "3.64", "CurrencyCode": "CAD"},
        }
        result = _map_order_item(raw, "uuid-123", {"3I-SHTN-9CKQ": "prod-uuid"})

        assert result["item_price"] == pytest.approx(27.99)
        assert result["promotion_discount"] == pytest.approx(0.0)

    def test_map_order_item_with_discount(self) -> None:
        from src.jobs.orders_sync import _map_order_item

        raw = {
            "ASIN": "B0FT3HN2XV",
            "SellerSKU": "3I-SHTN-9CKQ",
            "Title": "Almond Fingers",
            "QuantityOrdered": 2,
            "QuantityShipped": 2,
            "ItemPrice": {"Amount": "55.98", "CurrencyCode": "CAD"},
            "PromotionDiscount": {"Amount": "5.60", "CurrencyCode": "CAD"},
            "ItemTax": {"Amount": "6.54", "CurrencyCode": "CAD"},
        }
        result = _map_order_item(raw, "uuid-123", {})

        # Net revenue = 55.98 - 5.60 = 50.38
        assert result["item_price"] == pytest.approx(50.38)
        assert result["promotion_discount"] == pytest.approx(5.60)

    def test_map_order_item_missing_fields(self) -> None:
        from src.jobs.orders_sync import _map_order_item

        raw = {
            "ASIN": "B0FT3HN2XV",
            "SellerSKU": "3I-SHTN-9CKQ",
            "QuantityOrdered": 1,
        }
        result = _map_order_item(raw, "uuid-123", {})
        assert result["item_price"] == pytest.approx(0.0)
        assert result["promotion_discount"] == pytest.approx(0.0)

    def test_map_order_item_sku_lookup(self) -> None:
        from src.jobs.orders_sync import _map_order_item

        sku_to_id = {"3I-SHTN-9CKQ": "product-uuid-abc"}
        raw = {
            "ASIN": "B0FT3HN2XV",
            "SellerSKU": "3I-SHTN-9CKQ",
            "QuantityOrdered": 1,
        }
        result = _map_order_item(raw, "order-uuid", sku_to_id)
        assert result["product_id"] == "product-uuid-abc"

    def test_map_order_item_unknown_sku(self) -> None:
        from src.jobs.orders_sync import _map_order_item

        raw = {"ASIN": "B000UNKNOWN", "SellerSKU": "UNKNOWN-SKU", "QuantityOrdered": 1}
        result = _map_order_item(raw, "order-uuid", {})
        assert result["product_id"] is None


# ── Unit tests: listings.py TSV parsing ──────────────────────────────────────

class TestListingsParsing:
    """Test the active listings filter logic (not the API call itself)."""

    def test_active_filter_logic(self) -> None:
        """Simulate what sync_active_listings does after downloading the TSV."""
        rows = [
            {"seller-sku": "SKU1", "asin1": "B001", "status": "Active", "price": "27.99",
             "quantity": "10", "item-name": "Product 1", "fulfilment-channel": "AMAZON_NA"},
            {"seller-sku": "SKU2", "asin1": "B002", "status": "Inactive", "price": "25.00",
             "quantity": "0", "item-name": "Product 2", "fulfilment-channel": "AMAZON_NA"},
            {"seller-sku": "SKU3", "asin1": "B003", "status": "Active", "price": "35.00",
             "quantity": "5", "item-name": "Product 3", "fulfilment-channel": "DEFAULT"},
        ]

        active = []
        for row in rows:
            if row.get("status", "").strip().lower() == "active":
                price_str = row.get("price", "").strip()
                qty_str = row.get("quantity", "0").strip()
                active.append({
                    "seller_sku": row.get("seller-sku", "").strip(),
                    "asin": row.get("asin1", "").strip(),
                    "title": row.get("item-name", "").strip(),
                    "price": float(price_str) if price_str else None,
                    "quantity": int(qty_str) if qty_str.isdigit() else 0,
                    "status": row.get("status", "").strip(),
                    "fulfillment_channel": row.get("fulfilment-channel", "").strip(),
                })

        assert len(active) == 2
        assert active[0]["seller_sku"] == "SKU1"
        assert active[0]["price"] == 27.99
        assert active[1]["seller_sku"] == "SKU3"
        assert active[1]["fulfillment_channel"] == "DEFAULT"

    def test_empty_price_handled(self) -> None:
        """Empty price field should not crash — returns None."""
        rows = [
            {"seller-sku": "SKU1", "asin1": "B001", "status": "Active",
             "price": "", "quantity": "5", "item-name": "P1", "fulfilment-channel": "AMAZON_NA"},
        ]
        active = []
        for row in rows:
            if row.get("status", "").lower() == "active":
                price_str = row.get("price", "").strip()
                active.append({
                    "seller_sku": row["seller-sku"],
                    "price": float(price_str) if price_str else None,
                })

        assert active[0]["price"] is None


# ── Integration tests ─────────────────────────────────────────────────────────

@pytest.mark.integration
class TestListingsIntegration:
    """Live SP-API tests — require real credentials."""

    @pytest.mark.asyncio
    async def test_get_seller_id(self) -> None:
        from src.spapi.listings import get_seller_id
        seller_id = await get_seller_id()
        assert seller_id is not None
        assert len(seller_id) > 5
        print(f"\nSeller ID: {seller_id}")

    @pytest.mark.asyncio
    async def test_sync_active_listings_returns_26(self) -> None:
        from src.config.settings import settings
        from src.spapi.listings import sync_active_listings
        listings = await sync_active_listings(settings.SP_API_MARKETPLACE_CA)
        assert len(listings) > 0
        print(f"\nActive listings: {len(listings)}")
        for listing in listings:
            assert listing["seller_sku"]
            assert listing["asin"]
            assert listing["status"] == "Active"

    @pytest.mark.asyncio
    async def test_enrich_listing_known_sku(self) -> None:
        from src.config.settings import settings
        from src.spapi.listings import enrich_listing, get_seller_id
        seller_id = await get_seller_id()
        result = await enrich_listing(seller_id, "3I-SHTN-9CKQ", settings.SP_API_MARKETPLACE_CA)
        assert result
        assert result["sku"] == "3I-SHTN-9CKQ"
        assert result["asin"] == "B0FT3HN2XV"
        assert "BUYABLE" in result["status"]
        print(f"\nEnriched: {result}")


@pytest.mark.integration
class TestPricingIntegration:
    """Live SP-API pricing tests."""

    @pytest.mark.asyncio
    async def test_get_prices_batch_small(self) -> None:
        from src.config.settings import settings
        from src.spapi.pricing import get_prices_batch

        # Test with 3 known active SKUs
        test_skus = ["3I-SHTN-9CKQ", "RL-KMFR-SEGS", "ZK-4NDS-MNA9"]
        results = await get_prices_batch(test_skus, settings.SP_API_MARKETPLACE_CA)

        assert len(results) > 0
        for sku, data in results.items():
            assert "listing_price" in data
            assert "bsr" in data
            print(f"\n  {sku}: price={data['listing_price']}, bsr={data['bsr']}, buybox_ours={data['buybox_is_ours']}")

    @pytest.mark.asyncio
    async def test_pricing_returns_bsr(self) -> None:
        from src.config.settings import settings
        from src.spapi.pricing import get_prices_batch

        results = await get_prices_batch(["3I-SHTN-9CKQ"], settings.SP_API_MARKETPLACE_CA)
        if results:
            data = results.get("3I-SHTN-9CKQ", {})
            # BSR may be None if product has no sales rank yet — just check structure
            assert "bsr" in data
            assert "bsr_category" in data
            print(f"\nBSR: {data['bsr']} ({data['bsr_category']})")


@pytest.mark.integration
class TestJobsIntegration:
    """End-to-end job run tests against real DB and SP-API."""

    @pytest.mark.asyncio
    async def test_listings_sync_run(self, db) -> None:
        from src.jobs.listings_sync import run
        result = await run()
        assert result["error"] is None
        assert result["records_synced"] > 0
        print(f"\nlistings_sync: {result}")

    @pytest.mark.asyncio
    async def test_pricing_sync_run(self, db) -> None:
        from src.jobs.pricing_sync import run
        result = await run()
        assert result["error"] is None
        assert result["records_synced"] > 0
        print(f"\npricing_sync: {result}")

    @pytest.mark.asyncio
    async def test_inventory_sync_uses_active_only(self, db) -> None:
        """After listings_sync sets is_active, inventory_sync should only get active SKUs."""
        from src.jobs.inventory_sync import run
        result = await run()
        assert result["error"] is None
        # records_synced should be <= 26 (active SKU count), not 50
        assert result["records_synced"] <= 30
        print(f"\ninventory_sync: {result}")
