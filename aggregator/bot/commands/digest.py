"""/digest <topic_id> command handler. Runs a real digest on demand."""
from __future__ import annotations

import asyncio
import logging
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from aggregator.bot._authz import is_authorized
from aggregator.bot.digest_lock import lock_for
from aggregator.pipeline import run_digest

log = logging.getLogger(__name__)

_COOLDOWN_SECONDS = 300
_last_run: dict[str, float] = {}


async def _typing_loop(bot, chat_id: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:
            pass
        try:
            await asyncio.sleep(4)
        except asyncio.CancelledError:
            break


async def handle_digest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update, context):
        return

    cfg = context.bot_data["cfg"]
    storage = context.bot_data["storage"]
    args = list(context.args or [])

    if not args:
        topics_list = ", ".join(sorted(cfg.topics.keys()))
        await update.message.reply_text(
            f"Usage: /digest <topic_id>\nConfigured topics: {topics_list}"
        )
        return
    if args[0] not in cfg.topics:
        topics_list = ", ".join(sorted(cfg.topics.keys()))
        await update.message.reply_text(
            f"Unknown topic: {args[0]}\nConfigured topics: {topics_list}"
        )
        return

    topic_id = args[0]

    now = time.monotonic()
    last = _last_run.get(topic_id, 0.0)
    remaining = _COOLDOWN_SECONDS - (now - last)
    if remaining > 0:
        await update.message.reply_text(
            f"Digest for {topic_id} was run recently. "
            f"Try again in {int(remaining)}s."
        )
        return

    lock = lock_for(topic_id)
    try:
        await asyncio.wait_for(lock.acquire(), timeout=0.001)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        await update.message.reply_text(
            f"Digest for {topic_id} is already running. Try again shortly."
        )
        return

    try:
        await update.message.reply_text(f"Running digest for {topic_id}…")
        typing_stop = asyncio.Event()
        typing_task = asyncio.create_task(
            _typing_loop(context.bot, update.effective_chat.id, typing_stop)
        )
        await run_digest(topic_id, cfg, storage, trigger="manual")
        _last_run[topic_id] = time.monotonic()
    except Exception:
        log.exception("/digest for %s failed", topic_id)
        await update.message.reply_text(
            "Digest failed. Check server logs for details."
        )
    finally:
        typing_stop.set()
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass
        lock.release()
