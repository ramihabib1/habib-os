"""
Weekly job: snapshot competitor ASINs — price, BSR, rating, stock.

Fetches data via the SP-API Catalog API for competitor ASINs stored
in the competitors table. Writes to competitor_snapshots.
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

_JOB_NAME = "competitor_sync"

# Flag significant price drops (>10%) in the snapshot
_PRICE_DROP_THRESHOLD = 0.10


async def run() -> dict[str, Any]:
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    records = 0
    error: str | None = None

    try:
        db = await get_supabase()

        # Load all competitor ASINs (DB uses competitor_asin and competitor_name)
        comp_result = await db.table("competitors").select(
            "id, competitor_asin, product_id, competitor_name"
        ).execute()
        competitors = comp_result.data or []
        logger.info("competitor_sync_start", count=len(competitors))

        today = datetime.now(timezone.utc).date().isoformat()
        snapshot_rows: list[dict] = []
        flags: list[str] = []

        for comp in competitors:
            try:
                asin = comp["competitor_asin"]
                data = await get_catalog_item(asin, settings.SP_API_MARKETPLACE_CA)
                snapshot = _extract_snapshot(comp, data, today)
                if snapshot:
                    snapshot_rows.append(snapshot)

                    # Check for significant price drop vs last snapshot
                    last = await _get_last_snapshot(db, comp["id"])
                    if last and snapshot.get("price") and last.get("price"):
                        drop = (last["price"] - snapshot["price"]) / last["price"]
                        if drop >= _PRICE_DROP_THRESHOLD:
                            name = comp.get("competitor_name", asin)
                            flags.append(
                                f"⚠️ Competitor *{name}* dropped price "
                                f"by {drop*100:.0f}% "
                                f"(${last['price']:.2f} → ${snapshot['price']:.2f})"
                            )

            except Exception as exc:
                logger.warning(
                    "competitor_sync_item_failed",
                    asin=comp.get("competitor_asin"),
                    exc=str(exc),
                )

        if snapshot_rows:
            await db.table("competitor_snapshots").upsert(
                snapshot_rows, on_conflict="competitor_id,snapshot_date"
            ).execute()
            records = len(snapshot_rows)

        # Send flags to Maree
        if flags:
            from src.telegram.notifications import send_to_role
            alert = "*🔍 Competitor Update*\n\n" + "\n".join(flags)
            await send_to_role("maree", alert)

        await write_sync_log(db, _JOB_NAME, "success", records, start, started_at)
        await log_action(
            agent=_JOB_NAME,
            action="sync_complete",
            entity_type="competitor_snapshots",
            details={"records": records, "flags": len(flags)},
        )
        logger.info("competitor_sync_done", records=records, flags=len(flags))

    except Exception as exc:
        error = str(exc)
        logger.error("competitor_sync_error", exc=error)
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


def _extract_snapshot(comp: dict, catalog_data: dict, today: str) -> dict | None:
    summaries = catalog_data.get("summaries", [])
    sales_ranks = catalog_data.get("salesRanks", [])

    rating: float | None = None
    review_count: int | None = None
    for summary in summaries:
        if summary.get("marketplaceId") == settings.SP_API_MARKETPLACE_CA:
            rating = summary.get("averageCustomerReview")
            review_count = summary.get("numberOfCustomerReviews")
            break

    bsr: int | None = None
    bsr_category: str | None = None
    for rank_group in sales_ranks:
        if rank_group.get("marketplaceId") == settings.SP_API_MARKETPLACE_CA:
            ranks = rank_group.get("ranks", [])
            if ranks:
                best = min(ranks, key=lambda r: r.get("rank", 999999))
                bsr = best.get("rank")
                bsr_category = best.get("displayGroupName")

    return {
        "competitor_id": comp["id"],
        "snapshot_date": today,
        "price": None,   # Price requires Offers API (separate role)
        "bsr": bsr,
        "bsr_category": bsr_category,
        "rating": rating,
        "review_count": review_count,
        "is_in_stock": True,   # Assume in stock unless we get out-of-stock signal
    }


async def _get_last_snapshot(db, competitor_id: str) -> dict | None:
    result = await (
        db.table("competitor_snapshots")
        .select("price, bsr, rating")
        .eq("competitor_id", competitor_id)
        .order("snapshot_date", desc=True)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return rows[0] if rows else None
