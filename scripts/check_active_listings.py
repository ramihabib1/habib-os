"""
One-time diagnostic: fetch ALL active listings from Amazon CA via two methods
and compare results.

Method 1: Reports API (GET_MERCHANT_LISTINGS_ALL_DATA) — the authoritative source
Method 2: FBA Inventory API — what inventory_sync currently uses

Run:
    source .venv/bin/activate
    python scripts/check_active_listings.py
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
from datetime import datetime, timezone

from src.spapi.client import SPAPIClient
from src.config.settings import settings


MARKETPLACE_CA = settings.SP_API_MARKETPLACE_CA
SELLER_ID = "A2T2YE5V8GZQTZ"   # Habib Distribution seller ID — update if wrong


async def get_active_listings_via_report() -> list[dict]:
    """
    Use the Reports API to get all active listings.
    GET_MERCHANT_LISTINGS_ALL_DATA is the most complete source.
    Returns list of {sku, asin, status, price, title}
    """
    print("\n=== METHOD 1: Reports API (GET_MERCHANT_LISTINGS_ALL_DATA) ===")

    async with SPAPIClient(marketplace_id=MARKETPLACE_CA) as client:
        # Step 1: Request the report
        print("Requesting report...")
        resp = await client.post("/reports/2021-06-30/reports", body={
            "reportType": "GET_MERCHANT_LISTINGS_ALL_DATA",
            "marketplaceIds": [MARKETPLACE_CA],
        })
        report_id = resp.get("reportId")
        print(f"Report ID: {report_id}")

        # Step 2: Poll until done (up to 3 minutes)
        deadline = time.time() + 180
        report_doc_id = None
        while time.time() < deadline:
            status_resp = await client.get(f"/reports/2021-06-30/reports/{report_id}")
            status = status_resp.get("processingStatus")
            print(f"  Status: {status}")
            if status == "DONE":
                report_doc_id = status_resp.get("reportDocumentId")
                break
            if status in ("CANCELLED", "FATAL"):
                print(f"  Report failed: {status}")
                return []
            await asyncio.sleep(10)

        if not report_doc_id:
            print("  Timed out waiting for report")
            return []

        # Step 3: Get download URL
        doc_resp = await client.get(f"/reports/2021-06-30/documents/{report_doc_id}")
        url = doc_resp.get("url")
        compression = doc_resp.get("compressionAlgorithm")
        print(f"  Download URL obtained (compression: {compression})")

        # Step 4: Download the report (no SigV4 needed for the doc URL)
        import httpx
        async with httpx.AsyncClient(timeout=60) as http:
            download = await http.get(url)
            download.raise_for_status()
            content = download.content

        # Step 5: Decompress if needed
        if compression == "GZIP":
            import gzip
            content = gzip.decompress(content)

        # Step 6: Parse TSV
        text = content.decode("iso-8859-1")
        reader = csv.DictReader(io.StringIO(text), delimiter="\t")
        rows = list(reader)

        print(f"\nTotal rows in report: {len(rows)}")
        if rows:
            print(f"Columns: {list(rows[0].keys())[:10]}...")

        # Filter active only
        active = [r for r in rows if r.get("status", "").strip().lower() == "active"]
        print(f"Active listings: {len(active)}")

        print("\nActive SKUs:")
        for r in sorted(active, key=lambda x: x.get("seller-sku", "")):
            sku = r.get("seller-sku", "").strip()
            asin = r.get("asin1", "").strip()
            price = r.get("price", "").strip()
            status = r.get("status", "").strip()
            print(f"  {sku:<25} ASIN: {asin}  Price: ${price}  Status: {status}")

        return active


async def get_active_listings_via_fba_inventory() -> list[dict]:
    """
    What inventory_sync currently uses — FBA Inventory API.
    Only returns SKUs with stock at Amazon warehouses.
    """
    print("\n=== METHOD 2: FBA Inventory API ===")

    async with SPAPIClient(marketplace_id=MARKETPLACE_CA) as client:
        resp = await client.get("/fba/inventory/v1/summaries", params={
            "details": "true",
            "granularityType": "Marketplace",
            "granularityId": MARKETPLACE_CA,
            "marketplaceIds": MARKETPLACE_CA,
        })
        payload = resp.get("payload", {})
        summaries = payload.get("inventorySummaries", [])

        print(f"FBA inventory items returned: {len(summaries)}")
        print("\nFBA SKUs (have stock at Amazon):")
        for s in sorted(summaries, key=lambda x: x.get("sellerSku", "")):
            sku = s.get("sellerSku", "")
            asin = s.get("asin", "")
            inv = s.get("inventoryDetails", {})
            qty = inv.get("fulfillableQuantity", 0)
            print(f"  {sku:<25} ASIN: {asin}  Fulfillable: {qty}")

        return summaries


async def get_listings_via_listings_api() -> list[dict]:
    """
    Use Listings Items API to check status of each SKU individually.
    This is the most accurate method for checking if a listing is active.
    Requires the seller ID.
    """
    print(f"\n=== METHOD 3: Listings Items API (seller: {SELLER_ID}) ===")
    print("Checking listings status per SKU from DB...")

    # Import here to avoid circular deps
    from src.config.supabase_client import get_supabase
    db = await get_supabase()
    result = await db.table("products").select("sku, asin").execute()
    products = result.data or []
    print(f"Products in DB: {len(products)}")

    active_count = 0
    inactive_count = 0
    error_count = 0

    async with SPAPIClient(marketplace_id=MARKETPLACE_CA) as client:
        for p in products:
            sku = p.get("sku", "")
            if not sku:
                continue
            try:
                resp = await client.get(
                    f"/listings/2021-08-01/items/{SELLER_ID}/{sku}",
                    params={
                        "marketplaceIds": MARKETPLACE_CA,
                        "includedData": "summaries",
                    }
                )
                summaries = resp.get("summaries", [])
                status = summaries[0].get("status", ["unknown"])[0] if summaries else "unknown"
                is_active = status == "BUYABLE"
                if is_active:
                    active_count += 1
                    print(f"  ACTIVE   {sku:<25} status={status}")
                else:
                    inactive_count += 1
                    print(f"  inactive {sku:<25} status={status}")
                await asyncio.sleep(0.5)   # rate limit
            except Exception as exc:
                error_count += 1
                print(f"  ERROR    {sku:<25} {exc}")

    print(f"\nSummary: {active_count} active, {inactive_count} inactive, {error_count} errors")
    return []


async def main() -> None:
    print("=" * 60)
    print("AMAZON CA ACTIVE LISTINGS DIAGNOSTIC")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)

    # Run all three methods
    report_active = await get_active_listings_via_report()
    fba_items = await get_active_listings_via_fba_inventory()
    await get_listings_via_listings_api()

    # Summary comparison
    print("\n" + "=" * 60)
    print("COMPARISON SUMMARY")
    print("=" * 60)
    print(f"Reports API (active listings): {len(report_active)}")
    print(f"FBA Inventory API (with stock): {len(fba_items)}")

    if report_active:
        report_skus = {r.get("seller-sku", "").strip() for r in report_active}
        fba_skus = {s.get("sellerSku", "") for s in fba_items}
        in_report_not_fba = report_skus - fba_skus
        if in_report_not_fba:
            print(f"\nActive on Amazon but NO FBA stock ({len(in_report_not_fba)}):")
            for sku in sorted(in_report_not_fba):
                print(f"  {sku}")


if __name__ == "__main__":
    asyncio.run(main())
