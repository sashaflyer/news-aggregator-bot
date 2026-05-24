"""Telegram delivery. Single function: send_digest(text, topic_id, cfg) -> list[int]."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from aggregator.config import Config

log = logging.getLogger(__name__)

_MAX_CHARS = 4000
_RETRIES = 3
_BACKOFF_BASE = 2.0


def _chunk(text: str, limit: int = _MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    total = len(chunks)
    return [f"{c}\n\n_({i + 1}/{total})_" if total > 1 else c
            for i, c in enumerate(chunks)]


async def _send_one(client: httpx.AsyncClient, token: str, chat_id: str,
                    text: str, parse_mode: str) -> int | None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = await client.post(url, json=payload, timeout=20.0)
            if resp.status_code == 200 and resp.json().get("ok"):
                return resp.json()["result"]["message_id"]
            log.warning("telegram send returned %s: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as e:
            log.warning("telegram send error attempt=%d: %s", attempt, e)
        if attempt < _RETRIES:
            await asyncio.sleep(_BACKOFF_BASE ** attempt)
    return None


async def send_digest(text: str, *, topic_id: str, cfg: Config) -> list[int]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping send for %s",
                  topic_id)
        return []

    chunks = _chunk(text)
    ids: list[int] = []
    async with httpx.AsyncClient() as client:
        for chunk in chunks:
            mid = await _send_one(client, token, chat_id, chunk, cfg.telegram.parse_mode)
            if mid is not None:
                ids.append(mid)
            else:
                break
    return ids
