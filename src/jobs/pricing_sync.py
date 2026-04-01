"""
Every-4-hour job: sync listing prices + BSR for all active SKUs.

Uses getPricing v0 which returns both price AND BSR (SalesRankings) in a
single call — no need for a separate Catalog API call for BSR.

Rate limit: 0.5 req/s, burst 1, max 20 SKUs per call.
For 26 active SKUs: 2 API calls, ~4 seconds total.

Writes:
  - products.amazon_price (current listing price)
  - product_snapshots (bsr, bsr_category, price, snapshot_date=today)
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from src.config.db_helpers import write_sync_log
from src.config.settings import settings
from src.config.supabase_client import get_supabase
from src.spapi.pricing import get_prices_batch
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "pricing_sync"


async def run() -> dict[str, Any]:
    """
    Sync prices + BSR for all active SKUs.
    Returns summary with records_synced, price_updates, bsr_updates.
    """
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    records_synced = 0
    error: str | None = None

    try:
        db = await get_supabase()
        marketplace_id = settings.SP_API_MARKETPLACE_CA
        today = datetime.now(timezone.utc).date().isoformat()

        # ── 1. Load active SKUs ──────────────────────────────────────────────
        result = await db.table("products").select("id, sku, asin").eq("is_active", True).execute()
        products = result.data or []

        if not products:
            logger.warning("pricing_sync_no_active_products")
            await write_sync_log(db, _JOB_NAME, "success", 0, start, started_at)
            return {"records_synced": 0, "duration_seconds": 0, "error": None}

        skus = [p["sku"] for p in products]
        sku_to_product = {p["sku"]: p for p in products}
        logger.info("pricing_sync_start", active_skus=len(skus))

        # ── 2. Fetch prices + BSR from Amazon ────────────────────────────────
        pricing_data = await get_prices_batch(skus, marketplace_id)
        logger.info("pricing_sync_fetched", returned=len(pricing_data))

        # ── 3. Update products.amazon_price ─────────────────────────────────
        price_update_count = 0
        for sku, data in pricing_data.items():
            if data.get("listing_price") is not None:
                await db.table("products").update({
                    "amazon_price": data["listing_price"],
                }).eq("sku", sku).execute()
                price_update_count += 1

        logger.info("pricing_sync_prices_updated", count=price_update_count)

        # ── 4. Upsert product_snapshots with BSR ─────────────────────────────
        snapshot_rows: list[dict] = []
        for sku, data in pricing_data.items():
            product = sku_to_product.get(sku)
            if not product:
                continue

            bsr = data.get("bsr")
            listing_price = data.get("listing_price")

            # Only write snapshot if we have at least BSR or price
            if bsr is None and listing_price is None:
                continue

            snapshot_rows.append({
                "product_id": product["id"],
                "snapshot_date": today,
                "bsr": bsr,
                "bsr_category": data.get("bsr_category"),
                "price": listing_price,
            })

        if snapshot_rows:
            await db.table("product_snapshots").upsert(
                snapshot_rows, on_conflict="product_id,snapshot_date"
            ).execute()

        records_synced = len(pricing_data)

        await write_sync_log(db, _JOB_NAME, "success", records_synced, start, started_at)
        await log_action(
            agent=_JOB_NAME,
            action="sync_complete",
            entity_type="product_snapshots",
            details={
                "prices_updated": price_update_count,
                "bsr_snapshots": len(snapshot_rows),
            },
        )
        logger.info(
            "pricing_sync_done",
            prices=price_update_count,
            bsr_snapshots=len(snapshot_rows),
        )

    except Exception as exc:
        error = str(exc)
        logger.error("pricing_sync_error", exc=error)
        try:
            db = await get_supabase()
            await write_sync_log(db, _JOB_NAME, "failed", records_synced, start, started_at, error)
        except Exception:
            pass
        raise

    return {
        "records_synced": records_synced,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
    }
