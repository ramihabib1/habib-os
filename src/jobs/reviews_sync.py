"""
Daily job: sync product rating + review count via SP-API Catalog Items API.

Note: BSR is now synced by pricing_sync (every 4h) via the getPricing API
which returns SalesRankings for free alongside prices. This job only
syncs rating and review_count from the Catalog Items API.

SP-API does not provide individual review text — only rating + count.
Full review mining requires the Product Reviews API (separate approval).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from src.config.db_helpers import write_sync_log
from src.config.settings import settings
from src.config.supabase_client import get_supabase
from src.spapi.catalog import get_catalog_item
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "reviews_sync"


async def run() -> dict[str, Any]:
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    records = 0
    error: str | None = None

    try:
        db = await get_supabase()

        # Fetch all active products with their ASINs
        products_result = await db.table("products").select("id, sku, asin, amazon_price").execute()
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

        await write_sync_log(db, _JOB_NAME, "success", records, start, started_at)
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
            await write_sync_log(db, _JOB_NAME, "failed", records, start, started_at, error)
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

    if rating is None and review_count is None:
        return None

    today = datetime.now(timezone.utc).date().isoformat()
    # BSR and price are written by pricing_sync (every 4h via getPricing).
    # This job only updates rating and review_count — use upsert so that
    # pricing_sync's bsr/price values are preserved.
    return {
        "product_id": product["id"],
        "snapshot_date": today,
        "rating": rating,
        "review_count": review_count,
    }
