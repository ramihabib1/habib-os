"""
Verify Supabase database state after running sync_all.py.

Checks every table that the sync jobs write to, confirms row counts
and data recency, then queries all four pre-built views to confirm
end-to-end data is queryable.

Usage (from project root, venv active):
    python scripts/test_db_integration.py

Run AFTER scripts/sync_all.py.
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config.supabase_client import get_supabase, close_supabase

DIVIDER = "=" * 60
OK   = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
INFO = "  →"

passed_count = 0
failed_count = 0


def ok(msg: str) -> None:
    global passed_count
    passed_count += 1
    print(f"  {OK} {msg}")


def fail(msg: str) -> None:
    global failed_count
    failed_count += 1
    print(f"  {FAIL} {msg}")


def info(msg: str) -> None:
    print(f"{INFO} {msg}")


def section(title: str) -> None:
    print(f"\n{DIVIDER}\n  {title}\n{DIVIDER}")


async def check_table_count(db, table: str, min_count: int = 1, label: str | None = None) -> int:
    """Check row count for a table. Returns actual count."""
    label = label or table
    try:
        result = await db.table(table).select("*", count="exact").limit(0).execute()
        count = result.count or 0
        if count >= min_count:
            ok(f"{label}: {count} rows")
        else:
            fail(f"{label}: {count} rows (expected >= {min_count})")
        return count
    except Exception as e:
        fail(f"{label}: query error — {e}")
        return 0


async def check_recent_snapshot(db, table: str, ts_col: str, minutes: int = 30) -> None:
    """Check that the most recent entry in a table is within the last N minutes."""
    try:
        result = await db.table(table).select(ts_col).order(ts_col, desc=True).limit(1).execute()
        if not result.data:
            fail(f"{table}.{ts_col}: no rows found")
            return
        raw_ts = result.data[0][ts_col]
        # Handle date-only fields (e.g. snapshot_date = "2026-03-31")
        if len(raw_ts) == 10:
            today = datetime.now(timezone.utc).date().isoformat()
            if raw_ts == today:
                ok(f"{table}.{ts_col}: today ({raw_ts})")
            else:
                fail(f"{table}.{ts_col}: last entry is {raw_ts}, expected today")
            return
        # Parse full ISO timestamp
        latest = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        age_min = (datetime.now(timezone.utc) - latest).total_seconds() / 60
        if age_min <= minutes:
            ok(f"{table}.{ts_col}: {age_min:.1f} min ago (within {minutes} min)")
        else:
            fail(f"{table}.{ts_col}: last entry was {age_min:.0f} min ago (expected within {minutes} min)")
    except Exception as e:
        fail(f"{table}.{ts_col}: query error — {e}")


async def check_view(db, view: str, columns: list[str], label: str) -> None:
    """Query a view and show results."""
    try:
        result = await db.table(view).select(",".join(columns)).limit(10).execute()
        rows = result.data or []
        if rows:
            ok(f"{label}: {len(rows)} rows returned")
            for row in rows[:5]:
                vals = " | ".join(f"{c}={row.get(c, '?')}" for c in columns)
                print(f"      {vals}")
        else:
            fail(f"{label}: view returned 0 rows")
    except Exception as e:
        fail(f"{label}: query error — {e}")


async def main() -> None:
    print(f"\n{DIVIDER}")
    print("  Habib OS — Database Integration Test")
    print(f"  Checking all tables and views populated by sync_all.py")
    print(DIVIDER)

    # ── Connection ────────────────────────────────────────────────────────────
    section("1. Supabase Connection")
    try:
        db = await get_supabase()
        result = await db.table("marketplaces").select("id").limit(1).execute()
        ok("Supabase connected (service_role key)")
    except Exception as e:
        fail(f"Connection failed: {e}")
        print("\n  Cannot continue without DB connection.\n")
        sys.exit(1)

    # ── Core tables ───────────────────────────────────────────────────────────
    section("2. Core Seed Data")
    await check_table_count(db, "products", min_count=1, label="products (catalog seeded)")
    await check_table_count(db, "marketplaces", min_count=1, label="marketplaces")

    # ── Inventory ─────────────────────────────────────────────────────────────
    section("3. Inventory Sync (inventory_sync)")
    await check_table_count(db, "inventory_snapshots", min_count=1)
    await check_recent_snapshot(db, "inventory_snapshots", "snapshot_at", minutes=30)

    # ── Orders ────────────────────────────────────────────────────────────────
    section("4. Orders Sync (orders_sync)")
    # 0 rows is acceptable on a new account with no recent orders
    await check_table_count(db, "orders", min_count=0, label="orders (0 ok if no recent orders)")
    await check_table_count(db, "order_items", min_count=0, label="order_items (0 ok if no recent orders)")
    await check_table_count(db, "sales_daily", min_count=0, label="sales_daily (0 ok if no sales yet)")

    # ── Fees & Profit ─────────────────────────────────────────────────────────
    section("5. Fees & Profit (fees_sync)")
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        r = await db.table("fees_daily").select("*", count="exact").eq("fee_date", today).limit(0).execute()
        count = r.count or 0
        ok(f"fees_daily for today: {count} rows (0 ok if no sales yet)")
    except Exception as e:
        fail(f"fees_daily: query error — {e}")

    try:
        r = await db.table("profit_daily").select("*", count="exact").eq("profit_date", today).limit(0).execute()
        count = r.count or 0
        ok(f"profit_daily for today: {count} rows (0 ok if no sales yet)")
    except Exception as e:
        fail(f"profit_daily: query error — {e}")

    # ── PPC ───────────────────────────────────────────────────────────────────
    section("6. PPC Sync (ppc_sync)")
    # 0 rows expected until ADS_API_REFRESH_TOKEN is configured
    await check_table_count(db, "ppc_campaigns", min_count=0, label="ppc_campaigns (0 ok if ADS creds not set)")
    await check_table_count(db, "ppc_ad_groups", min_count=0, label="ppc_ad_groups")
    await check_table_count(db, "ppc_keywords", min_count=0, label="ppc_keywords")
    await check_table_count(db, "ppc_keyword_stats_daily", min_count=0, label="ppc_keyword_stats_daily")
    await check_table_count(db, "ppc_campaign_stats_daily", min_count=0, label="ppc_campaign_stats_daily (rollup)")

    # ── Product Snapshots ─────────────────────────────────────────────────────
    section("7. Reviews / BSR Sync (reviews_sync)")
    # New ASINs may have no BSR/rating yet — 0 rows is acceptable early on
    await check_table_count(db, "product_snapshots", min_count=0, label="product_snapshots (0 ok for new ASINs)")
    snap_count = await db.table("product_snapshots").select("*", count="exact").limit(0).execute()
    if (snap_count.count or 0) > 0:
        await check_recent_snapshot(db, "product_snapshots", "snapshot_date", minutes=60)

    # ── System Logs ───────────────────────────────────────────────────────────
    section("8. System Logs")
    await check_table_count(db, "sync_log", min_count=1)
    await check_table_count(db, "audit_log", min_count=1)

    # Show last 8 sync_log entries
    try:
        r = await db.table("sync_log").select("sync_type,status,records_synced,duration_ms,completed_at").order("completed_at", desc=True).limit(8).execute()
        if r.data:
            print()
            info("Last 8 sync_log entries:")
            for row in r.data:
                status_icon = "✓" if row.get("status") == "success" else "✗"
                ts_str = (row.get("completed_at") or "")[:19].replace("T", " ")
                dur_s = round((row.get("duration_ms") or 0) / 1000, 1)
                print(f"      {status_icon} {row.get('sync_type'):<22} records={row.get('records_synced'):<5} {dur_s}s  {ts_str}")
    except Exception as e:
        info(f"Could not fetch sync_log entries: {e}")

    # ── Views ─────────────────────────────────────────────────────────────────
    section("9. Pre-built Views")

    await check_view(
        db, "v_current_inventory",
        ["sku", "fba_fulfillable", "fba_total"],
        "v_current_inventory (FBA stock per SKU)",
    )

    await check_view(
        db, "v_days_of_stock",
        ["sku", "fba_days_remaining"],
        "v_days_of_stock (days until stockout)",
    )

    # These views return 0 rows until sales/PPC data exists — queryable = pass
    try:
        r = await db.table("v_product_profitability_30d").select("sku,gross_profit,margin_pct").limit(10).execute()
        ok(f"v_product_profitability_30d (profit per SKU): queryable, {len(r.data or [])} rows (0 ok if no sales)")
    except Exception as e:
        fail(f"v_product_profitability_30d: query error — {e}")

    try:
        r = await db.table("v_ppc_overview_7d").select("campaign_name,spend,acos").limit(10).execute()
        ok(f"v_ppc_overview_7d (PPC performance): queryable, {len(r.data or [])} rows (0 ok if ADS creds not set)")
    except Exception as e:
        fail(f"v_ppc_overview_7d: query error — {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    section("Summary")
    total = passed_count + failed_count
    print(f"  {passed_count}/{total} checks passed\n")

    if failed_count == 0:
        print("  All checks passed. Database is fully populated and queryable.")
    else:
        print(f"  {failed_count} check(s) failed — review output above.")

    print()
    await close_supabase()

    if failed_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
