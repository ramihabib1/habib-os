"""
Telegram bot — polling loop with command handlers.

Commands:
  /start    — welcome message
  /status   — system health (scheduler + last sync times)
  /inventory — current FBA stock summary
  /pending  — list pending approval requests
  /approve <id> — approve a request by ID
  /reject <id>  — reject a request by ID

Run as a standalone process via PM2 (see ecosystem.config.js).
"""

from __future__ import annotations

import asyncio
import uuid as _uuid_mod

from telegram import BotCommand, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from src.config.settings import settings
from src.telegram.approval import handle_callback, poll_and_send_pending_approvals
from src.utils.audit import log_action
from src.utils.logging import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

# All authorized chat IDs (for data read commands)
_AUTHORIZED_IDS = {
    settings.TELEGRAM_RAMI_CHAT_ID,
    settings.TELEGRAM_FATHER_CHAT_ID,
    settings.TELEGRAM_MAREE_CHAT_ID,
}


def _is_authorized(update: Update) -> bool:
    """Return True if the sender is an authorized team member."""
    return str(update.effective_user.id) in _AUTHORIZED_IDS


def _parse_uuid_arg(args: list[str]) -> str | None:
    """Validate and return a UUID string from command args, or None if invalid."""
    if not args:
        return None
    candidate = args[0]
    try:
        _uuid_mod.UUID(candidate)
        return candidate
    except ValueError:
        return None


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Habib Distribution OS* is online.\n\n"
        "Commands:\n"
        "/status — system health\n"
        "/inventory — FBA stock\n"
        "/pending — pending approvals\n"
        "/approve <id> — approve action\n"
        "/reject <id> — reject action",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show last sync times from sync_log."""
    if not _is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    from src.config.supabase_client import get_supabase

    try:
        db = await get_supabase()
        result = await (
            db.table("sync_log")
            .select("sync_type, status, completed_at, records_synced")
            .order("completed_at", desc=True)
            .limit(10)
            .execute()
        )
        rows = result.data or []
    except Exception as exc:
        logger.error("cmd_status_error", exc=str(exc))
        await update.message.reply_text("❌ Something went wrong. Rami has been notified.")
        return

    if not rows:
        await update.message.reply_text("No sync history yet.")
        return

    lines = ["*📊 Last Sync Status*", ""]
    for row in rows:
        ts = (row.get("completed_at") or "")[:16]
        status_icon = "✅" if row.get("status") == "success" else "❌"
        lines.append(
            f"{status_icon} `{row['sync_type']}` — {row.get('records_synced', 0)} records @ {ts}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current FBA + warehouse stock from v_current_inventory."""
    if not _is_authorized(update):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    from src.config.supabase_client import get_supabase

    try:
        db = await get_supabase()
        result = await db.table("v_current_inventory").select("*").execute()
        rows = result.data or []
    except Exception as exc:
        logger.error("cmd_inventory_error", exc=str(exc))
        await update.message.reply_text("❌ Something went wrong. Rami has been notified.")
        return

    if not rows:
        await update.message.reply_text("No inventory data yet.")
        return

    lines = ["*📦 Current Inventory*", ""]
    low_stock = []
    for row in rows:
        fba = row.get("fba_fulfillable_qty", 0) or 0
        wh = row.get("warehouse_qty", 0) or 0
        name = row.get("product_name", row.get("sku", "?"))[:30]
        if fba < 10:
            low_stock.append(f"⚠️ `{name}` — FBA: {fba}, WH: {wh}")
        else:
            lines.append(f"• `{name}` — FBA: {fba}, WH: {wh}")

    if low_stock:
        lines = ["*📦 Current Inventory*", "", "*🚨 Low Stock:*"] + low_stock + ["", "*Other:*"] + lines[2:]

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n…(truncated)"

    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all pending approval requests."""
    from src.config.supabase_client import get_supabase

    # Only Rami sees this
    if str(update.effective_user.id) != settings.TELEGRAM_RAMI_CHAT_ID:
        await update.message.reply_text("⛔ Only Rami can view pending approvals.")
        return

    try:
        db = await get_supabase()
        result = await (
            db.table("approval_requests")
            .select("id, action_type, agent, description, requested_at")
            .eq("status", "pending")
            .order("requested_at", desc=False)
            .execute()
        )
        rows = result.data or []
    except Exception as exc:
        logger.error("cmd_pending_error", exc=str(exc))
        await update.message.reply_text("❌ Something went wrong. Rami has been notified.")
        return

    if not rows:
        await update.message.reply_text("✅ No pending approvals.")
        return

    lines = [f"*⏳ Pending Approvals ({len(rows)})*", ""]
    for row in rows:
        short_id = str(row["id"])[:8]
        ts = (row.get("requested_at") or "")[:16]
        lines.append(
            f"• `{short_id}` — {row['action_type']} ({row.get('agent', '?')}) @ {ts}"
        )
    lines.append("\nUse /approve <id> or /reject <id>")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve a pending request: /approve <id>"""
    if str(update.effective_user.id) != settings.TELEGRAM_RAMI_CHAT_ID:
        await update.message.reply_text("⛔ Only Rami can approve actions.")
        return

    request_id = _parse_uuid_arg(context.args)
    if not request_id:
        await update.message.reply_text("Usage: /approve <request_id> (full UUID required)")
        return

    await _set_approval_status(update, request_id, "approved")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reject a pending request: /reject <id>"""
    if str(update.effective_user.id) != settings.TELEGRAM_RAMI_CHAT_ID:
        await update.message.reply_text("⛔ Only Rami can reject actions.")
        return

    request_id = _parse_uuid_arg(context.args)
    if not request_id:
        await update.message.reply_text("Usage: /reject <request_id> (full UUID required)")
        return

    await _set_approval_status(update, request_id, "rejected")


async def _set_approval_status(update: Update, request_id: str, status: str) -> None:
    from datetime import datetime, timezone
    from src.config.supabase_client import get_supabase

    try:
        db = await get_supabase()
        result = await (
            db.table("approval_requests")
            .update({
                "status": status,
                "approved_by": str(update.effective_user.id),
                "responded_at": datetime.now(timezone.utc).isoformat(),
            })
            .eq("status", "pending")
            .eq("id", request_id)
            .execute()
        )
        if result.data:
            emoji = "✅" if status == "approved" else "❌"
            await update.message.reply_text(f"{emoji} Request {request_id} {status}.")
            await log_action(
                agent="telegram_bot",
                action=f"approval_{status}",
                entity_type="approval_requests",
                entity_id=request_id,
                details={"via": "command"},
            )
        else:
            await update.message.reply_text(
                f"⚠️ Request `{request_id}` not found or already processed."
            )
    except Exception as exc:
        logger.error("set_approval_status_error", request_id=request_id, exc=str(exc))
        await update.message.reply_text("❌ Something went wrong. Rami has been notified.")


# ── Background approval poller ────────────────────────────────────────────────

async def approval_poll_loop(app: Application) -> None:
    """Poll for unsent pending approvals every 30 seconds."""
    while True:
        try:
            sent = await poll_and_send_pending_approvals()
            if sent:
                logger.info("approval_poll_sent", count=sent)
        except Exception as exc:
            logger.error("approval_poll_error", exc=str(exc))
        await asyncio.sleep(30)


# ── Entry point ───────────────────────────────────────────────────────────────

async def main() -> None:
    app = (
        Application.builder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .build()
    )

    # Register commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("inventory", cmd_inventory))
    app.add_handler(CommandHandler("pending", cmd_pending))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))

    # Inline button callbacks for approval messages
    app.add_handler(CallbackQueryHandler(handle_callback))

    # Set bot command menu in Telegram
    await app.bot.set_my_commands([
        BotCommand("start", "Welcome"),
        BotCommand("status", "System health"),
        BotCommand("inventory", "FBA stock summary"),
        BotCommand("pending", "Pending approvals"),
        BotCommand("approve", "Approve action"),
        BotCommand("reject", "Reject action"),
    ])

    logger.info("telegram_bot_starting")

    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)

        # Start background approval poller
        asyncio.create_task(approval_poll_loop(app))

        # Run until interrupted
        await asyncio.Event().wait()

        await app.updater.stop()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
