"""
Every-6-hour job: sync active listings from Amazon → products table.

Amazon is the source of truth. This job:
  1. Fetches GET_MERCHANT_LISTINGS_ALL_DATA report (all active SKUs)
  2. Upserts products table — sets is_active, amazon_price, asin
  3. Auto-inserts unknown SKUs (source=amazon_discovered)
  4. Marks SKUs missing from report as is_active=false
  5. Enriches each active SKU with getListingsItem (fnsku, status)

This runs BEFORE inventory_sync so that inventory_sync always has
an accurate is_active=true set to query against.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from src.config.db_helpers import write_sync_log
from src.config.settings import settings
from src.config.supabase_client import get_supabase
from src.spapi.listings import enrich_listings_batch, get_seller_id, sync_active_listings
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "listings_sync"


async def run() -> dict[str, Any]:
    """
    Sync active listings from Amazon to the products table.
    Returns summary with records_synced, new_discovered, marked_inactive.
    """
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    records_synced = 0
    new_discovered = 0
    marked_inactive = 0
    error: str | None = None

    try:
        db = await get_supabase()
        seller_id = await get_seller_id()
        marketplace_id = settings.SP_API_MARKETPLACE_CA
        now_iso = datetime.now(timezone.utc).isoformat()

        # ── 1. Fetch active listings from Amazon ────────────────────────────
        amazon_listings = await sync_active_listings(marketplace_id)
        amazon_skus: set[str] = {row["seller_sku"] for row in amazon_listings}
        amazon_by_sku: dict[str, dict] = {row["seller_sku"]: row for row in amazon_listings}

        # ── 2. Load current products from DB ────────────────────────────────
        db_result = await db.table("products").select("id, sku, asin, is_active").execute()
        db_products = db_result.data or []
        db_by_sku: dict[str, dict] = {p["sku"]: p for p in db_products}
        db_skus: set[str] = set(db_by_sku.keys())


        # ── 3. Mark SKUs no longer in Amazon as inactive ────────────────────
        now_active_in_db = {p["sku"] for p in db_products if p.get("is_active")}
        went_inactive = now_active_in_db - amazon_skus
        if went_inactive:
            await db.table("products").update({
                "is_active": False,
            }).in_("sku", list(went_inactive)).execute()
            marked_inactive = len(went_inactive)
            logger.info("listings_sync_marked_inactive", count=marked_inactive, skus=list(went_inactive))

        # ── 4. Upsert active listings — update existing, insert new ─────────
        for listing in amazon_listings:
            sku = listing["seller_sku"]
            update_data: dict = {
                "is_active": True,
                "last_seen_active": now_iso,
            }
            if listing.get("asin"):
                update_data["asin"] = listing["asin"]
            if listing.get("price") is not None:
                update_data["amazon_price"] = listing["price"]

            if sku in db_skus:
                # Existing product — update status fields only
                await db.table("products").update(update_data).eq("sku", sku).execute()
            else:
                # SKU active on Amazon but not in our products table.
                # Do NOT auto-insert — products require business data (landed_cost,
                # fees, etc.) that can only be set manually by Rami.
                # Log for visibility; Rami can add the product manually.
                new_discovered += 1
                logger.warning(
                    "listings_sync_unknown_sku",
                    sku=sku,
                    asin=listing.get("asin"),
                    title=listing.get("title"),
                    price=listing.get("price"),
                    action="manual_add_required",
                )

            records_synced += 1

        # ── 5. Enrich active SKUs with getListingsItem ───────────────────────
        active_skus = list(amazon_skus)
        logger.info("listings_sync_enriching", count=len(active_skus))
        enriched = await enrich_listings_batch(seller_id, active_skus, marketplace_id)

        for sku, data in enriched.items():
            enrich_update: dict = {}
            if data.get("fnsku"):
                enrich_update["fnsku"] = data["fnsku"]
            if enrich_update:
                await db.table("products").update(enrich_update).eq("sku", sku).execute()

        logger.info("listings_sync_enriched", count=len(enriched))

        await write_sync_log(db, _JOB_NAME, "success", records_synced, start, started_at)
        await log_action(
            agent=_JOB_NAME,
            action="sync_complete",
            entity_type="products",
            details={
                "active": len(amazon_skus),
                "new_discovered": new_discovered,
                "marked_inactive": marked_inactive,
            },
        )
        logger.info(
            "listings_sync_done",
            active=len(amazon_skus),
            new=new_discovered,
            inactive=marked_inactive,
        )

    except Exception as exc:
        error = str(exc)
        logger.error("listings_sync_error", exc=error)
        try:
            db = await get_supabase()
            await write_sync_log(db, _JOB_NAME, "failed", records_synced, start, started_at, error)
        except Exception:
            pass
        raise

    return {
        "records_synced": records_synced,
        "new_discovered": new_discovered,
        "marked_inactive": marked_inactive,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
    }
