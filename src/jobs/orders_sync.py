"""
Hourly job: sync orders from SP-API → orders + order_items + sales_daily.

- Fetches orders updated in the last 2 hours (overlapping window for safety)
- Upserts into orders (ON CONFLICT amazon_order_id)
- Upserts order items
- Rebuilds sales_daily for today from order_items
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.settings import settings
from src.config.supabase_client import get_supabase
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
    orders_synced = 0
    items_synced = 0
    error: str | None = None

    try:
        db = await get_supabase()

        # Build SKU → product_id map
        products_result = await db.table("products").select("id, sku").execute()
        sku_to_id: dict[str, str] = {p["sku"]: p["id"] for p in (products_result.data or [])}

        # Fetch recent orders
        since = (datetime.now(timezone.utc) - timedelta(hours=_LOOKBACK_HOURS)).isoformat()
        raw_orders = await get_orders(settings.SP_API_MARKETPLACE_CA, since)
        logger.info("orders_sync_fetched", count=len(raw_orders))

        for raw_order in raw_orders:
            order_id = raw_order["AmazonOrderId"]
            order_row = _map_order(raw_order)

            # Upsert order
            await db.table("orders").upsert(
                order_row, on_conflict="amazon_order_id"
            ).execute()
            orders_synced += 1

            # Fetch + upsert order items (rate limited)
            try:
                raw_items = await get_order_items(order_id)
                item_rows = [_map_order_item(item, order_id, sku_to_id) for item in raw_items]
                if item_rows:
                    await db.table("order_items").upsert(
                        item_rows, on_conflict="amazon_order_id,asin"
                    ).execute()
                    items_synced += len(item_rows)
            except Exception as exc:
                logger.warning("order_items_fetch_failed", order_id=order_id, exc=str(exc))

            await asyncio.sleep(_ORDER_ITEMS_DELAY)

        # Rebuild sales_daily for today
        await _rebuild_sales_daily(db, sku_to_id)

        await _write_sync_log(db, "success", orders_synced, start)
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
            await _write_sync_log(db, "error", orders_synced, start, error)
        except Exception:
            pass
        raise

    return {
        "orders_synced": orders_synced,
        "items_synced": items_synced,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
    }


def _map_order(raw: dict) -> dict:
    return {
        "amazon_order_id": raw["AmazonOrderId"],
        "marketplace_id": raw.get("MarketplaceId"),
        "status": raw.get("OrderStatus"),
        "purchase_date": raw.get("PurchaseDate"),
        "last_update_date": raw.get("LastUpdateDate"),
        "fulfillment_channel": raw.get("FulfillmentChannel"),
        "sales_channel": raw.get("SalesChannel"),
        "order_total_amount": _parse_money(raw.get("OrderTotal")),
        "order_total_currency": _parse_currency(raw.get("OrderTotal")),
        "number_of_items_shipped": raw.get("NumberOfItemsShipped", 0),
        "number_of_items_unshipped": raw.get("NumberOfItemsUnshipped", 0),
        "is_business_order": raw.get("IsBusinessOrder", False),
        "is_prime": raw.get("IsPrime", False),
    }


def _map_order_item(raw: dict, amazon_order_id: str, sku_to_id: dict) -> dict:
    sku = raw.get("SellerSKU")
    return {
        "amazon_order_id": amazon_order_id,
        "asin": raw.get("ASIN"),
        "sku": sku,
        "product_id": sku_to_id.get(sku),
        "title": raw.get("Title"),
        "quantity_ordered": raw.get("QuantityOrdered", 0),
        "quantity_shipped": raw.get("QuantityShipped", 0),
        "item_price_amount": _parse_money(raw.get("ItemPrice")),
        "item_price_currency": _parse_currency(raw.get("ItemPrice")),
        "item_tax_amount": _parse_money(raw.get("ItemTax")),
        "promotion_discount_amount": _parse_money(raw.get("PromotionDiscount")),
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


async def _rebuild_sales_daily(db, sku_to_id: dict) -> None:
    """
    Aggregate today's shipped order items into sales_daily.
    Uses a direct SQL RPC to avoid N+1 queries.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        await db.rpc("rebuild_sales_daily", {"p_date": today}).execute()
        logger.debug("sales_daily_rebuilt", date=today)
    except Exception as exc:
        # RPC may not exist yet — fall back gracefully
        logger.warning("sales_daily_rebuild_failed", exc=str(exc))


async def _write_sync_log(db, status: str, records: int, start: float, error: str | None = None) -> None:
    await db.table("sync_log").insert({
        "type": _JOB_NAME,
        "status": status,
        "records_synced": records,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
