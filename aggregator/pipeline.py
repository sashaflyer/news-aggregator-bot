"""Pipeline orchestration. One callable: run_digest(topic_id, cfg, storage, trigger)."""
from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aggregator.config import Config
from aggregator.ranking import (
    cap_per_symbol,
    score_and_dedup,
    item_to_source_item,
    engagement_score,
    apply_per_author_cap,
)
from aggregator.sources.base import Item, Source
from aggregator.sources.github import GithubSource
from aggregator.sources.hn import HnSource
from aggregator.sources.polymarket import PolymarketSource
from aggregator.sources.registry import KNOWN_SOURCE_KEYS
from aggregator.sources.rss import RssSource
from aggregator.storage import Storage
from aggregator.relevance import filter_crypto_watchlist_items
from aggregator.synth import synthesize_async
from aggregator.url_norm import dedup_key
from aggregator.delivery.telegram import send_digest

log = logging.getLogger(__name__)

# Source registry: keys MUST match KNOWN_SOURCE_KEYS from sources.registry.
# Adding a new source = (1) define the adapter, (2) add the key to
# KNOWN_SOURCE_KEYS (aggregator/sources/registry.py), (3) add the instance
# here — in that order.
SOURCES: dict[str, Source] = {
    "rss": RssSource(),
    "polymarket": PolymarketSource(),
    "hackernews": HnSource(),
    "github": GithubSource(),
}

# Defensive: at import time, catch a divergence between the canonical key
# set (config validator) and the actually-built instances. A typo in either
# would otherwise surface only as a config-time error.
if set(SOURCES.keys()) != set(KNOWN_SOURCE_KEYS):
    raise RuntimeError(
        f"SOURCES dict {sorted(SOURCES)} diverges from "
        f"KNOWN_SOURCE_KEYS {sorted(KNOWN_SOURCE_KEYS)}"
    )

# Backward-compatible aliases: tests and external code reference these as
# pipeline._score_and_dedup etc. Canonical implementations live in ranking.py.
_score_and_dedup = score_and_dedup
_cap_per_symbol = cap_per_symbol
_apply_per_author_cap = apply_per_author_cap
_item_to_source_item = item_to_source_item


@dataclass
class RunResult:
    run_id: int
    status: str       # "ok" | "partial" | "error"
    items_fetched: int
    items_delivered: int


async def _fetch_all(
    queries: dict[str, Any], allowed_sources: list[str]
) -> dict[str, list[Item] | Exception]:
    async def safe(name: str, src: Source):
        try:
            return name, await src.fetch(queries)
        except Exception as e:
            log.exception("source %s failed", name)
            return name, e
    selected = []
    for n in allowed_sources:
        if n in SOURCES:
            selected.append((n, SOURCES[n]))
        else:
            log.warning("source %r in config but not in SOURCES registry; skipping", n)
    pairs = await asyncio.gather(*(safe(n, s) for n, s in selected))
    return dict(pairs)


def _prepare_queries(topic: Any) -> dict[str, Any]:
    queries: dict[str, Any] = {
        "polymarket_tags": topic.polymarket_tags,
        "hn_keywords": topic.hn_keywords,
        "rss_feeds": topic.rss_feeds,
        "github_keywords": topic.github_keywords,
    }
    if topic.kind == "watchlist":
        queries["rss_symbol_feeds"] = {
            w.ticker: w.feeds for w in topic.watch if w.feeds
        }
        queries["rss_search_feeds"] = [
            {"symbol": w.ticker, "terms": [w.ticker, *w.aliases], "url": u}
            for w in topic.watch for u in w.search_feeds
        ]
        queries["symbols"] = topic.query_symbols
    else:
        queries["rss_symbol_feeds"] = {}
        queries["rss_search_feeds"] = []
        queries["symbols"] = []
    return queries


def _process_source_results(
    per_source: dict[str, list[Item] | Exception], storage: Storage
) -> tuple[list[Item], int, int]:
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
    return items, ok_count, fail_count


async def _deliver_and_record(
    topic_id: str, cfg: Config, storage: Storage,
    ranked: list[Item], fetched: int, fail_count: int, run_id: int, now: datetime,
) -> RunResult:
    try:
        message_text = await synthesize_async(
            topic_id, [i.to_dict() for i in ranked], cfg=cfg
        )
    except Exception as e:
        log.exception("synthesis failed")
        message_text = (
            f"BriefBot: digest for {html.escape(topic_id)} "
            f"failed during synthesis: {html.escape(str(e))}"
        )
        msg_ids = await send_digest(message_text, topic_id=topic_id, cfg=cfg)
        storage.log_digest(run_id=run_id, topic_id=topic_id, message_text=message_text,
                           telegram_message_ids=msg_ids, at=datetime.now(timezone.utc))
        storage.finish_run(run_id, status="error", items_fetched=fetched,
                           items_delivered=0, error_message=str(e),
                           at=datetime.now(timezone.utc))
        return RunResult(run_id, "error", fetched, 0)

    msg_ids = await send_digest(message_text, topic_id=topic_id, cfg=cfg)
    delivery_at = datetime.now(timezone.utc)
    storage.log_digest(run_id=run_id, topic_id=topic_id, message_text=message_text,
                       telegram_message_ids=msg_ids, at=delivery_at)

    if msg_ids:
        storage.record_delivered_items(
            topic_id=topic_id,
            items=[i.to_dict() for i in ranked],
            at=delivery_at,
        )

    status = "partial" if fail_count > 0 else "ok"
    storage.finish_run(run_id, status=status, items_fetched=fetched,
                       items_delivered=len(ranked), at=datetime.now(timezone.utc))
    return RunResult(run_id, status, fetched, len(ranked))


async def run_digest(topic_id: str, cfg: Config, storage: Storage, *,
                     trigger: str = "scheduled") -> RunResult:
    now = datetime.now(timezone.utc)
    run_id = storage.start_run(topic_id, trigger=trigger, at=now)
    log.info("run %s started for topic %s (trigger=%s)", run_id, topic_id, trigger)

    topic = cfg.topics.get(topic_id)
    if topic is None:
        msg = f"topic {topic_id!r} not found in config"
        storage.finish_run(run_id, status="error", items_fetched=0, items_delivered=0,
                           error_message=msg, at=datetime.now(timezone.utc))
        return RunResult(run_id, "error", 0, 0)

    queries = _prepare_queries(topic)
    per_source = await _fetch_all(queries, topic.sources)
    items, ok_count, fail_count = _process_source_results(per_source, storage)
    fetched = len(items)

    if ok_count == 0:
        fallback_msg = (
            f"BriefBot: all sources failed for topic "
            f"<code>{html.escape(topic_id)}</code>. "
            f"Check source_health and logs."
        )
        try:
            await send_digest(fallback_msg, topic_id=topic_id, cfg=cfg)
        except Exception:
            log.exception("failed to send all-sources-failed heartbeat")
        storage.finish_run(run_id, status="error", items_fetched=0, items_delivered=0,
                           error_message="all sources failed",
                           at=datetime.now(timezone.utc))
        return RunResult(run_id, "error", 0, 0)

    since = datetime.now(timezone.utc) - timedelta(days=cfg.scoring.dedup_window_days)
    pruned = storage.prune_delivered_findings(older_than=since)
    if pruned:
        log.info("pruned %d delivered_findings rows older than %s", pruned, since.date())
    recent_urls = storage.recently_delivered_urls(topic_id=topic_id, since=since)
    if recent_urls:
        before = len(items)
        items = [
            it for it in items
            if dedup_key({"url": it.url, "id": it.id}) not in recent_urls
        ]
        log.info("filtered %d previously-delivered items; %d remain",
                 before - len(items), len(items))

    if topic.kind == "watchlist":
        before = len(items)
        items = filter_crypto_watchlist_items(items)
        dropped = before - len(items)
        if dropped:
            log.info("watchlist relevance filter: dropped %d off-topic items; %d remain",
                     dropped, len(items))

    if topic.kind == "general":
        ranked = await asyncio.to_thread(
            _score_and_dedup, items, top_n=topic.top_n,
            per_author_cap=cfg.scoring.per_author_cap, scoring=cfg.scoring,
        )
    else:
        pre_cap = topic.per_symbol_top_n * len(topic.canonical_symbols) * 4
        ranked = await asyncio.to_thread(
            _score_and_dedup, items, top_n=pre_cap,
            per_author_cap=cfg.scoring.per_author_cap, scoring=cfg.scoring,
        )
        alias_map: dict[str, str] = {}
        for w in topic.watch:
            alias_map[w.ticker.lower()] = w.ticker
            for a in w.aliases:
                alias_map.setdefault(a.lower(), w.ticker)
        ranked = _cap_per_symbol(
            ranked, topic.canonical_symbols, alias_map, topic.per_symbol_top_n,
        )

    if not ranked:
        log.info("no new items to deliver for %s; sending heartbeat", topic_id)
        message_text = (
            f"BriefBot: no new items for "
            f"<code>{html.escape(topic_id)}</code> "
            f"in the last {cfg.scoring.dedup_window_days} days "
            f"(fetched {fetched}, all previously delivered or filtered)"
        )
        msg_ids = await send_digest(message_text, topic_id=topic_id, cfg=cfg)
        if not msg_ids:
            storage.finish_run(run_id, status="error", items_fetched=fetched,
                               items_delivered=0,
                               error_message="heartbeat send failed",
                               at=datetime.now(timezone.utc))
            return RunResult(run_id, "error", fetched, 0)
        storage.log_digest(run_id=run_id, topic_id=topic_id, message_text=message_text,
                           telegram_message_ids=msg_ids,
                           at=datetime.now(timezone.utc))
        status = "partial" if fail_count > 0 else "ok"
        storage.finish_run(run_id, status=status, items_fetched=fetched,
                           items_delivered=0, at=datetime.now(timezone.utc))
        return RunResult(run_id, status, fetched, 0)

    return await _deliver_and_record(topic_id, cfg, storage, ranked, fetched, fail_count, run_id, now)
