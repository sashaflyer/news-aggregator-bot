"""PTB Application factory."""
from __future__ import annotations

import os
from datetime import datetime, timezone

from telegram.ext import Application, CommandHandler, filters

from aggregator.bot.commands.digest import handle_digest
from aggregator.bot.commands.help import handle_help
from aggregator.bot.commands.status import handle_status
from aggregator.bot.commands.topics import handle_topics
from aggregator.config import Config


# Single source of truth for registered commands.
# Each entry: (name, one-line description, handler).
# /help renders this list. setMyCommands (Task 8) reads from it.
COMMANDS = [
    ("status", "Bot uptime, last runs, source health",      handle_status),
    ("digest", "Run a digest now: /digest <topic_id>",      handle_digest),
    ("topics", "List configured topics, schedule, sources", handle_topics),
    ("help",   "List available commands",                   handle_help),
]


def build_application(*, storage, scheduler=None, cfg: Config) -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

    # Explicit HTTPXRequest timeouts. PTB's defaults (5s connect/read/write,
    # 1s pool) are conservative for transient network issues. We widen modestly
    # so a single Telegram-side hiccup surfaces as a real error inside our retry
    # loop instead of being clipped before the server can respond. get_updates
    # gets a longer read window because it uses long polling.
    #
    # Defense in depth only — the structural recovery for "alive but mute" is
    # the systemd watchdog wired up in aggregator/__main__.py. See the
    # 2026-05-28 incident notes in deploy/README.md.
    app = (
        Application.builder()
        .token(token)
        .connect_timeout(10.0)
        .read_timeout(30.0)
        .write_timeout(20.0)
        .pool_timeout(5.0)
        .get_updates_connect_timeout(10.0)
        .get_updates_read_timeout(30.0)
        .get_updates_write_timeout(20.0)
        .get_updates_pool_timeout(5.0)
        .build()
    )
    app.bot_data["storage"] = storage
    app.bot_data["scheduler"] = scheduler
    app.bot_data["cfg"] = cfg
    app.bot_data["authorized_chat_id"] = chat_id
    app.bot_data["started_at"] = datetime.now(timezone.utc)

    # Dispatcher-level chat filter: an Update from any other chat never reaches
    # a handler. Belt-and-braces with is_authorized() inside handlers, but this
    # makes authz unbypassable if someone adds a new command and forgets the
    # in-handler check.
    chat_filter = filters.Chat(chat_id=chat_id)
    for name, _description, handler in COMMANDS:
        app.add_handler(CommandHandler(name, handler, filters=chat_filter))

    return app


async def publish_commands(bot) -> None:
    """Tell Telegram about our command set so the `/` menu shows autocomplete.

    Best-effort: a failure here (network, bad token, Telegram outage) is
    logged but does not block startup — the bot still polls and handles
    commands; only the client-side menu is missing.
    """
    import logging
    from telegram import BotCommand

    log = logging.getLogger(__name__)
    cmds = [BotCommand(name, description) for name, description, _ in COMMANDS]
    try:
        await bot.set_my_commands(cmds)
        log.info("published %d commands to Telegram", len(cmds))
    except Exception:
        log.exception("set_my_commands failed; continuing without menu")
