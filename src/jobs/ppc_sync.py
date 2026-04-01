"""
Daily job: sync Amazon Advertising campaign structure + keyword performance.

Flow:
  1. Sync campaign structure (campaigns → ad groups → keywords), resolving UUID FKs
  2. Request keyword-level performance report for yesterday
  3. Poll until ready, download + parse gzipped JSON
  4. Upsert into ppc_keyword_stats_daily
  5. Roll up to ppc_campaign_stats_daily via RPC

FK chain: Amazon uses text IDs; DB uses UUID PKs throughout.
Each level is upserted first, then queried back to build amazon_id → UUID maps.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from src.config.db_helpers import write_sync_log
from src.config.settings import settings
from src.config.supabase_client import get_marketplace_uuid, get_supabase
from src.spapi.advertising import AdsAPIClient
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

_JOB_NAME = "ppc_sync"


async def run() -> dict[str, Any]:
    start = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    records = 0
    error: str | None = None

    try:
        db = await get_supabase()
        ads = AdsAPIClient()
        marketplace_uuid = await get_marketplace_uuid(settings.SP_API_MARKETPLACE_CA)
        yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1))
        report_date = yesterday.strftime("%Y%m%d")
        yesterday_str = yesterday.isoformat()

        # ── 1. Sync campaigns ────────────────────────────────────────────────
        campaigns = await ads.list_campaigns()
        logger.info("ppc_sync_campaigns", count=len(campaigns))

        campaign_rows = [_map_campaign(c, marketplace_uuid) for c in campaigns]
        if campaign_rows:
            await db.table("ppc_campaigns").upsert(
                campaign_rows, on_conflict="campaign_id"
            ).execute()

        # Build amazon_campaign_id (text) → DB UUID map
        amazon_campaign_ids = [str(c["campaignId"]) for c in campaigns]
        campaign_uuid_map: dict[str, str] = {}
        if amazon_campaign_ids:
            res = await db.table("ppc_campaigns").select("id, campaign_id").execute()
            campaign_uuid_map = {r["campaign_id"]: r["id"] for r in (res.data or [])}

        # ── 2. Sync ad groups ────────────────────────────────────────────────
        ad_groups = await ads.list_ad_groups()
        ag_rows = []
        for ag in ad_groups:
            campaign_uuid = campaign_uuid_map.get(str(ag["campaignId"]))
            if not campaign_uuid:
                logger.warning("ppc_sync_ag_no_campaign", campaign_id=ag["campaignId"])
                continue
            ag_rows.append(_map_ad_group(ag, campaign_uuid))

        if ag_rows:
            await db.table("ppc_ad_groups").upsert(ag_rows, on_conflict="ad_group_id").execute()

        # Build amazon_ad_group_id (text) → DB UUID map
        ad_group_uuid_map: dict[str, str] = {}
        if ag_rows:
            res = await db.table("ppc_ad_groups").select("id, ad_group_id").execute()
            ad_group_uuid_map = {r["ad_group_id"]: r["id"] for r in (res.data or [])}

        # ── 3. Sync keywords ─────────────────────────────────────────────────
        keywords = await ads.list_keywords()
        kw_rows = []
        for kw in keywords:
            ag_uuid = ad_group_uuid_map.get(str(kw["adGroupId"]))
            if not ag_uuid:
                logger.warning("ppc_sync_kw_no_ag", ad_group_id=kw["adGroupId"])
                continue
            kw_rows.append(_map_keyword(kw, ag_uuid))

        if kw_rows:
            await db.table("ppc_keywords").upsert(kw_rows, on_conflict="keyword_id").execute()

        # Build amazon_keyword_id (text) → DB UUID map
        keyword_uuid_map: dict[str, str] = {}
        if kw_rows:
            res = await db.table("ppc_keywords").select("id, keyword_id").execute()
            keyword_uuid_map = {r["keyword_id"]: r["id"] for r in (res.data or [])}

        # ── 4. Keyword performance report ────────────────────────────────────
        logger.info("ppc_report_requesting", date=report_date)
        report_id = await ads.request_keyword_report(report_date)
        download_url = await ads.wait_for_report(report_id)
        report_rows = await ads.download_report(download_url)
        logger.info("ppc_report_downloaded", rows=len(report_rows))

        stat_rows = []
        for row in report_rows:
            kw_uuid = keyword_uuid_map.get(str(row.get("keywordId")))
            if not kw_uuid:
                continue
            stat_rows.append({
                "keyword_id": kw_uuid,
                "stat_date": yesterday_str,
                "impressions": row.get("impressions", 0),
                "clicks": row.get("clicks", 0),
                "spend": row.get("cost", 0.0),
                "sales": row.get("attributedSales14d", 0.0),
                "orders": row.get("attributedConversions14d", 0),
                "acos": _safe_acos(row.get("cost"), row.get("attributedSales14d")),
            })

        if stat_rows:
            await db.table("ppc_keyword_stats_daily").upsert(
                stat_rows, on_conflict="keyword_id,stat_date"
            ).execute()

        # ── 5. Roll up to campaign stats ─────────────────────────────────────
        await _rollup_campaign_stats(db, yesterday_str)

        records = len(stat_rows)
        await write_sync_log(db, _JOB_NAME, "success", records, start, started_at)
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
            await write_sync_log(db, _JOB_NAME, "failed", records, start, started_at, error)
        except Exception:
            pass
        raise

    return {
        "records_synced": records,
        "duration_seconds": round(time.monotonic() - start, 2),
        "error": error,
    }


def _map_campaign(c: dict, marketplace_uuid: str) -> dict:
    # Amazon Ads API returns state as "ENABLED"/"PAUSED"/"ARCHIVED" — DB enum is lowercase.
    # start_date is "YYYYMMDD" — reformat to ISO "YYYY-MM-DD" for PostgreSQL date column.
    raw_date = c.get("startDate", "")
    iso_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}" if len(raw_date) == 8 else None
    return {
        "campaign_id": str(c["campaignId"]),
        "campaign_name": c.get("name"),
        "marketplace_id": marketplace_uuid,
        "state": (c.get("state") or "").lower() or None,
        "targeting_type": c.get("targetingType"),
        "daily_budget": c.get("dailyBudget"),
        "start_date": iso_date,
    }


def _map_ad_group(ag: dict, campaign_uuid: str) -> dict:
    return {
        "ad_group_id": str(ag["adGroupId"]),
        "campaign_id": campaign_uuid,
        "ad_group_name": ag.get("name"),
        "state": (ag.get("state") or "").lower() or None,
        "default_bid": ag.get("defaultBid"),
    }


def _map_keyword(kw: dict, ag_uuid: str) -> dict:
    return {
        "keyword_id": str(kw["keywordId"]),
        "ad_group_id": ag_uuid,
        "keyword_text": kw.get("keywordText"),
        "match_type": (kw.get("matchType") or "").lower() or None,
        "state": (kw.get("state") or "").lower() or None,
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
