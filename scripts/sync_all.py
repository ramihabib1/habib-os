"""
Run all sync jobs sequentially and report results.

Executes every data pipeline job in dependency order:
  1. listings_sync   — Amazon listings report → products table (is_active source of truth)
  2. inventory_sync  — FBA inventory → inventory_snapshots (uses is_active from step 1)
  3. orders_sync     — Orders + items → orders, order_items, sales_daily
  4. pricing_sync    — getPricing → products.amazon_price + product_snapshots BSR
  5. ppc_sync        — Advertising API → ppc_* tables (requires ADS_API_REFRESH_TOKEN)
  6. reviews_sync    — Catalog API → product_snapshots rating/review_count
  7. competitor_sync — Catalog API → competitor_snapshots
  8. fees_sync       — Profit calc → fees_daily, profit_daily (needs sales_daily from step 3)

Usage (from project root, venv active):
    python scripts/sync_all.py
"""

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.jobs import (
    listings_sync,
    inventory_sync,
    orders_sync,
    pricing_sync,
    ppc_sync,
    reviews_sync,
    competitor_sync,
    fees_sync,
)

DIVIDER = "=" * 60
OK   = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


async def run_job(name: str, coro) -> tuple[bool, dict]:
    print(f"\n{DIVIDER}")
    print(f"  {name}  [{ts()}]")
    print(DIVIDER)
    start = time.monotonic()
    try:
        result = await coro
        elapsed = round(time.monotonic() - start, 1)
        records = result.get("records_synced", result.get("records", "?"))
        skipped = result.get("skipped", 0)
        error   = result.get("error")
        if error:
            print(f"  {FAIL} FAILED in {elapsed}s — {error}")
            return False, result
        print(f"  {OK} PASSED in {elapsed}s — records={records} skipped={skipped}")
        return True, result
    except Exception as exc:
        elapsed = round(time.monotonic() - start, 1)
        print(f"  {FAIL} EXCEPTION in {elapsed}s — {exc}")
        return False, {"error": str(exc)}


async def main() -> None:
    wall_start = time.monotonic()
    print(f"\n{DIVIDER}")
    print("  Habib OS — Full Data Sync")
    print(f"  Started at {ts()}")
    print(DIVIDER)

    jobs = [
        ("listings_sync",  listings_sync.run()),
        ("inventory_sync", inventory_sync.run()),
        ("orders_sync",    orders_sync.run()),
        ("pricing_sync",   pricing_sync.run()),
        ("ppc_sync",       ppc_sync.run()),
        ("reviews_sync",   reviews_sync.run()),
        ("competitor_sync", competitor_sync.run()),
        ("fees_sync",      fees_sync.run()),
    ]

    results: list[tuple[str, bool, dict]] = []
    for name, coro in jobs:
        passed, result = await run_job(name, coro)
        results.append((name, passed, result))

    # Summary
    elapsed_total = round(time.monotonic() - wall_start, 1)
    print(f"\n{DIVIDER}")
    print(f"  Summary — {elapsed_total}s total")
    print(DIVIDER)
    all_passed = True
    for name, passed, result in results:
        icon = OK if passed else FAIL
        records = result.get("records_synced", result.get("records", "?"))
        print(f"  {icon} {name:<20} records={records}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("  All jobs passed. Database is fully synced.")
    else:
        print("  Some jobs failed — check output above for details.")
    print()

    if not all_passed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
