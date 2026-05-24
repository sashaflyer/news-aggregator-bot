"""Pipeline orchestration. One callable: run_digest(topic_id, cfg, storage, trigger)."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aggregator.config import Config
from aggregator.sources.base import Item, Source
from aggregator.sources.polymarket import PolymarketSource
from aggregator.sources.reddit import RedditSource
from aggregator.storage import Storage
from aggregator.synth import synthesize
from aggregator.delivery.telegram import send_digest
from aggregator.vendor.last30days import dedupe as _dedupe
from aggregator.vendor.last30days import schema as _schema

log = logging.getLogger(__name__)

SOURCES: dict[str, Source] = {
    "reddit": RedditSource(),
    "polymarket": PolymarketSource(),
}


@dataclass
class RunResult:
    run_id: int
    status: str       # "ok" | "partial" | "error"
    items_fetched: int
    items_delivered: int


async def _fetch_all(queries: dict[str, Any]) -> dict[str, list[Item] | Exception]:
    async def safe(name: str, src: Source):
        try:
            return name, await src.fetch(queries)
        except Exception as e:
            log.exception("source %s failed", name)
            return name, e
    pairs = await asyncio.gather(*(safe(n, s) for n, s in SOURCES.items()))
    return dict(pairs)


def _item_to_source_item(item: Item) -> "_schema.SourceItem":
    """Convert our Item -> upstream SourceItem just enough for dedupe."""
    return _schema.SourceItem(
        item_id=item.id,
        source=item.source,
        title=item.title,
        body=item.text,
        url=item.url,
        author=str(item.metadata.get("author") or "") or None,
        container=str(item.metadata.get("subreddit") or "") or None,
    )


def _engagement_score(item: Item) -> float:
    """Single sortable engagement number; same weights as v1."""
    return (
        item.engagement_raw.get("upvotes", 0)
        + item.engagement_raw.get("score", 0)
        + 0.1 * item.engagement_raw.get("comments", 0)
        + 0.001 * (item.engagement_raw.get("volume") or 0)
    )


def _score_and_dedup(items: list[Item], *, top_n: int, per_author_cap: int) -> list[Item]:
    """Dedupe near-duplicates via upstream Jaccard-based dedupe_items, then
    sort by engagement, then truncate to top_n.

    Args:
        per_author_cap: Reserved for a future weighted_rrf wiring; currently
            unused (per-author cap not yet enforced).
    """
    if not items:
        return []

    by_id = {item.id: item for item in items}
    source_items = [_item_to_source_item(it) for it in items]
    try:
        deduped = _dedupe.dedupe_items(source_items, threshold=0.7)
    except Exception:
        log.exception("upstream dedupe_items failed; falling back to raw items")
        deduped = source_items

    deduped_items = [by_id[si.item_id] for si in deduped if si.item_id in by_id]
    log.info("dedupe: %d -> %d items", len(items), len(deduped_items))

    ranked = sorted(deduped_items, key=_engagement_score, reverse=True)
    return ranked[:top_n]


async def run_digest(topic_id: str, cfg: Config, storage: Storage, *,
                     trigger: str = "scheduled") -> RunResult:
    now = datetime.now(timezone.utc)
    run_id = storage.start_run(topic_id, trigger=trigger, at=now)
    log.info("run %s started for topic %s (trigger=%s)", run_id, topic_id, trigger)

    topic = next((t for t in storage.list_topics() if t["name"] == topic_id), None)
    if topic is None:
        msg = f"topic {topic_id!r} not found in DB"
        storage.finish_run(run_id, status="error", items_fetched=0, items_delivered=0,
                           error_message=msg, at=datetime.now(timezone.utc))
        return RunResult(run_id, "error", 0, 0)

    queries = json.loads(topic["search_queries"])

    per_source = await _fetch_all(queries)
    items: list[Item] = []
    ok_count = 0
    fail_count = 0
    for name, result in per_source.items():
        attempt_at = datetime.now(timezone.utc)
        if isinstance(result, Exception):
            storage.record_source_failure(name, str(result), at=attempt_at)
            fail_count += 1
        else:
            storage.record_source_success(name, at=attempt_at)
            items.extend(result)
            ok_count += 1

    fetched = len(items)
    if ok_count == 0:
        storage.finish_run(run_id, status="error", items_fetched=0, items_delivered=0,
                           error_message="all sources failed",
                           at=datetime.now(timezone.utc))
        return RunResult(run_id, "error", 0, 0)

    top_n = (cfg.crypto_general.top_n if topic_id == "crypto_general"
             else cfg.crypto_watchlist.per_symbol_top_n * len(cfg.crypto_watchlist.symbols))

    ranked = _score_and_dedup(items, top_n=top_n, per_author_cap=cfg.scoring.per_author_cap)

    try:
        message_text = synthesize(topic_id, [i.to_dict() for i in ranked], cfg=cfg)
    except Exception as e:
        log.exception("synthesis failed")
        message_text = f"news-aggregator: digest for {topic_id} failed during synthesis: {e}"
        msg_ids = await send_digest(message_text, topic_id=topic_id, cfg=cfg)
        storage.log_digest(run_id=run_id, topic_id=topic_id, message_text=message_text,
                           telegram_message_ids=msg_ids, at=datetime.now(timezone.utc))
        storage.finish_run(run_id, status="error", items_fetched=fetched,
                           items_delivered=0, error_message=str(e),
                           at=datetime.now(timezone.utc))
        return RunResult(run_id, "error", fetched, 0)

    msg_ids = await send_digest(message_text, topic_id=topic_id, cfg=cfg)
    storage.log_digest(run_id=run_id, topic_id=topic_id, message_text=message_text,
                       telegram_message_ids=msg_ids, at=datetime.now(timezone.utc))

    status = "partial" if fail_count > 0 else "ok"
    storage.finish_run(run_id, status=status, items_fetched=fetched,
                       items_delivered=len(ranked), at=datetime.now(timezone.utc))
    return RunResult(run_id, status, fetched, len(ranked))
