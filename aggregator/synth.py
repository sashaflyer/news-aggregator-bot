"""LLM synthesis. One function: synthesize(topic_id, items, cfg) -> str."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from typing import Any

from openai import OpenAI

from aggregator.config import Config
from aggregator.prompts import load as load_prompt
from aggregator.vendor.last30days import schema as _schema
from aggregator.vendor.last30days import snippet as _snippet

log = logging.getLogger(__name__)

_client: OpenAI | None = None

# Max words per item body before sending to the LLM. Anything longer is
# trimmed to a query-relevant window via the upstream snippet extractor.
_MAX_BODY_WORDS = 120


def _shorten_body(item_dict: dict[str, Any], query: str) -> dict[str, Any]:
    """Return a copy of `item_dict` with `text` replaced by a snippet if long.

    Items whose body already fits under _MAX_BODY_WORDS are returned unchanged
    (cheap no-op). Longer bodies are wrapped in a SourceItem just long enough
    to call upstream `extract_best_snippet`, which returns a query-aligned window.
    """
    body = item_dict.get("text") or ""
    if len(body.split()) <= _MAX_BODY_WORDS:
        return item_dict
    si = _schema.SourceItem(
        item_id=str(item_dict.get("id", "")),
        source=str(item_dict.get("source", "")),
        title=str(item_dict.get("title", "")),
        body=body,
        url=str(item_dict.get("url", "")),
    )
    short = _snippet.extract_best_snippet(si, query, max_words=_MAX_BODY_WORDS)
    out = dict(item_dict)
    out["text"] = short
    return out


def _sanitize_for_html(item_dict: dict[str, Any]) -> dict[str, Any]:
    """HTML-escape user-controlled string fields before they reach the LLM.

    The digest is sent with ``parse_mode="HTML"``. If a Reddit/HN/Polymarket
    title contains raw ``<a href="https://evil">click</a>``, the LLM might
    pass it through verbatim — injecting attacker-chosen links into the
    trusted digest. Escaping at the prompt boundary means even a
    prompt-injected title can only produce literal text in the output.

    URLs are not escaped — they need to remain valid hrefs; Telegram itself
    blocks ``javascript:``/``data:`` schemes.
    """
    out = dict(item_dict)
    for key in ("title", "text"):
        val = out.get(key)
        if isinstance(val, str) and val:
            out[key] = html.escape(val, quote=True)
    return out


def _query_for_topic(topic_id: str, cfg: Config) -> str:
    """Single string used by the snippet extractor to score window relevance.

    For watchlist topics, use the full ticker+alias union so snippet windows
    catch either form. For general topics, prefer hn_keywords ->
    polymarket_tags -> a generic fallback.
    """
    topic = cfg.topics[topic_id]
    if topic.kind == "watchlist":
        return " ".join(topic.query_symbols)
    if topic.hn_keywords:
        return " ".join(topic.hn_keywords)
    if topic.polymarket_tags:
        return " ".join(topic.polymarket_tags)
    return topic_id


def _format_watch_symbols(topic) -> str:
    """Render the watch list as canonical tickers with parenthesized aliases.

    Example: ``SOL (also: Solana), SUI (also: Sui Network), AVAX``. Lets the
    prompt show one canonical bucket per coin while telling the LLM which
    alternate strings in item titles still count toward that bucket.
    """
    parts: list[str] = []
    for w in topic.watch:
        if w.aliases:
            parts.append(f"{w.ticker} (also: {', '.join(w.aliases)})")
        else:
            parts.append(w.ticker)
    return ", ".join(parts)


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        # SDK default timeout is 600s and a hung TCP would otherwise pin the
        # event-loop thread for 10 minutes; cap at 60s with 2 SDK-level retries.
        _client = OpenAI(api_key=key, timeout=60.0, max_retries=2)
    return _client


def _build_messages(topic_id: str, items: list[dict[str, Any]], cfg: Config) -> list[dict[str, str]]:
    """Return the system + user message pair for the OpenAI Chat API.

    The system message is the (static) template — role, format spec, worked
    example, and shared rules. The user message carries the per-run payload:
    items JSON, plus the SYMBOLS line for watchlist topics. Splitting this way
    lets the API cache the system prefix across runs.
    """
    topic = cfg.topics[topic_id]
    system = load_prompt(topic.prompt_template)
    items_json = json.dumps(items, ensure_ascii=False, indent=2)
    if topic.kind == "watchlist":
        user = f"SYMBOLS: {_format_watch_symbols(topic)}\n\nITEMS (JSON):\n{items_json}"
    else:
        user = f"ITEMS (JSON):\n{items_json}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def synthesize(topic_id: str, items: list[dict[str, Any]], *, cfg: Config) -> str:
    """Blocking variant. Prefer ``synthesize_async`` from coroutines."""
    capped = items[: cfg.synth.max_input_items]
    query = _query_for_topic(topic_id, cfg)
    shortened = [_shorten_body(it, query) for it in capped]
    sanitized = [_sanitize_for_html(it) for it in shortened]
    messages = _build_messages(topic_id, sanitized, cfg)
    total_chars = sum(len(m["content"]) for m in messages)
    log.info("synth topic=%s items=%d prompt_chars=%d",
             topic_id, len(capped), total_chars)

    client = _get_client()
    resp = client.chat.completions.create(
        model=cfg.synth.model,
        messages=messages,
        max_completion_tokens=cfg.synth.max_output_tokens,
        reasoning_effort="medium",
    )
    choice = resp.choices[0]

    # Truncation produces partial HTML (missing closing tag on the final
    # bullet); Telegram rejects the whole message. Surface this as a loud
    # error instead of silently delivering malformed output.
    if getattr(choice, "finish_reason", None) == "length":
        raise RuntimeError(
            f"LLM hit max_completion_tokens={cfg.synth.max_output_tokens}; "
            "raise synth.max_output_tokens or shorten the prompt"
        )

    text = (choice.message.content or "").strip()

    # Some proxies/models return ``usage = None``; don't crash after a
    # successful call just because the metrics aren't present.
    usage = getattr(resp, "usage", None)
    if usage is not None:
        log.info("synth done tokens=%s/%s/%s",
                 getattr(usage, "prompt_tokens", "?"),
                 getattr(usage, "completion_tokens", "?"),
                 getattr(usage, "total_tokens", "?"))
    else:
        log.info("synth done (usage unavailable)")

    # Empty content (refusal, max_completion_tokens truncation, content
    # filter) must be a hard error — otherwise the caller would mark every
    # ranked item as ``delivered`` for a blank message and lose them forever.
    if not text:
        raise RuntimeError("LLM returned empty content")
    return text


async def synthesize_async(topic_id: str, items: list[dict[str, Any]], *, cfg: Config) -> str:
    """Async wrapper. The OpenAI sync SDK call would otherwise block the
    event loop for the duration of the LLM call, stalling the bot poller
    and other scheduled topics.
    """
    return await asyncio.to_thread(synthesize, topic_id, items, cfg=cfg)
