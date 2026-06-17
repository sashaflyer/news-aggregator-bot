"""/start command handler."""
from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from aggregator.bot._authz import is_authorized


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update, context):
        return
    await update.message.reply_text(
        "BriefBot is running. Type /help for available commands.",
        parse_mode="HTML",
    )
