"""Telegram delivery. Single function: send_digest(text, topic_id, cfg) -> list[int]."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from aggregator.config import Config

log = logging.getLogger(__name__)

# Telegram's 4096 cap counts UTF-16 code units, not Python str chars: a single
# emoji is 1 char in Python but 2 code units in UTF-16. Budget in UTF-16 so
# emoji-heavy / surrogate-pair-heavy chunks don't slip past the cap.
_TG_HARD_LIMIT_UTF16 = 4096
_SUFFIX_RESERVE = 32  # leaves room for "\n\n<i>(NN/NN)</i>" page counter.
_RETRIES = 3
_BACKOFF_BASE = 2.0
# 4xx responses Telegram won't change its mind on — don't waste retries.
# 401: bad token (rotated/revoked). 403: bot blocked or kicked. 404: bad chat id.
_NON_RETRIABLE_STATUSES = frozenset({401, 403, 404})


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _chunk(text: str, limit: int = _TG_HARD_LIMIT_UTF16 - _SUFFIX_RESERVE) -> list[str]:
    if _utf16_len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while _utf16_len(remaining) > limit:
        # Binary search the largest prefix that fits the UTF-16 budget.
        lo, hi = 0, len(remaining)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if _utf16_len(remaining[:mid]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        cut = lo
        # Walk back to a sensible boundary if one exists nearby.
        for sep in ("\n\n", "\n", ". "):
            idx = remaining.rfind(sep, 0, cut)
            if idx > 0 and idx > cut - 200:
                cut = idx + len(sep)
                break
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    total = len(chunks)
    # HTML mode renders `_..._` as literal underscores. Use <i>...</i> so the
    # page counter italicizes correctly under the configured parse_mode.
    return [f"{c}\n\n<i>({i + 1}/{total})</i>" if total > 1 else c
            for i, c in enumerate(chunks)]


def _is_parse_error(resp: httpx.Response) -> bool:
    """Detect 'Bad Request: can't parse entities' — LLM emitted malformed Markdown."""
    if resp.status_code != 400:
        return False
    try:
        desc = (resp.json().get("description") or "").lower()
    except Exception:  # noqa: BLE001
        return False
    return "can't parse entities" in desc or "cant parse entities" in desc


async def _post(client: httpx.AsyncClient, token: str, payload: dict[str, Any]) -> httpx.Response:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    return await client.post(url, json=payload, timeout=20.0)


async def _send_one(client: httpx.AsyncClient, token: str, chat_id: str,
                    text: str, parse_mode: str) -> int | None:
    """Send one chunk. If Telegram rejects our Markdown parse, fall back to
    plain text once for the same chunk so the user still gets the message."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = await _post(client, token, payload)
            if resp.status_code == 200 and resp.json().get("ok"):
                return resp.json()["result"]["message_id"]

            # Markdown rejection — one-shot retry as plain text so the chunk
            # still goes out. We do not loop here; either the plain-text send
            # works or we move on.
            if _is_parse_error(resp) and payload.get("parse_mode"):
                log.warning("telegram rejected %s formatting; retrying chunk as plain text",
                            payload["parse_mode"])
                fallback = dict(payload)
                # Telegram requires the field to be ABSENT for plain text;
                # sending `parse_mode: null` gets rejected as "unsupported".
                fallback.pop("parse_mode", None)
                resp2 = await _post(client, token, fallback)
                if resp2.status_code == 200 and resp2.json().get("ok"):
                    return resp2.json()["result"]["message_id"]
                log.warning("telegram plain-text fallback also failed: %s %s",
                            resp2.status_code, resp2.text[:200])
                return None

            # 429: Telegram tells us exactly how long to wait via
            # parameters.retry_after. A fixed exponential backoff can be
            # shorter than that wait, which just extends the ban.
            if resp.status_code == 429:
                try:
                    retry_after = float(
                        resp.json().get("parameters", {}).get("retry_after", 0)
                    )
                except (ValueError, KeyError, AttributeError):
                    retry_after = 0
                delay = retry_after if retry_after > 0 else _BACKOFF_BASE ** attempt
                log.warning("telegram 429; sleeping %.1fs", delay)
                if attempt < _RETRIES:
                    await asyncio.sleep(delay)
                continue

            log.warning("telegram send returned %s: %s", resp.status_code, resp.text[:200])
            if resp.status_code in _NON_RETRIABLE_STATUSES:
                # Bot blocked, token revoked, or chat gone — retrying with
                # backoff just wastes seconds and pollutes logs every run.
                return None
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
