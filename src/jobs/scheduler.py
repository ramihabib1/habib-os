"""
Main scheduler entry point — runs all sync jobs and agent skills on a schedule.

Schedule:
  :00 every hour   — inventory_sync (active SKUs only, targeted query)
  :15 every hour   — orders_sync
  :30 every hour   — expire_approvals
  00:00 daily      — listings_sync (Amazon → products table, source of truth)
  06:00 daily      — listings_sync (second run — catch mid-day listing changes)
  06:30 daily      — fees_sync
  07:00 daily      — ppc_sync
  07:00 every 4h   — pricing_sync (price + BSR via getPricing)
  08:00 daily      — reviews_sync (rating + review_count only)
  08:30 daily      — ops_agent: daily_briefing
  20:00 Sunday     — finance_agent: weekly_finance
  09:00 Monday     — competitor_sync + marketing_agent: competitor_snapshot

Run via PM2: pm2 start ecosystem.config.js
"""

from __future__ import annotations

import asyncio
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

import schedule

from src.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)


async def _run_job(
    name: str,
    coro_fn: Callable[[], Awaitable[Any]],
) -> None:
    """Execute a job coroutine, log result, alert on failure."""
    logger.info("job_start", job=name)
    try:
        result = await coro_fn()
        logger.info("job_done", job=name, result=result)
    except Exception:
        tb = traceback.format_exc()
        logger.error("job_failed", job=name, traceback=tb)
        try:
            from src.telegram.notifications import send_alert
            await send_alert(
                f"❌ *Job failed:* `{name}`\n\n```\n{tb[-800:]}\n```",
                roles=["rami"],
            )
        except Exception:
            pass


def _schedule_async(name: str, coro_fn: Callable[[], Awaitable[Any]]) -> None:
    """Wrap async job so schedule library can call it synchronously."""
    asyncio.create_task(_run_job(name, coro_fn))


def _register_jobs() -> None:
    """Register all jobs with the schedule library."""
    from src.jobs import (
        competitor_sync,
        expire_approvals,
        fees_sync,
        inventory_sync,
        listings_sync,
        orders_sync,
        pricing_sync,
        ppc_sync,
        reviews_sync,
    )

    # ── Hourly ────────────────────────────────────────────────────────────────
    schedule.every().hour.at(":00").do(
        _schedule_async, "inventory_sync", inventory_sync.run
    )
    schedule.every().hour.at(":15").do(
        _schedule_async, "orders_sync", orders_sync.run
    )
    schedule.every().hour.at(":30").do(
        _schedule_async, "expire_approvals", expire_approvals.run
    )

    # ── Daily — listings_sync runs twice: midnight + 06:00 ───────────────────
    # listings_sync must run BEFORE inventory_sync has a chance to use stale
    # is_active flags. Two runs catches mid-day listing status changes.
    schedule.every().day.at("00:00").do(
        _schedule_async, "listings_sync_midnight", listings_sync.run
    )
    schedule.every().day.at("06:00").do(
        _schedule_async, "listings_sync_morning", listings_sync.run
    )
    schedule.every().day.at("06:30").do(
        _schedule_async, "fees_sync", fees_sync.run
    )
    schedule.every().day.at("07:00").do(
        _schedule_async, "ppc_sync", ppc_sync.run
    )
    schedule.every().day.at("08:00").do(
        _schedule_async, "reviews_sync", reviews_sync.run
    )
    schedule.every().day.at("08:30").do(
        _schedule_async, "daily_briefing", _daily_briefing
    )

    # ── Every 4 hours — pricing + BSR ────────────────────────────────────────
    schedule.every(4).hours.do(
        _schedule_async, "pricing_sync", pricing_sync.run
    )

    # ── Weekly ────────────────────────────────────────────────────────────────
    schedule.every().sunday.at("20:00").do(
        _schedule_async, "weekly_finance", _weekly_finance
    )
    schedule.every().monday.at("09:00").do(
        _schedule_async, "competitor_sync", competitor_sync.run
    )
    schedule.every().monday.at("09:30").do(
        _schedule_async, "competitor_snapshot", _competitor_snapshot
    )

    logger.info("jobs_registered", count=len(schedule.jobs))


# ── Agent skill launchers ─────────────────────────────────────────────────────

async def _daily_briefing() -> dict:
    from src.agents.ops_agent import OpsAgent
    agent = OpsAgent()
    return await agent.run_skill("daily_briefing")


async def _weekly_finance() -> dict:
    from src.agents.finance_agent import FinanceAgent
    agent = FinanceAgent()
    return await agent.run_skill("weekly_finance")


async def _competitor_snapshot() -> dict:
    from src.agents.marketing_agent import MarketingAgent
    agent = MarketingAgent()
    return await agent.run_skill("competitor_snapshot")


# ── Main loop ─────────────────────────────────────────────────────────────────

async def _scheduler_loop() -> None:
    """Run the schedule check loop every 30 seconds."""
    while True:
        schedule.run_pending()
        await asyncio.sleep(30)


async def main() -> None:
    logger.info("scheduler_starting")
    _register_jobs()

    # Run startup jobs immediately if this is the first boot
    # (comment out after first successful run to avoid re-running on every restart)
    # asyncio.create_task(_run_job("inventory_sync_startup", inventory_sync.run))

    await _scheduler_loop()


if __name__ == "__main__":
    asyncio.run(main())
