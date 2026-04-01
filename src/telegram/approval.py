"""
Approval workflow — formats approval requests and processes Telegram callbacks.

Flow:
  1. Agent creates row in approval_requests (status=pending)
  2. send_approval_request() sends Telegram message with Approve/Reject buttons
  3. handle_callback() processes the button tap → updates DB → unblocks agent
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from src.config.supabase_client import get_supabase
from src.config.settings import settings
from src.telegram.notifications import send_to_role
from src.utils.audit import log_action
from src.utils.logging import get_logger

logger = get_logger(__name__)

ACTION_LABELS: dict[str, str] = {
    "ppc_bid_change": "PPC Bid Change",
    "ppc_budget_change": "PPC Budget Change",
    "price_change": "Price Change",
    "fba_replenishment": "FBA Replenishment",
    "supplier_reorder": "Supplier Reorder",
    "listing_change": "Listing Change",
    "campaign_create": "Create Campaign",
    "campaign_pause": "Pause Campaign",
    "custom": "Custom Action",
}


def _format_approval_message(request: dict[str, Any]) -> str:
    """Build the Telegram message text for an approval request."""
    action_label = ACTION_LABELS.get(request["action_type"], request["action_type"])
    payload = request.get("payload", {})

    lines = [
        f"*⚠️ Approval Required: {action_label}*",
        f"ID: `{request['id']}`",
        "",
        f"*Agent:* {request.get('agent', 'system')}",
        f"*Reason:* {request.get('description', 'N/A')}",
        "",
        "*Details:*",
        f"```\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n```",
        "",
        "_Expires in 24 hours. Tap to approve or reject._",
    ]
    return "\n".join(lines)


def _approval_keyboard(request_id: str) -> InlineKeyboardMarkup:
    """Return Approve / Reject inline keyboard for an approval request."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{request_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{request_id}"),
        ]
    ])


async def send_approval_request(request: dict[str, Any]) -> int | None:
    """
    Send an approval request to Rami via Telegram.
    Only Rami can approve; this always goes to his chat.

    Returns the Telegram message_id on success, None on failure.
    """
    text = _format_approval_message(request)
    keyboard = _approval_keyboard(str(request["id"]))

    msg_id = await send_to_role("rami", text, reply_markup=keyboard)
    if msg_id:
        logger.info("approval_request_sent", request_id=request["id"], msg_id=msg_id)
    else:
        logger.error("approval_request_send_failed", request_id=request["id"])
    return msg_id


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle Approve/Reject button taps from Rami.
    Updates approval_requests status in DB and edits the Telegram message.
    """
    query = update.callback_query
    await query.answer()

    data: str = query.data  # "approve:<uuid>" or "reject:<uuid>"
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] not in ("approve", "reject"):
        await query.edit_message_text("⚠️ Unknown action.")
        return

    action, request_id = parts
    new_status = "approved" if action == "approve" else "rejected"
    approver_chat_id = str(query.from_user.id)

    # Only Rami can approve
    if approver_chat_id != settings.TELEGRAM_RAMI_CHAT_ID:
        await query.answer("Only Rami can approve actions.", show_alert=True)
        return

    try:
        db = await get_supabase()
        result = (
            await db.table("approval_requests")
            .update({
                "status": new_status,
                "approved_by": approver_chat_id,
                "responded_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("id", request_id)
            .eq("status", "pending")  # guard: only update if still pending
            .execute()
        )

        if not result.data:
            await query.edit_message_text(
                f"⚠️ Request `{request_id}` not found or already processed."
            )
            return

        emoji = "✅" if new_status == "approved" else "❌"
        await query.edit_message_text(
            f"{emoji} *{new_status.capitalize()}* by Rami.\n\nRequest ID: `{request_id}`",
            parse_mode="Markdown",
        )

        await log_action(
            agent="telegram_bot",
            action=f"approval_{new_status}",
            entity_type="approval_requests",
            entity_id=request_id,
            details={"approver": approver_chat_id},
        )

        logger.info("approval_handled", request_id=request_id, status=new_status)

    except Exception as exc:
        logger.error("approval_callback_error", request_id=request_id, exc=str(exc))
        await query.edit_message_text("❌ Error processing response. Check logs.")


async def poll_and_send_pending_approvals() -> int:
    """
    Fetch all pending approval_requests that have no Telegram message yet
    (telegram_msg_id IS NULL) and send them.

    Returns the number of requests sent.
    """
    try:
        db = await get_supabase()
        result = (
            await db.table("approval_requests")
            .select("*")
            .eq("status", "pending")
            .is_("telegram_msg_id", "null")
            .execute()
        )
        requests = result.data or []
    except Exception as exc:
        logger.error("poll_pending_approvals_failed", exc=str(exc))
        return 0

    sent = 0
    for req in requests:
        msg_id = await send_approval_request(req)
        if msg_id:
            # Store the Telegram message ID so we don't re-send on next poll
            try:
                db = await get_supabase()
                await (
                    db.table("approval_requests")
                    .update({"telegram_msg_id": msg_id})
                    .eq("id", req["id"])
                    .execute()
                )
                sent += 1
            except Exception as exc:
                logger.error("mark_telegram_msg_id_failed", request_id=req["id"], exc=str(exc))

    return sent
