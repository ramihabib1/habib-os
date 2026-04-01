"""
Full SP-API endpoint test.

Runs through every endpoint the system actually uses in production:
  1. Auth (LWA + STS) — via src.spapi modules
  2. FBA Inventory summaries
  3. Orders (last 7 days)
  4. Order items for the most recent order
  5. Catalog item (BSR + rating) for one of our ASINs

Usage (from project root, venv active):
    python scripts/test_spapi_full.py

Requires: .env with valid credentials
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Make sure src/ is importable from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.settings import settings
from src.spapi.auth import get_lwa_access_token, get_aws_credentials_sync
from src.spapi.client import SPAPIClient
from src.spapi.inventory import get_fba_inventory_summaries
from src.spapi.orders import get_orders, get_order_items
from src.spapi.catalog import get_catalog_item
from src.spapi.advertising import AdsAPIClient

DIVIDER = "=" * 60
OK  = "  \033[32m✓\033[0m"
FAIL = "  \033[31m✗\033[0m"
INFO = "  →"

# One of our real ASINs to test catalog endpoint
TEST_ASIN = "B0FT3PHRF6"  # Cashew Fingers 400g


def section(title: str) -> None:
    print(f"\n{DIVIDER}\n{title}\n{DIVIDER}")


def ok(msg: str) -> None:
    print(f"{OK} {msg}")


def fail(msg: str) -> None:
    print(f"{FAIL} {msg}")


def info(msg: str) -> None:
    print(f"{INFO} {msg}")


# ── Test 1: Auth ──────────────────────────────────────────────────────────────

async def test_auth() -> bool:
    section("Test 1: Authentication (LWA + AWS STS)")

    # LWA
    try:
        token = await get_lwa_access_token()
        ok(f"LWA access_token obtained: {token[:30]}...")
    except Exception as e:
        fail(f"LWA token exchange failed: {e}")
        return False

    # STS
    try:
        creds = get_aws_credentials_sync()
        ok(f"STS AssumeRole succeeded")
        ok(f"Temp access key: {creds.access_key[:15]}...")
        ok(f"Expires at: {datetime.fromtimestamp(creds.expires_at, tz=timezone.utc).strftime('%H:%M:%S UTC')}")
    except Exception as e:
        fail(f"STS AssumeRole failed: {e}")
        return False

    return True


# ── Test 2: FBA Inventory ─────────────────────────────────────────────────────

async def test_inventory() -> bool:
    section("Test 2: FBA Inventory Summaries")
    info(f"Marketplace: {settings.SP_API_MARKETPLACE_CA}")
    info("GET /fba/inventory/v1/summaries")

    # Catalog: SKU → ASIN mapping (from CLAUDE.md)
    catalog_sku_to_asin = {
        "3I-SHTN-9CKQ": "B0FT3HN2XV", "RL-KMFR-SEGS": "B0FT3PHRF6",
        "ZK-4NDS-MNA9": "B0FT3L774Y", "FO-SE3J-T74M": "B0FT8GSHMV",
        "5G-ZW6Q-WOZG": "B0FT3KLFHK", "KP-MEL9-XYGW": "B0FXXQHDHP",
        "Y4-Y8EE-VEOD": "B0FTM6Y263", "W3-UQRU-PGRR": "B0FXXN7HGB",
        "26-JITG-E4FU": "B0FXXM1CK8", "KL-GDUL-HEA1": "B0FTSNBX57",
        "LE-SUHY-BI89": "B0FT3DDX65", "OA-26MX-IHV0": "B0FT3DNMJR",
        "GG-0DC1-SKHG": "B0FTSQ8M46", "AN-9938-NXOT": "B0FTM1JV7N",
        "QF-3CKA-W90D": "B0FTG2FJTW", "C5-TXQU-Y67R": "B0FTM92W43",
        "VH-ZTOC-GW1Q": "B0FTM5PBZW", "BU-6GOS-GW5Q": "B0FXX2R3BD",
        "O3-V1B9-CH1H": "B0FTMBSVDN", "09-AJOP-CS83": "B0FY6PBYZS",
        "E3-DSPC-O2UN": "B0FY6MFJV5", "9J-ASSK-BVKC": "B0FY6NS7MQ",
        "EU-Z87B-ZRBZ": "B0FY6N6TRH", "H8-PWJ0-3B1Y": "B0FY6SX9RP",
        "SP-AST-500CA": "B0FY6M2LHX", "FX-M8MA-MMSA": "B0FTSM2HSJ",
        "9Z-KUHZ-FU2I": "B0FTSMTDGP", "18-116Z-1R77": "B0FXX46ST8",
        "T8-2W2X-INOK": "B0FXX3JVR5", "0C-45D7-6JUB": "B0FXX2QVF8",
    }
    catalog_asins = set(catalog_sku_to_asin.values())

    try:
        summaries = await get_fba_inventory_summaries(
            settings.SP_API_MARKETPLACE_CA,
            known_skus=list(catalog_sku_to_asin.keys()),
        )
    except Exception as e:
        fail(f"Request failed: {e}")
        return False

    ok(f"Total SKU entries returned: {len(summaries)}")

    if not summaries:
        fail("No inventory summaries returned — account may have no active FBA inventory")
        return False

    # Build API response lookup by ASIN
    api_by_asin: dict[str, dict] = {}
    for s in summaries:
        asin = s.get("asin")
        if asin:
            # Keep the one with most stock if there are multiple SKUs per ASIN
            existing = api_by_asin.get(asin)
            inv = s.get("inventoryDetails", {})
            qty = inv.get("fulfillableQuantity", 0)
            if not existing or qty > (existing.get("inventoryDetails", {}).get("fulfillableQuantity", 0)):
                api_by_asin[asin] = s

    # Show each catalog product matched by ASIN (most reliable)
    print()
    print(f"  {'SKU (catalog)':<22} {'ASIN':<14} {'SC SKU':<22} {'Fulfillable':>11} {'Inbound':>8} {'Status'}")
    print(f"  {'-'*22} {'-'*14} {'-'*22} {'-'*11} {'-'*8} {'-'*15}")

    total_fulfillable = 0
    matched = 0
    sku_match = 0
    asin_fallback = 0
    not_found = 0

    for cat_sku, asin in sorted(catalog_sku_to_asin.items(), key=lambda x: x[0]):
        # Try SKU match first
        api_entry = next((s for s in summaries if s.get("sellerSku") == cat_sku), None)
        match_type = "SKU"

        # Fallback to ASIN
        if not api_entry:
            api_entry = api_by_asin.get(asin)
            match_type = "ASIN"

        if api_entry:
            inv_details = api_entry.get("inventoryDetails", {})
            fulfillable = inv_details.get("fulfillableQuantity", 0)
            inbound = inv_details.get("inboundWorkingQuantity", 0) + inv_details.get("inboundShippedQuantity", 0)
            sc_sku = api_entry.get("sellerSku", "?")
            total_fulfillable += fulfillable
            matched += 1
            if match_type == "SKU":
                sku_match += 1
            else:
                asin_fallback += 1
            sku_mismatch_flag = " ⚠ SKU mismatch" if sc_sku != cat_sku else ""
            print(f"  {cat_sku:<22} {asin:<14} {sc_sku:<22} {fulfillable:>11} {inbound:>8}  {match_type}{sku_mismatch_flag}")
        else:
            not_found += 1
            print(f"  {cat_sku:<22} {asin:<14} {'—':<22} {'—':>11} {'—':>8}  NOT IN FBA")

    print()
    ok(f"Catalog products found in FBA: {matched}/30 ({sku_match} by SKU, {asin_fallback} by ASIN fallback)")
    ok(f"Total fulfillable units: {total_fulfillable}")
    if not_found:
        info(f"Catalog products NOT in FBA at all: {not_found}")
    if asin_fallback:
        info(f"{asin_fallback} products have SKU mismatches — inventory_sync now handles these via ASIN fallback")

    # Show anything returned by Amazon that's NOT in our catalog
    returned_asins = {s.get("asin") for s in summaries if s.get("asin")}
    unknown_asins = returned_asins - catalog_asins
    if unknown_asins:
        info(f"ASINs returned by Amazon NOT in our catalog: {unknown_asins}")
        for s in summaries:
            if s.get("asin") in unknown_asins:
                inv = s.get("inventoryDetails", {})
                print(f"      - {s.get('sellerSku')} / {s.get('asin')} — {inv.get('fulfillableQuantity',0)} units")

    return True


# ── Test 3: Orders ────────────────────────────────────────────────────────────

async def test_orders() -> tuple[bool, str | None]:
    """Returns (success, most_recent_order_id)"""
    section("Test 3: Orders (last 7 days)")

    last_updated_after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    info(f"LastUpdatedAfter: {last_updated_after}")
    info("GET /orders/v0/orders")

    try:
        orders = await get_orders(settings.SP_API_MARKETPLACE_CA, last_updated_after)
    except Exception as e:
        fail(f"Request failed: {e}")
        return False, None

    ok(f"Orders returned: {len(orders)}")

    if not orders:
        info("No orders in the last 7 days")
        return True, None

    # Show summary
    total_revenue = 0.0
    status_counts: dict[str, int] = {}

    print()
    print(f"  {'Order ID':<35} {'Status':<20} {'Amount':>10} {'Date'}")
    print(f"  {'-'*35} {'-'*20} {'-'*10} {'-'*20}")

    for o in orders[:20]:  # show max 20
        order_id   = o.get("AmazonOrderId", "?")
        status     = o.get("OrderStatus", "?")
        amount_obj = o.get("OrderTotal", {})
        amount     = float(amount_obj.get("Amount", 0))
        currency   = amount_obj.get("CurrencyCode", "CAD")
        date       = o.get("LastUpdateDate", "?")[:10]

        total_revenue += amount
        status_counts[status] = status_counts.get(status, 0) + 1

        print(f"  {order_id:<35} {status:<20} {currency} {amount:>7.2f}  {date}")

    if len(orders) > 20:
        info(f"... and {len(orders) - 20} more orders")

    print()
    ok(f"Status breakdown: {dict(sorted(status_counts.items()))}")
    ok(f"Revenue (shown orders): {total_revenue:.2f}")

    most_recent = orders[0].get("AmazonOrderId")
    return True, most_recent


# ── Test 4: Order Items ───────────────────────────────────────────────────────

async def test_order_items(order_id: str) -> bool:
    section(f"Test 4: Order Items — {order_id}")
    info("GET /orders/v0/orders/{id}/orderItems")

    try:
        items = await get_order_items(order_id)
    except Exception as e:
        fail(f"Request failed: {e}")
        return False

    ok(f"Items returned: {len(items)}")

    if items:
        print()
        for item in items:
            print(f"  • ASIN: {item.get('ASIN', '?'):<15} SKU: {item.get('SellerSKU', '?'):<20} "
                  f"Qty: {item.get('QuantityOrdered', '?'):<5} "
                  f"Title: {item.get('Title', '?')[:50]}")

    return True


# ── Test 5: Catalog ───────────────────────────────────────────────────────────

async def test_catalog() -> bool:
    section(f"Test 5: Catalog Item — {TEST_ASIN} (Cashew Fingers 400g)")
    info(f"GET /catalog/2022-04-01/items/{TEST_ASIN}")

    try:
        data = await get_catalog_item(TEST_ASIN, settings.SP_API_MARKETPLACE_CA)
    except Exception as e:
        fail(f"Request failed: {e}")
        return False

    ok("Catalog item fetched")

    # BSR
    sales_ranks = data.get("salesRanks", [])
    if sales_ranks:
        for rank_group in sales_ranks:
            ranks = rank_group.get("ranks", [])
            for r in ranks:
                print(f"  {INFO} BSR #{r.get('rank')} in {r.get('title', r.get('id', '?'))}")
    else:
        info("No salesRanks returned")

    # Rating + review count
    summaries = data.get("summaries", [])
    for summary in summaries:
        rating = summary.get("averageRating")
        count  = summary.get("ratingsTotal")
        if rating is not None:
            ok(f"Rating: {rating} stars ({count} ratings)")

    return True


# ── Test 6: Advertising API ───────────────────────────────────────────────────

async def test_advertising() -> bool:
    section("Test 6: Advertising API (Sponsored Products)")
    info("Separate LWA auth + profile-scoped requests")
    info(f"Profile ID: {settings.ADS_API_PROFILE_ID}")

    ads = AdsAPIClient()

    # Campaigns
    try:
        campaigns = await ads.list_campaigns()
        ok(f"Campaigns: {len(campaigns)} returned")
        for c in campaigns[:5]:
            state = c.get("state", "?")
            name  = c.get("name", "?")
            budget = c.get("dailyBudget", "?")
            info(f"  {name} | state={state} | dailyBudget={budget}")
        if len(campaigns) > 5:
            info(f"  ... and {len(campaigns) - 5} more")
    except Exception as e:
        fail(f"list_campaigns failed: {e}")
        return False

    if not campaigns:
        info("No campaigns found — account may have no Sponsored Products campaigns")
        return True

    # Ad groups
    try:
        ad_groups = await ads.list_ad_groups()
        ok(f"Ad groups: {len(ad_groups)} returned")
    except Exception as e:
        fail(f"list_ad_groups failed: {e}")
        return False

    # Keywords
    try:
        keywords = await ads.list_keywords()
        ok(f"Keywords: {len(keywords)} returned")
        if keywords:
            info(f"  Sample: {keywords[0].get('keywordText')} | matchType={keywords[0].get('matchType')} | bid={keywords[0].get('bid')}")
    except Exception as e:
        fail(f"list_keywords failed: {e}")
        return False

    # Keyword performance report (yesterday)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")
    info(f"Requesting keyword report for {yesterday}...")
    try:
        report_id = await ads.request_keyword_report(yesterday)
        ok(f"Report requested — ID: {report_id}")
    except Exception as e:
        fail(f"request_keyword_report failed: {e}")
        return False

    try:
        info("Polling for report completion (up to 5 min)...")
        download_url = await ads.wait_for_report(report_id)
        ok(f"Report ready")
    except Exception as e:
        fail(f"wait_for_report failed: {e}")
        return False

    try:
        rows = await ads.download_report(download_url)
        ok(f"Report downloaded — {len(rows)} keyword rows")
        if rows:
            r = rows[0]
            info(f"  Sample row: keyword={r.get('keywordText')} impressions={r.get('impressions')} clicks={r.get('clicks')} spend={r.get('cost')}")
    except Exception as e:
        fail(f"download_report failed: {e}")
        return False

    return True


# ── Main ──────────────────────────────────────────────────────────────────────

async def main() -> None:
    print(f"\n{'=' * 60}")
    print("Habib OS — Full SP-API Integration Test")
    print(f"Marketplace: {settings.SP_API_MARKETPLACE_CA} (Amazon CA)")
    print(f"{'=' * 60}")

    results: dict[str, bool] = {}

    results["auth"] = await test_auth()
    if not results["auth"]:
        print("\n⚠ Auth failed — cannot continue with API tests.\n")
        sys.exit(1)

    results["inventory"] = await test_inventory()

    ok_orders, recent_order_id = await test_orders()
    results["orders"] = ok_orders

    if recent_order_id:
        results["order_items"] = await test_order_items(recent_order_id)
    else:
        results["order_items"] = True
        info("Skipping order items test — no recent orders")

    results["catalog"] = await test_catalog()
    results["advertising"] = await test_advertising()

    # Summary
    section("Summary")
    all_passed = True
    for test, passed in results.items():
        if passed:
            ok(f"{test}")
        else:
            fail(f"{test}")
            all_passed = False

    print()
    if all_passed:
        print("  All tests passed. SP-API is fully operational.")
    else:
        print("  Some tests failed. Check the output above for details.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
