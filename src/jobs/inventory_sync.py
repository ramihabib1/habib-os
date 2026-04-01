"""
Hourly job: sync FBA inventory from SP-API → inventory_snapshots table.

The DB trigger check_fba_inventory_threshold fires automatically after inserts
and will create approval_requests for low-stock SKUs.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from src.config.db_helpers import write_sync_log
from src.config.settings import settings
from src.config.supabase_client import get_marketplace_uuid, get_supabase
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
    started_at = datetime.now(timezone.utc).isoformat()
    records_synced = 0
    skipped = 0
    error: str | None = None

    try:
        db = await get_supabase()
        marketplace_uuid = await get_marketplace_uuid(settings.SP_API_MARKETPLACE_CA)

        # Only query ACTIVE SKUs — is_active=true set by listings_sync.
        # Querying all 30 DB SKUs (some inactive) + the old full marketplace
        # scan returned 50 ghost SKUs. Targeted query for active SKUs only.
        products_result = await db.table("products").select("id, sku, asin").eq("is_active", True).execute()
        products_data = products_result.data or []
        sku_to_id: dict[str, str] = {p["sku"]: p["id"] for p in products_data}
        asin_to_id: dict[str, str] = {
            p["asin"]: p["id"] for p in products_data if p.get("asin")
        }

        if not sku_to_id:
            logger.warning("inventory_sync_no_active_products")
            return _finish(start, 0, 0, "no active products in DB")

        # Targeted query only — no full marketplace scan.
        # See src/spapi/inventory.py for why Phase 2 was removed.
        known_skus = list(sku_to_id.keys())
        summaries = await get_fba_inventory_summaries(
            settings.SP_API_MARKETPLACE_CA,
            known_skus=known_skus,
        )
        logger.info("inventory_sync_fetched", count=len(summaries))

        snapshot_time = datetime.now(timezone.utc).isoformat()
        rows_to_insert: list[dict] = []

        for summary in summaries:
            sku = summary.get("sellerSku")
            asin = summary.get("asin")
            product_id = sku_to_id.get(sku)

            # Fallback: match by ASIN if SKU doesn't match
            if not product_id and asin:
                product_id = asin_to_id.get(asin)
                if product_id:
                    logger.debug("inventory_sync_asin_fallback", sku=sku, asin=asin)

            if not product_id:
                logger.debug("inventory_sync_unknown_sku", sku=sku, asin=asin)
                skipped += 1
                continue

            inv_details = summary.get("inventoryDetails", {})
            reserved = inv_details.get("reservedQuantity", {})
            rows_to_insert.append({
                "product_id": product_id,
                "marketplace_id": marketplace_uuid,
                "snapshot_at": snapshot_time,
                "fulfillable_qty": inv_details.get("fulfillableQuantity", 0),
                "inbound_working_qty": inv_details.get("inboundWorkingQuantity", 0),
                "inbound_shipped_qty": inv_details.get("inboundShippedQuantity", 0),
                "inbound_receiving_qty": inv_details.get("inboundReceivingQuantity", 0),
                "reserved_fc_transfers": reserved.get("pendingTransshipmentQuantity", 0),
                "reserved_fc_processing": reserved.get("fcProcessingQuantity", 0),
                "unfulfillable_qty": inv_details.get("unfulfillableQuantity", {}).get("totalUnfulfillableQuantity", 0),
                "researching_qty": inv_details.get("researchingQuantity", {}).get("totalResearchingQuantity", 0),
                # total_qty is a generated column — computed automatically by the DB
            })

        if rows_to_insert:
            await db.table("inventory_snapshots").insert(rows_to_insert).execute()
            records_synced = len(rows_to_insert)

        await write_sync_log(db, _JOB_NAME, "success", records_synced, start, started_at)
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
            await write_sync_log(db, _JOB_NAME, "failed", records_synced, start, started_at, error)
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
