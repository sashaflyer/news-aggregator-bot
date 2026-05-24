"""LLM synthesis. One function: synthesize(topic_id, items, cfg) -> str."""
from __future__ import annotations

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


def _query_for_topic(topic_id: str, cfg: Config) -> str:
    """Single string used by the snippet extractor to score window relevance."""
    if topic_id == "crypto_watchlist":
        return " ".join(cfg.crypto_watchlist.symbols)
    return "crypto"


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        _client = OpenAI(api_key=key)
    return _client


def _build_prompt(topic_id: str, items: list[dict[str, Any]], cfg: Config) -> str:
    items_json = json.dumps(items, ensure_ascii=False, indent=2)
    if topic_id == "crypto_general":
        template = load_prompt("general_crypto.md")
        return template.format(n_items=len(items), items_json=items_json)
    if topic_id == "crypto_watchlist":
        template = load_prompt("watchlist.md")
        return template.format(
            symbols=", ".join(cfg.crypto_watchlist.symbols),
            items_json=items_json,
        )
    raise ValueError(f"unknown topic_id: {topic_id!r}")


def synthesize(topic_id: str, items: list[dict[str, Any]], *, cfg: Config) -> str:
    capped = items[: cfg.synth.max_input_items]
    query = _query_for_topic(topic_id, cfg)
    shortened = [_shorten_body(it, query) for it in capped]
    prompt = _build_prompt(topic_id, shortened, cfg)
    log.info("synth topic=%s items=%d prompt_chars=%d",
             topic_id, len(capped), len(prompt))

    client = _get_client()
    resp = client.chat.completions.create(
        model=cfg.synth.model,
        messages=[{"role": "user", "content": prompt}],
        max_completion_tokens=cfg.synth.max_output_tokens,
        temperature=0.3,
    )
    text = resp.choices[0].message.content or ""
    log.info("synth done tokens=%s/%s/%s",
             resp.usage.prompt_tokens, resp.usage.completion_tokens, resp.usage.total_tokens)
    return text
