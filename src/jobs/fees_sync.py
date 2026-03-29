"""
Daily job: calculate fees_daily and profit_daily from known fee structures.

Sources:
  - products table: landed_cost, amazon_price, referral_fee_pct, fba_fulfillment_fee
  - sales_daily: units sold and revenue per SKU per day
  - ppc_campaign_stats_daily: ad spend per day (rolled up to product level)

Writes:
  - fees_daily: fee breakdown per SKU per day
  - profit_daily: revenue - COGS - fees per SKU per day
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from src.config.supabase_client import get_supabase
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "fees_sync"

# Amazon CA fee constants (approximate — adjust from Seller Central reports)
_REFERRAL_FEE_PCT_DEFAULT = Decimal("0.15")   # 15% referral fee
_CLOSING_FEE_DEFAULT = Decimal("0.00")         # No closing fee for grocery


async def run() -> dict[str, Any]:
    """Calculate and upsert fees_daily + profit_daily for yesterday."""
    start = time.monotonic()
    records = 0
    error: str | None = None

    try:
        db = await get_supabase()
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

        # Load all products with fee structure
        products_result = await db.table("products").select(
            "id, sku, landed_cost, amazon_price, "
            "referral_fee_pct, fba_fulfillment_fee"
        ).execute()
        products = {p["id"]: p for p in (products_result.data or [])}

        # Load yesterday's sales
        sales_result = await db.table("sales_daily").select(
            "product_id, units_sold, revenue, refund_units, refund_amount"
        ).eq("date", yesterday).execute()
        sales_by_product = {s["product_id"]: s for s in (sales_result.data or [])}

        # Load yesterday's PPC spend per product (via campaign → product mapping)
        # For now use campaign stats rolled up by product_id if that column exists
        ppc_result = await db.table("ppc_campaign_stats_daily").select(
            "product_id, spend"
        ).eq("date", yesterday).execute()
        ppc_by_product: dict[str, float] = {}
        for row in (ppc_result.data or []):
            pid = row.get("product_id")
            if pid:
                ppc_by_product[pid] = ppc_by_product.get(pid, 0.0) + (row.get("spend") or 0.0)

        fees_rows: list[dict] = []
        profit_rows: list[dict] = []

        for product_id, product in products.items():
            sales = sales_by_product.get(product_id)
            if not sales or not sales.get("units_sold"):
                continue  # No sales yesterday → no fees/profit rows needed

            units = int(sales["units_sold"])
            revenue = Decimal(str(sales["revenue"] or 0))
            refund_amount = Decimal(str(sales.get("refund_amount") or 0))
            net_revenue = revenue - refund_amount

            amazon_price = Decimal(str(product.get("amazon_price") or 0))
            landed_cost = Decimal(str(product.get("landed_cost") or 0))

            # Fee components
            ref_fee_pct = Decimal(str(product.get("referral_fee_pct") or _REFERRAL_FEE_PCT_DEFAULT))
            referral_fee = net_revenue * ref_fee_pct

            fba_fee_per_unit = Decimal(str(product.get("fba_fulfillment_fee") or 0))
            fba_fee = fba_fee_per_unit * units

            ppc_spend = Decimal(str(ppc_by_product.get(product_id, 0)))
            total_fees = referral_fee + fba_fee

            # COGS
            cogs = landed_cost * units
            gross_profit = net_revenue - cogs - total_fees - ppc_spend
            margin_pct = float(gross_profit / net_revenue * 100) if net_revenue else 0.0

            fees_rows.append({
                "product_id": product_id,
                "date": yesterday,
                "referral_fee": float(referral_fee),
                "fba_fulfillment_fee": float(fba_fee),
                "closing_fee": 0.0,
                "storage_fee": 0.0,
                "other_fees": 0.0,
                "total_fees": float(total_fees),
            })

            profit_rows.append({
                "product_id": product_id,
                "date": yesterday,
                "units_sold": units,
                "revenue": float(net_revenue),
                "cogs": float(cogs),
                "total_fees": float(total_fees),
                "ppc_spend": float(ppc_spend),
                "gross_profit": float(gross_profit),
                "margin_pct": round(margin_pct, 2),
            })

        if fees_rows:
            await db.table("fees_daily").upsert(fees_rows, on_conflict="product_id,date").execute()
        if profit_rows:
            await db.table("profit_daily").upsert(profit_rows, on_conflict="product_id,date").execute()

        records = len(profit_rows)
        await _write_sync_log(db, "success", records, start)
        await log_action(
            agent=_JOB_NAME,
            action="sync_complete",
            entity_type="profit_daily",
            details={"date": yesterday, "records": records},
        )
        logger.info("fees_sync_done", date=yesterday, records=records)

    except Exception as exc:
        error = str(exc)
        logger.error("fees_sync_error", exc=error)
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


async def _write_sync_log(db, status: str, records: int, start: float, error: str | None = None) -> None:
    await db.table("sync_log").insert({
        "type": _JOB_NAME,
        "status": status,
        "records_synced": records,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
