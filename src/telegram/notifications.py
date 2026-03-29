"""
Send Telegram messages to specific team members by role.
All functions are fire-and-forget; errors are logged but never raised.
"""

from __future__ import annotations

import traceback

from telegram import Bot
from telegram.constants import ParseMode

from src.config.settings import settings
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Role → chat ID mapping
ROLE_CHAT_IDS: dict[str, str] = {
    "rami": settings.TELEGRAM_RAMI_CHAT_ID,
    "father": settings.TELEGRAM_FATHER_CHAT_ID,
    "maree": settings.TELEGRAM_MAREE_CHAT_ID,
}


def _get_bot() -> Bot:
    return Bot(token=settings.TELEGRAM_BOT_TOKEN)


async def send_to_role(
    role: str,
    text: str,
    parse_mode: str = ParseMode.MARKDOWN,
    reply_markup=None,
) -> bool:
    """
    Send a message to a team member by role name ("rami", "father", "maree").

    Returns True on success, False on failure.
    """
    chat_id = ROLE_CHAT_IDS.get(role.lower())
    if not chat_id:
        logger.error("unknown_role", role=role)
        return False

    return await send_to_chat(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)


async def send_to_chat(
    chat_id: str,
    text: str,
    parse_mode: str = ParseMode.MARKDOWN,
    reply_markup=None,
) -> bool:
    """Send a message to a specific chat ID. Returns True on success."""
    try:
        async with _get_bot() as bot:
            await bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        return True
    except Exception:
        logger.error(
            "telegram_send_failed",
            chat_id=chat_id,
            exc=traceback.format_exc(),
        )
        return False


async def send_alert(
    text: str,
    roles: list[str] | None = None,
    parse_mode: str = ParseMode.MARKDOWN,
) -> None:
    """
    Broadcast an alert to one or more roles (default: rami only).
    """
    targets = roles or ["rami"]
    for role in targets:
        await send_to_role(role, text, parse_mode=parse_mode)
