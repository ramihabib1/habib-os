"""
Daily job: calculate fees_daily and profit_daily from known fee structures.

Sources:
  - products table: landed_cost, amazon_price, referral_fee (flat CAD), fba_fulfillment (flat CAD)
  - sales_daily: units sold and revenue per SKU per day
  - (PPC spend per product not yet attributable — set to 0 until campaign→product mapping exists)

Writes:
  - fees_daily: fee breakdown per SKU per day
  - profit_daily: revenue - COGS - fees per SKU per day
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from src.config.db_helpers import write_sync_log
from src.config.supabase_client import get_supabase
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "fees_sync"


async def run() -> dict[str, Any]:
    """Calculate and upsert fees_daily + profit_daily for yesterday."""
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    records = 0
    error: str | None = None

    try:
        db = await get_supabase()
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()

        # Load all products with fee structure
        # referral_fee = flat CAD amount per unit (from DB)
        # fba_fulfillment = flat CAD fulfillment fee per unit (from DB)
        products_result = await db.table("products").select(
            "id, sku, landed_cost, amazon_price, referral_fee, fba_fulfillment"
        ).execute()
        products = {p["id"]: p for p in (products_result.data or [])}

        # Load yesterday's sales (sale_date column)
        sales_result = await db.table("sales_daily").select(
            "product_id, units_sold, revenue, refund_units, refund_amount"
        ).eq("sale_date", yesterday).execute()
        sales_by_product = {s["product_id"]: s for s in (sales_result.data or [])}

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

            landed_cost = Decimal(str(product.get("landed_cost") or 0))

            # Flat fee per unit (CAD amounts stored in products table)
            referral_fee_per_unit = Decimal(str(product.get("referral_fee") or 0))
            fba_fee_per_unit = Decimal(str(product.get("fba_fulfillment") or 0))

            referral_fee = referral_fee_per_unit * units
            fba_fee = fba_fee_per_unit * units
            total_fees = referral_fee + fba_fee

            # COGS and profit (PPC spend set to 0 until campaign→product mapping exists)
            cogs = landed_cost * units
            ppc_spend = Decimal("0")
            gross_profit = net_revenue - cogs - total_fees - ppc_spend
            margin_pct = float(gross_profit / net_revenue * 100) if net_revenue else 0.0

            fees_rows.append({
                "product_id": product_id,
                "fee_date": yesterday,
                "referral_fee": float(referral_fee),
                "fba_fulfillment_fee": float(fba_fee),
                "fba_storage_fee": 0.0,
                "other_fees": 0.0,
                "ppc_spend": float(ppc_spend),
                "total_fees": float(total_fees),
            })

            profit_rows.append({
                "product_id": product_id,
                "profit_date": yesterday,
                "units_sold": units,
                "revenue": float(net_revenue),
                "cogs": float(cogs),
                "total_fees": float(total_fees),
                "ppc_spend": float(ppc_spend),
                "gross_profit": float(gross_profit),
                "margin_pct": round(margin_pct, 2),
            })

        if fees_rows:
            await db.table("fees_daily").upsert(
                fees_rows, on_conflict="product_id,fee_date"
            ).execute()
        if profit_rows:
            await db.table("profit_daily").upsert(
                profit_rows, on_conflict="product_id,profit_date"
            ).execute()

        records = len(profit_rows)
        await write_sync_log(db, _JOB_NAME, "success", records, start, started_at)
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
            await write_sync_log(db, _JOB_NAME, "failed", records, start, started_at, error)
        except Exception:
            pass
        raise

    return {
        "records_synced": records,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
    }
