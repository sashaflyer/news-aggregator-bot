"""Telegram delivery. Single function: send_digest(text, topic_id, cfg) -> list[int]."""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import httpx

from aggregator.config import Config
from aggregator.delivery._html_filter import ALLOWED_TAGS, HTML_ENTITY_RE, sanitize_outgoing

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


def _find_safe_cut(text: str, hard_cut: int) -> int:
    """Return a cut position in (0, hard_cut] that doesn't split an HTML entity
    or an unclosed tag. Prefers the last </tag> in the window, then a paragraph
    / line / sentence break. Returns hard_cut unchanged as a last resort.
    """
    if hard_cut >= len(text):
        return len(text)
    if hard_cut <= 0:
        return hard_cut

    # 1) Avoid splitting an HTML entity (e.g. &amp; / &#x27;) at the boundary.
    for end in range(hard_cut, max(hard_cut - 32, 0), -1):
        if text[end - 1] != ";":
            continue
        amp = text.rfind("&", 0, end)
        if amp == -1:
            continue
        if HTML_ENTITY_RE.fullmatch(text[amp:end]):
            continue
        return end

    # 2) Prefer the last </tag> in the window — keeps every chunk well-formed.
    best: int | None = None
    for tag in ALLOWED_TAGS:
        close = text.rfind(f"</{tag}>", 0, hard_cut)
        if close == -1:
            continue
        candidate = close + len(f"</{tag}>")
        if best is None or candidate > best:
            best = candidate
    if best is not None and best > 0:
        return best

    # 3) Fall back to natural separators, then to hard_cut.
    for sep in ("\n\n", "\n", ". "):
        idx = text.rfind(sep, 0, hard_cut)
        if idx > 0:
            return idx + len(sep)
    return hard_cut


def _chunk_body(text: str, limit: int = _TG_HARD_LIMIT_UTF16 - _SUFFIX_RESERVE) -> list[str]:
    """Split `text` into chunks that each fit `limit` UTF-16 code units.

    Returns *bodies only* — page counters are appended at send time so the
    plain-text fallback (which strips parse_mode) doesn't render `<i>` as
    literal text. Each chunk is HTML-safe by construction: cuts never split
    an entity or an unclosed tag, so the downstream ``sanitize_outgoing``
    can't silently delete text.
    """
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
        cut = _find_safe_cut(remaining, lo)
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _page_counter(idx: int, total: int, parse_mode: str) -> str:
    """Render the (N/M) page counter for a chunk. Empty when single-chunk."""
    if total <= 1:
        return ""
    if parse_mode == "HTML":
        return f"\n\n<i>({idx + 1}/{total})</i>"
    return f"\n\n({idx + 1}/{total})"


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
    plain text once for the same chunk so the user still gets the message.

    `text` is the *full* chunk body including any page-counter suffix already
    attached by `send_digest`; the parse_mode=fallback path receives a counter
    rendered for the active parse mode, so we never emit literal `<i>` in
    plain-text delivery.
    """
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
                fallback["text"] = re.sub(
                    r"\s*<i>\((\d+)/(\d+)\)</i>\s*$",
                    lambda m: f" ({m.group(1)}/{m.group(2)})",
                    fallback["text"],
                )
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

    # Whitelist outgoing HTML so a prompt-injected anchor in LLM output can't
    # smuggle a phishing href through our trusted bot. No-op under non-HTML
    # parse modes (the LLM output for those should not contain HTML anyway).
    if cfg.telegram.parse_mode == "HTML":
        text = sanitize_outgoing(text)

    # Split into bodies first; the page counter is added per-send so a
    # parse_mode=plain-text fallback can render it without literal HTML tags.
    bodies = _chunk_body(text)
    total = len(bodies)
    ids: list[int] = []
    async with httpx.AsyncClient() as client:
        for i, body in enumerate(bodies):
            # Pre-render for the configured parse mode. On fallback, _send_one
            # strips the <i>...</i> suffix and re-emits a plain-text counter.
            chunk = body + _page_counter(i, total, cfg.telegram.parse_mode)
            mid = await _send_one(client, token, chat_id, chunk, cfg.telegram.parse_mode)
            if mid is not None:
                ids.append(mid)
            else:
                log.error("telegram: chunk %d/%d failed permanently; aborting send "
                          "(dropped %d remaining chunk(s), first %d chars: %r)",
                          i + 1, total, total - i - 1,
                          len(body), body[:200])
                break
    return ids
