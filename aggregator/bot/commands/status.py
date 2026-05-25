"""/status command handler."""
from __future__ import annotations

from datetime import datetime, timezone
from html import escape as _html_escape
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

from aggregator.bot._authz import is_authorized
from aggregator.delivery.telegram import _chunk


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "never"
    return iso.replace("T", " ").split("+")[0].split(".")[0] + " UTC"


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h or d: parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update, context):
        return
    data = context.bot_data
    storage = data["storage"]
    started_at: datetime = data["started_at"]
    scheduler: Any = data.get("scheduler")

    uptime = (datetime.now(timezone.utc) - started_at).total_seconds()
    # Use HTML markup. Topic names (e.g., crypto_general) contain underscores
    # which legacy Markdown would interpret as italic markers and reject the
    # whole message. HTML doesn't care about underscores.
    lines = [
        "<b>news-aggregator status</b>",
        "",
        f"Uptime: {_fmt_uptime(uptime)}",
        "",
    ]

    for topic_row in storage.list_topics():
        topic = _html_escape(topic_row["name"])
        last = storage.last_run(topic_row["name"])
        if last:
            status_str = _html_escape(str(last.get("status") or "?"))
            lines.append(
                f"Last digest ({topic}): {_fmt_dt(last['finished_at'])} "
                f"{status_str} ({last.get('items_delivered', 0)} items)"
            )
        else:
            lines.append(f"Last digest ({topic}): never")

    if scheduler is not None:
        next_runs = []
        for job in scheduler.get_jobs():
            if job.next_run_time:
                next_runs.append(job.next_run_time)
        if next_runs:
            nxt = min(next_runs).astimezone(timezone.utc).isoformat()
            lines.append(f"Next scheduled run: {_fmt_dt(nxt)}")

    lines.append("")
    lines.append("<b>Source health:</b>")
    for h in storage.all_source_health():
        last_ok = _fmt_dt(h.get("last_success_at"))
        fails = h.get("consecutive_failures", 0)
        status = "ok" if fails == 0 else f"{fails} consecutive fails"
        lines.append(
            f"  {_html_escape(h['source'])}: {_html_escape(status)}  "
            f"last success {last_ok}"
        )

    # Chunk so a status reply over Telegram's 4096-char limit (many topics +
    # accumulated source_health rows) doesn't fail with HTTP 400.
    for chunk in _chunk("\n".join(lines)):
        await update.message.reply_text(chunk, parse_mode="HTML")
