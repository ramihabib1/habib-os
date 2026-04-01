"""
Shared database helper utilities used across sync jobs.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from supabase import AClient as AsyncClient


async def write_sync_log(
    db: AsyncClient,
    sync_type: str,
    status: str,
    records: int,
    start_time: float,
    started_at: str,
    error: str | None = None,
) -> None:
    """
    Write one row to the sync_log table.

    Args:
        db:          Supabase async client.
        sync_type:   Job name string (e.g. "inventory_sync").
        status:      "success", "partial", or "failed".
        records:     Number of records synced.
        start_time:  monotonic start time (from time.monotonic()).
        started_at:  ISO-8601 timestamp when the job started.
        error:       Error message if status is "failed".
    """
    duration_ms = int((time.monotonic() - start_time) * 1000)
    await db.table("sync_log").insert({
        "sync_type": sync_type,
        "status": status,
        "records_synced": records,
        "duration_ms": duration_ms,
        "error_message": error,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }).execute()
