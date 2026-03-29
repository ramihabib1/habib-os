"""
Daily job: sync product snapshots (BSR, rating) and reviews via SP-API catalog.

Note: SP-API does not provide individual review text — only rating + count.
Full review mining requires the Product Reviews API (separate approval process).
For now this syncs BSR and star rating into product_snapshots.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from src.config.settings import settings
from src.config.supabase_client import get_supabase
from src.spapi.catalog import get_catalog_item
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "reviews_sync"


async def run() -> dict[str, Any]:
    start = time.monotonic()
    records = 0
    error: str | None = None

    try:
        db = await get_supabase()

        # Fetch all active products with their ASINs
        products_result = await db.table("products").select("id, sku, asin").execute()
        products = [p for p in (products_result.data or []) if p.get("asin")]
        logger.info("reviews_sync_products", count=len(products))

        snapshot_rows: list[dict] = []

        for product in products:
            try:
                data = await get_catalog_item(product["asin"], settings.SP_API_MARKETPLACE_CA)
                snapshot = _extract_snapshot(product, data)
                if snapshot:
                    snapshot_rows.append(snapshot)
            except Exception as exc:
                logger.warning(
                    "reviews_sync_item_failed",
                    asin=product["asin"],
                    exc=str(exc),
                )

        if snapshot_rows:
            await db.table("product_snapshots").upsert(
                snapshot_rows, on_conflict="product_id,snapshot_date"
            ).execute()
            records = len(snapshot_rows)

        await _write_sync_log(db, "success", records, start)
        await log_action(
            agent=_JOB_NAME,
            action="sync_complete",
            entity_type="product_snapshots",
            details={"records": records},
        )
        logger.info("reviews_sync_done", records=records)

    except Exception as exc:
        error = str(exc)
        logger.error("reviews_sync_error", exc=error)
        try:
            db = await get_supabase()
            await _write_sync_log(db, "error", records, start, error)
        except Exception:
            pass
        raise

    return {
        "records_synced": records,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
    }


def _extract_snapshot(product: dict, catalog_data: dict) -> dict | None:
    """Parse SP-API catalog item response into a product_snapshots row."""
    summaries = catalog_data.get("summaries", [])
    sales_ranks = catalog_data.get("salesRanks", [])

    # Extract rating and review count from summaries
    rating: float | None = None
    review_count: int | None = None
    for summary in summaries:
        if summary.get("marketplaceId") == settings.SP_API_MARKETPLACE_CA:
            rating = summary.get("averageCustomerReview")
            review_count = summary.get("numberOfCustomerReviews")
            break

    # Extract BSR (lowest rank across all categories)
    bsr: int | None = None
    bsr_category: str | None = None
    for rank_group in sales_ranks:
        if rank_group.get("marketplaceId") == settings.SP_API_MARKETPLACE_CA:
            for rank in rank_group.get("ranks", []):
                if bsr is None or rank.get("rank", 999999) < bsr:
                    bsr = rank.get("rank")
                    bsr_category = rank.get("displayGroupName")

    if rating is None and bsr is None:
        return None

    today = datetime.now(timezone.utc).date().isoformat()
    return {
        "product_id": product["id"],
        "snapshot_date": today,
        "bsr": bsr,
        "bsr_category": bsr_category,
        "rating": rating,
        "review_count": review_count,
        "price": product.get("amazon_price"),
    }


async def _write_sync_log(db, status: str, records: int, start: float, error: str | None = None) -> None:
    await db.table("sync_log").insert({
        "type": _JOB_NAME,
        "status": status,
        "records_synced": records,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
