"""PTB Application factory."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from telegram.ext import Application, CommandHandler

from aggregator.bot.commands.help import handle_help
from aggregator.bot.commands.status import handle_status
from aggregator.bot.commands.topics import handle_topics
from aggregator.config import Config


# Single source of truth for registered commands.
# Each entry: (name, one-line description, handler).
# /help renders this list. setMyCommands (Task 8) reads from it.
COMMANDS = [
    ("status", "Bot uptime, last runs, source health",      handle_status),
    ("topics", "List configured topics, schedule, sources", handle_topics),
    ("help",   "List available commands",                   handle_help),
]


def build_application(*, storage, scheduler=None, cfg: Config) -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

    app = Application.builder().token(token).build()
    app.bot_data["storage"] = storage
    app.bot_data["scheduler"] = scheduler
    app.bot_data["cfg"] = cfg
    app.bot_data["authorized_chat_id"] = chat_id
    app.bot_data["started_at"] = datetime.now(timezone.utc)

    for name, _description, handler in COMMANDS:
        app.add_handler(CommandHandler(name, handler))

    return app
