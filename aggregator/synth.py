"""LLM synthesis. One function: synthesize(topic_id, items, cfg) -> str."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

from aggregator.config import Config
from aggregator.prompts import load as load_prompt

log = logging.getLogger(__name__)

_client: OpenAI | None = None


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
    prompt = _build_prompt(topic_id, capped, cfg)
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
