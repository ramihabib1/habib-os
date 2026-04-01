"""
PreToolUse hook for the Claude Agent SDK.

When an agent tries to call a tool that involves a financial action
(price change, bid change, replenishment, etc.), this hook:
  1. Creates an approval_request row in Supabase (status=pending)
  2. Sends a Telegram message to Rami with Approve/Reject buttons
  3. Polls the DB until Rami responds (or timeout)
  4. Returns allow=True (approved) or allow=False (rejected/timeout)

This implements the Golden Rule: NEVER take financial action without approval.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from src.config.settings import settings
from src.config.supabase_client import get_supabase
from src.telegram.approval import send_approval_request
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Tool names that require human approval before execution
_FINANCIAL_TOOLS = {
    "update_ppc_bid",
    "update_ppc_budget",
    "update_price",
    "create_fba_shipment",
    "trigger_supplier_reorder",
    "update_listing",
    "create_campaign",
    "pause_campaign",
    "resume_campaign",
    "apply_coupon",
    "delete_coupon",
}

# Map tool name → action_type for approval_requests table
_TOOL_TO_ACTION_TYPE: dict[str, str] = {
    "update_ppc_bid": "ppc_bid_change",
    "update_ppc_budget": "ppc_budget_change",
    "update_price": "price_change",
    "create_fba_shipment": "fba_replenishment",
    "trigger_supplier_reorder": "supplier_reorder",
    "update_listing": "listing_change",
    "create_campaign": "campaign_create",
    "pause_campaign": "campaign_pause",
    "resume_campaign": "campaign_pause",
    "apply_coupon": "custom",
    "delete_coupon": "custom",
}

# How long to wait for approval (matches DB expiry + buffer)
_APPROVAL_TIMEOUT_SECONDS = 86400  # 24 hours
_POLL_INTERVAL_SECONDS = 10


async def pre_tool_use_hook(
    tool_name: str,
    tool_input: dict[str, Any],
    agent_name: str,
    reason: str = "",
) -> bool:
    """
    PreToolUse hook. Returns True if the tool call should proceed.

    For financial tools: creates approval request, waits for Rami's response.
    For non-financial tools: returns True immediately.
    """
    if tool_name not in _FINANCIAL_TOOLS:
        return True

    action_type = _TOOL_TO_ACTION_TYPE.get(tool_name, "custom")
    request_id = str(uuid.uuid4())

    logger.info(
        "approval_required",
        tool=tool_name,
        action_type=action_type,
        request_id=request_id,
        agent=agent_name,
    )

    # Create approval_request in DB
    request = {
        "id": request_id,
        "action_type": action_type,
        "agent": agent_name,
        "description": reason or f"Agent wants to call {tool_name}",
        "payload": tool_input,
        "status": "pending",
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        db = await get_supabase()
        await db.table("approval_requests").insert(request).execute()
    except Exception as exc:
        logger.error("approval_request_insert_failed", exc=str(exc))
        return False  # Fail closed: block if we can't log

    # Send Telegram message immediately
    await send_approval_request(request)

    # Poll for response
    approved = await _wait_for_approval(request_id)

    await log_action(
        agent=agent_name,
        action=f"tool_{'allowed' if approved else 'blocked'}",
        entity_type="approval_requests",
        entity_id=request_id,
        details={"tool": tool_name, "approved": approved},
    )

    return approved


async def _wait_for_approval(request_id: str) -> bool:
    """
    Poll approval_requests until status is no longer 'pending'.
    Returns True if approved, False if rejected/expired/error.
    """
    deadline = asyncio.get_event_loop().time() + _APPROVAL_TIMEOUT_SECONDS

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        try:
            db = await get_supabase()
            result = await (
                db.table("approval_requests")
                .select("status")
                .eq("id", request_id)
                .single()
                .execute()
            )
            status = result.data.get("status") if result.data else None
        except Exception as exc:
            logger.error("approval_poll_error", request_id=request_id, exc=str(exc))
            continue

        if status == "approved":
            logger.info("approval_granted", request_id=request_id)
            return True
        if status in ("rejected", "expired"):
            logger.info("approval_denied", request_id=request_id, status=status)
            return False
        # Still pending — keep polling

    logger.warning("approval_timeout", request_id=request_id)
    return False
