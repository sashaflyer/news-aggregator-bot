"""/help command handler. Lists registered commands."""
from __future__ import annotations

from html import escape as _html_escape

from telegram import Update
from telegram.ext import ContextTypes

from aggregator.bot._authz import is_authorized


async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update, context):
        return
    # Local import to avoid a top-level cycle: app.py imports this module.
    from aggregator.bot.app import COMMANDS

    lines = ["<b>Available commands</b>", ""]
    for name, description, _handler in COMMANDS:
        lines.append(f"<b>/{name}</b> — {_html_escape(description)}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")
