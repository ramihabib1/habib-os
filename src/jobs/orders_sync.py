"""
Hourly job: sync orders from SP-API → orders + order_items + sales_daily.

- Fetches orders updated in the last 2 hours (overlapping window for safety)
- Upserts into orders (ON CONFLICT amazon_order_id)
- Upserts order items (ON CONFLICT order_id, asin) using DB UUID FK
- Rebuilds sales_daily for today from order_items via RPC
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.db_helpers import write_sync_log
from src.config.settings import settings
from src.config.supabase_client import get_marketplace_uuid, get_supabase
from src.spapi.orders import get_order_items, get_orders
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "orders_sync"
# Fetch window: 2 hours back to handle clock skew and retries
_LOOKBACK_HOURS = 2
# SP-API rate limit: ~1 req/sec for getOrderItems
_ORDER_ITEMS_DELAY = 1.1


async def run() -> dict[str, Any]:
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    orders_synced = 0
    items_synced = 0
    error: str | None = None

    try:
        db = await get_supabase()
        marketplace_uuid = await get_marketplace_uuid(settings.SP_API_MARKETPLACE_CA)

        # Build SKU → product_id map
        products_result = await db.table("products").select("id, sku").execute()
        sku_to_id: dict[str, str] = {p["sku"]: p["id"] for p in (products_result.data or [])}

        # Fetch recent orders — Amazon requires ISO 8601 without microseconds
        since_dt = datetime.now(timezone.utc) - timedelta(hours=_LOOKBACK_HOURS)
        since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        raw_orders = await get_orders(settings.SP_API_MARKETPLACE_CA, since)
        logger.info("orders_sync_fetched", count=len(raw_orders))

        for raw_order in raw_orders:
            amazon_order_id = raw_order["AmazonOrderId"]
            order_row = _map_order(raw_order, marketplace_uuid)

            # Upsert order — returns the row including its DB UUID
            result = await db.table("orders").upsert(
                order_row, on_conflict="amazon_order_id"
            ).execute()
            orders_synced += 1

            # Get DB UUID for this order (needed for order_items FK)
            order_uuid: str | None = None
            if result.data:
                order_uuid = result.data[0].get("id")
            if not order_uuid:
                # Fallback: look it up
                lookup = await (
                    db.table("orders")
                    .select("id")
                    .eq("amazon_order_id", amazon_order_id)
                    .single()
                    .execute()
                )
                order_uuid = lookup.data.get("id") if lookup.data else None

            if not order_uuid:
                logger.warning("orders_sync_no_uuid", amazon_order_id=amazon_order_id)
                await asyncio.sleep(_ORDER_ITEMS_DELAY)
                continue

            # Fetch + upsert order items
            try:
                raw_items = await get_order_items(amazon_order_id)
                item_rows = [
                    _map_order_item(item, order_uuid, sku_to_id)
                    for item in raw_items
                ]
                if item_rows:
                    await db.table("order_items").upsert(
                        item_rows, on_conflict="order_id,asin"
                    ).execute()
                    items_synced += len(item_rows)
            except Exception as exc:
                logger.warning("order_items_fetch_failed", order_id=amazon_order_id, exc=str(exc))

            await asyncio.sleep(_ORDER_ITEMS_DELAY)

        # Rebuild sales_daily for today
        await _rebuild_sales_daily(db)

        await write_sync_log(db, _JOB_NAME, "success", orders_synced, start, started_at)
        await log_action(
            agent=_JOB_NAME,
            action="sync_complete",
            entity_type="orders",
            details={"orders": orders_synced, "items": items_synced},
        )
        logger.info("orders_sync_done", orders=orders_synced, items=items_synced)

    except Exception as exc:
        error = str(exc)
        logger.error("orders_sync_error", exc=error)
        try:
            db = await get_supabase()
            await write_sync_log(db, _JOB_NAME, "failed", orders_synced, start, started_at, error)
        except Exception:
            pass
        raise

    return {
        "orders_synced": orders_synced,
        "items_synced": items_synced,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
    }


def _map_order(raw: dict, marketplace_uuid: str) -> dict:
    return {
        "amazon_order_id": raw["AmazonOrderId"],
        "marketplace_id": marketplace_uuid,
        "order_status": raw.get("OrderStatus"),
        "purchase_date": raw.get("PurchaseDate"),
        "last_update_date": raw.get("LastUpdateDate"),
        "fulfillment_channel": raw.get("FulfillmentChannel"),
        "sales_channel": raw.get("SalesChannel"),
        "order_total": _parse_money(raw.get("OrderTotal")),
        "currency": _parse_currency(raw.get("OrderTotal")),
        "number_of_items_shipped": raw.get("NumberOfItemsShipped", 0),
        "number_of_items_unshipped": raw.get("NumberOfItemsUnshipped", 0),
        "is_business_order": raw.get("IsBusinessOrder", False),
        "is_prime": raw.get("IsPrime", False),
    }


def _map_order_item(raw: dict, order_uuid: str, sku_to_id: dict) -> dict:
    sku = raw.get("SellerSKU")
    # item_price stores net revenue: ItemPrice - PromotionDiscount
    # Per SP-API research: ItemPrice.Amount - PromotionDiscount.Amount is the
    # correct per-SKU revenue figure. OrderTotal includes shipping/tax and
    # cannot be broken down per SKU.
    gross_price = _parse_money(raw.get("ItemPrice")) or 0.0
    promotion_discount = _parse_money(raw.get("PromotionDiscount")) or 0.0
    net_item_price = gross_price - promotion_discount

    return {
        "order_id": order_uuid,
        "asin": raw.get("ASIN"),
        "sku": sku,
        "product_id": sku_to_id.get(sku),
        "title": raw.get("Title"),
        "qty_ordered": raw.get("QuantityOrdered", 0),
        "qty_shipped": raw.get("QuantityShipped", 0),
        "item_price": net_item_price,
        "item_tax": _parse_money(raw.get("ItemTax")),
        "promotion_discount": promotion_discount,
    }


def _parse_money(money: dict | None) -> float | None:
    if not money:
        return None
    try:
        return float(money.get("Amount", 0))
    except (TypeError, ValueError):
        return None


def _parse_currency(money: dict | None) -> str | None:
    if not money:
        return None
    return money.get("CurrencyCode")


async def _rebuild_sales_daily(db) -> None:
    """Aggregate today's shipped order items into sales_daily via RPC."""
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        await db.rpc("rebuild_sales_daily", {"p_date": today}).execute()
        logger.debug("sales_daily_rebuilt", date=today)
    except Exception as exc:
        logger.warning("sales_daily_rebuild_failed", exc=str(exc))
