"""
Daily job: sync Amazon Advertising campaign structure + keyword performance.

Flow:
  1. Sync campaign → ad group → keyword structure (upsert all)
  2. Request keyword-level performance report for yesterday
  3. Poll until ready, download + parse gzipped JSON
  4. Upsert into ppc_keyword_stats_daily
  5. Roll up to ppc_campaign_stats_daily
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.supabase_client import get_supabase
from src.spapi.advertising import AdsAPIClient
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "ppc_sync"


async def run() -> dict[str, Any]:
    start = time.monotonic()
    records = 0
    error: str | None = None

    try:
        db = await get_supabase()
        ads = AdsAPIClient()
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1))
        report_date = yesterday.strftime("%Y%m%d")
        yesterday_str = yesterday.isoformat()

        # ── 1. Sync campaign structure ────────────────────────────────────────
        campaigns = await ads.list_campaigns()
        logger.info("ppc_sync_campaigns", count=len(campaigns))

        campaign_rows = [_map_campaign(c) for c in campaigns]
        if campaign_rows:
            await db.table("ppc_campaigns").upsert(
                campaign_rows, on_conflict="campaign_id"
            ).execute()

        # Ad groups and keywords
        ad_groups = await ads.list_ad_groups()
        ag_rows = [_map_ad_group(ag) for ag in ad_groups]
        if ag_rows:
            await db.table("ppc_ad_groups").upsert(ag_rows, on_conflict="ad_group_id").execute()

        keywords = await ads.list_keywords()
        kw_rows = [_map_keyword(kw) for kw in keywords]
        if kw_rows:
            await db.table("ppc_keywords").upsert(kw_rows, on_conflict="keyword_id").execute()

        # ── 2–4. Keyword performance report ─────────────────────────────────
        logger.info("ppc_report_requesting", date=report_date)
        report_id = await ads.request_keyword_report(report_date)
        download_url = await ads.wait_for_report(report_id)
        report_rows = await ads.download_report(download_url)
        logger.info("ppc_report_downloaded", rows=len(report_rows))

        # Build keyword_id → DB id map
        kw_result = await db.table("ppc_keywords").select("id, keyword_id").execute()
        keyword_id_map: dict[str, str] = {k["keyword_id"]: k["id"] for k in (kw_result.data or [])}

        stat_rows = []
        for row in report_rows:
            kw_db_id = keyword_id_map.get(str(row.get("keywordId")))
            if not kw_db_id:
                continue
            stat_rows.append({
                "keyword_id": kw_db_id,
                "date": yesterday_str,
                "impressions": row.get("impressions", 0),
                "clicks": row.get("clicks", 0),
                "spend": row.get("cost", 0.0),
                "sales": row.get("attributedSales14d", 0.0),
                "orders": row.get("attributedConversions14d", 0),
                "acos": _safe_acos(row.get("cost"), row.get("attributedSales14d")),
            })

        if stat_rows:
            await db.table("ppc_keyword_stats_daily").upsert(
                stat_rows, on_conflict="keyword_id,date"
            ).execute()

        # ── 5. Roll up to campaign stats ─────────────────────────────────────
        await _rollup_campaign_stats(db, yesterday_str)

        records = len(stat_rows)
        await _write_sync_log(db, "success", records, start)
        await log_action(
            agent=_JOB_NAME,
            action="sync_complete",
            entity_type="ppc_keyword_stats_daily",
            details={"date": yesterday_str, "keyword_stats": records},
        )
        logger.info("ppc_sync_done", date=yesterday_str, records=records)

    except Exception as exc:
        error = str(exc)
        logger.error("ppc_sync_error", exc=error)
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


def _map_campaign(c: dict) -> dict:
    return {
        "campaign_id": str(c["campaignId"]),
        "name": c.get("name"),
        "state": c.get("state"),
        "targeting_type": c.get("targetingType"),
        "daily_budget": c.get("dailyBudget"),
        "start_date": c.get("startDate"),
        "end_date": c.get("endDate"),
    }


def _map_ad_group(ag: dict) -> dict:
    return {
        "ad_group_id": str(ag["adGroupId"]),
        "campaign_id": str(ag["campaignId"]),
        "name": ag.get("name"),
        "state": ag.get("state"),
        "default_bid": ag.get("defaultBid"),
    }


def _map_keyword(kw: dict) -> dict:
    return {
        "keyword_id": str(kw["keywordId"]),
        "ad_group_id": str(kw["adGroupId"]),
        "campaign_id": str(kw["campaignId"]),
        "keyword_text": kw.get("keywordText"),
        "match_type": kw.get("matchType"),
        "state": kw.get("state"),
        "bid": kw.get("bid"),
    }


def _safe_acos(spend: Any, sales: Any) -> float | None:
    try:
        s = float(spend or 0)
        r = float(sales or 0)
        return round(s / r * 100, 2) if r > 0 else None
    except (TypeError, ValueError):
        return None


async def _rollup_campaign_stats(db, date: str) -> None:
    """Roll up keyword stats to campaign level for the given date."""
    try:
        await db.rpc("rollup_ppc_campaign_stats", {"p_date": date}).execute()
    except Exception as exc:
        logger.warning("ppc_rollup_failed", exc=str(exc))


async def _write_sync_log(db, status: str, records: int, start: float, error: str | None = None) -> None:
    await db.table("sync_log").insert({
        "type": _JOB_NAME,
        "status": status,
        "records_synced": records,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
