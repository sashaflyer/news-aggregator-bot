"""/topics command handler. Lists configured topics."""
from __future__ import annotations

import json
from html import escape as _html_escape

from telegram import Update
from telegram.ext import ContextTypes

from aggregator.bot._authz import is_authorized
from aggregator.text import chunk_text


async def handle_topics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update, context):
        return
    storage = context.bot_data["storage"]

    lines = ["<b>Configured topics</b>", ""]
    for row in storage.list_topics():
        name = _html_escape(row["name"])
        schedule = _html_escape(row["schedule"])
        try:
            meta = json.loads(row["search_queries"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        kind = _html_escape(str(meta.get("kind") or "?"))
        sources = ", ".join(_html_escape(s) for s in (meta.get("sources") or []))
        lines.append(f"• <b>{name}</b>")
        lines.append(f"  kind: {kind} · schedule: {schedule} · sources: {sources}")
        lines.append("")

    text = "\n".join(lines).rstrip()
    for chunk in chunk_text(text):
        await update.message.reply_text(chunk, parse_mode="HTML")
