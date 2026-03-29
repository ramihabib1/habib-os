"""
Hourly job: sync FBA inventory from SP-API → inventory_snapshots table.

The DB trigger check_fba_inventory_threshold fires automatically after inserts
and will create approval_requests for low-stock SKUs.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from src.config.settings import settings
from src.config.supabase_client import get_supabase
from src.spapi.inventory import get_fba_inventory_summaries
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "inventory_sync"


async def run() -> dict[str, Any]:
    """
    Sync FBA inventory for the CA marketplace.

    Returns a summary dict with records_synced, skipped, duration_seconds.
    """
    start = time.monotonic()
    records_synced = 0
    skipped = 0
    error: str | None = None

    try:
        db = await get_supabase()

        # Fetch all products to build SKU → product_id map
        products_result = await db.table("products").select("id, sku").execute()
        sku_to_id: dict[str, str] = {p["sku"]: p["id"] for p in (products_result.data or [])}

        if not sku_to_id:
            logger.warning("inventory_sync_no_products")
            return _finish(start, 0, 0, "no products in DB")

        # Fetch FBA inventory from SP-API
        summaries = await get_fba_inventory_summaries(settings.SP_API_MARKETPLACE_CA)
        logger.info("inventory_sync_fetched", count=len(summaries))

        snapshot_time = datetime.now(timezone.utc).isoformat()
        rows_to_insert: list[dict] = []

        for summary in summaries:
            sku = summary.get("sellerSku")
            product_id = sku_to_id.get(sku)

            if not product_id:
                logger.debug("inventory_sync_unknown_sku", sku=sku)
                skipped += 1
                continue

            inv_details = summary.get("inventoryDetails", {})
            rows_to_insert.append({
                "product_id": product_id,
                "marketplace_id": settings.SP_API_MARKETPLACE_CA,
                "snapshot_at": snapshot_time,
                "fulfillable_qty": inv_details.get("fulfillableQuantity", 0),
                "inbound_working_qty": inv_details.get("inboundWorkingQuantity", 0),
                "inbound_shipped_qty": inv_details.get("inboundShippedQuantity", 0),
                "inbound_receiving_qty": inv_details.get("inboundReceivingQuantity", 0),
                "reserved_fc_transfers": (
                    inv_details.get("reservedQuantity", {}).get("fcProcessingQuantity", 0)
                ),
                "reserved_fc_processing": (
                    inv_details.get("reservedQuantity", {}).get("fcProcessingQuantity", 0)
                ),
                "unfulfillable_qty": inv_details.get("unfulfillableQuantity", {}).get("totalUnfulfillableQuantity", 0),
                "researching_qty": 0,
                "total_qty": summary.get("totalQuantity", 0),
            })

        if rows_to_insert:
            await db.table("inventory_snapshots").insert(rows_to_insert).execute()
            records_synced = len(rows_to_insert)

        # Write to sync_log
        await _write_sync_log(db, "success", records_synced, skipped, start)
        await log_action(
            agent=_JOB_NAME,
            action="sync_complete",
            entity_type="inventory_snapshots",
            details={"records": records_synced, "skipped": skipped},
        )

        logger.info("inventory_sync_done", records=records_synced, skipped=skipped)

    except Exception as exc:
        error = str(exc)
        logger.error("inventory_sync_error", exc=error)
        try:
            db = await get_supabase()
            await _write_sync_log(db, "error", records_synced, skipped, start, error)
        except Exception:
            pass
        raise

    return _finish(start, records_synced, skipped, error)


def _finish(
    start: float,
    records_synced: int,
    skipped: int,
    error: str | None,
) -> dict[str, Any]:
    return {
        "records_synced": records_synced,
        "skipped": skipped,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
    }


async def _write_sync_log(
    db,
    status: str,
    records: int,
    skipped: int,
    start: float,
    error: str | None = None,
) -> None:
    await db.table("sync_log").insert({
        "type": _JOB_NAME,
        "status": status,
        "records_synced": records,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "details": {"skipped": skipped},
    }).execute()
