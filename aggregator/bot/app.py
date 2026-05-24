"""PTB Application factory."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from telegram.ext import Application, CommandHandler

from aggregator.bot.commands.status import handle_status


def build_application(*, storage, scheduler=None) -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

    app = Application.builder().token(token).build()
    app.bot_data["storage"] = storage
    app.bot_data["scheduler"] = scheduler
    app.bot_data["authorized_chat_id"] = chat_id
    app.bot_data["started_at"] = datetime.now(timezone.utc)

    app.add_handler(CommandHandler("status", handle_status))
    # Add new commands here: app.add_handler(CommandHandler("<name>", handle_<name>))

    return app
