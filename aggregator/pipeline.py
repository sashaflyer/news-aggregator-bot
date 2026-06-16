"""Pipeline orchestration. One callable: run_digest(topic_id, cfg, storage, trigger)."""
from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aggregator.config import Config
from aggregator.sources.base import Item, Source
from aggregator.sources.hn import HnSource
from aggregator.sources.polymarket import PolymarketSource
from aggregator.sources.registry import KNOWN_SOURCE_KEYS
from aggregator.sources.rss import RssSource
from aggregator.storage import Storage
from aggregator.relevance import filter_crypto_watchlist_items
from aggregator.synth import synthesize_async
from aggregator.url_norm import dedup_key
from aggregator.delivery.telegram import send_digest
from aggregator.vendor.last30days import dedupe as _dedupe
from aggregator.vendor.last30days import schema as _schema

log = logging.getLogger(__name__)

# Source registry: keys MUST match KNOWN_SOURCE_KEYS from sources.registry.
# Adding a new source = (1) define the adapter, (2) add the key to
# KNOWN_SOURCE_KEYS, (3) add the instance here — in that order.
SOURCES: dict[str, Source] = {
    "rss": RssSource(),
    "polymarket": PolymarketSource(),
    "hackernews": HnSource(),
}

# Defensive: at import time, catch a divergence between the canonical key
# set (config validator) and the actually-built instances. A typo in either
# would otherwise surface only as a config-time error.
if set(SOURCES.keys()) != set(KNOWN_SOURCE_KEYS):
    raise RuntimeError(
        f"SOURCES dict {sorted(SOURCES)} diverges from "
        f"KNOWN_SOURCE_KEYS {sorted(KNOWN_SOURCE_KEYS)}"
    )


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


def _item_to_source_item(item: Item) -> "_schema.SourceItem":
    """Convert our Item -> upstream SourceItem just enough for dedupe."""
    return _schema.SourceItem(
        item_id=item.id,
        source=item.source,
        title=item.title,
        body=item.text,
        url=item.url,
        author=str(item.metadata.get("author") or "") or None,
        container=None,
    )


def _engagement_score(item: Item, *, scoring: "Config | None" = None) -> float:
    """Single sortable engagement number. Weights come from ScoringConfig when
    provided; defaults match the original hardcoded values."""
    from aggregator.config import ScoringConfig
    w_up = scoring.weight_upvotes if scoring else 1.0
    w_sc = scoring.weight_score if scoring else 1.0
    w_co = scoring.weight_comments if scoring else 0.1
    w_vo = scoring.weight_volume if scoring else 0.001
    return (
        w_up * item.engagement_raw.get("upvotes", 0)
        + w_sc * item.engagement_raw.get("score", 0)
        + w_co * item.engagement_raw.get("comments", 0)
        + w_vo * (item.engagement_raw.get("volume") or 0)
    )


def _cap_per_symbol(
    items: list[Item],
    canonical_symbols: list[str],
    alias_map: dict[str, str],
    per_symbol_top_n: int,
) -> list[Item]:
    """Group items by canonical ticker; keep at most ``per_symbol_top_n`` each.

    Bucketing per item: an explicit ``metadata["watchlist_symbol"]`` (set by a
    per-coin RSS feed) wins; otherwise the first ``alias_map`` key (ticker or
    alias) matching title/body by word-boundary picks the canonical ticker.
    ``alias_map`` maps ``lower(ticker|alias) -> canonical ticker``. Items that
    match nothing are dropped. Bucket output follows ``canonical_symbols`` order.
    Each kept item has ``metadata["watchlist_symbol"]`` stamped with its canonical
    bucket so the synth prompt can group by that field instead of re-matching.
    """
    buckets: dict[str, list[Item]] = {sym: [] for sym in canonical_symbols}
    canon_lower = {s.lower(): s for s in canonical_symbols}
    for it in items:
        matched = None
        tag = (it.metadata.get("watchlist_symbol") or "").strip().lower()
        if tag and tag in canon_lower:
            matched = canon_lower[tag]
        else:
            text = f"{it.title} {it.text or ''}".lower()
            for alias_lower, canon in alias_map.items():
                if re.search(rf"\b{re.escape(alias_lower)}\b", text):
                    matched = canon
                    break
        if matched is None:
            continue
        if len(buckets[matched]) < per_symbol_top_n:
            # Stamp the canonical bucket so downstream (synth prompt) can trust
            # it directly instead of re-deriving buckets by text-matching.
            # Item is frozen; the new instance leaves the input untouched.
            buckets[matched].append(it.with_metadata(watchlist_symbol=matched))
    out: list[Item] = []
    for sym in canonical_symbols:
        out.extend(buckets[sym])
    return out


def _apply_per_author_cap(items: list[Item], cap: int) -> list[Item]:
    """Keep at most `cap` items per (source, author) pair, preserving order.

    Items with no author key (e.g., Polymarket markets) are uncapped — they
    don't have a "voice" that could dominate. `cap <= 0` disables the cap.
    """
    if cap <= 0:
        return items
    counts: dict[tuple[str, str], int] = {}
    out: list[Item] = []
    dropped = 0
    for it in items:
        author = (it.metadata.get("author") or "").strip()
        if not author:
            out.append(it)
            continue
        key = (it.source, author)
        if counts.get(key, 0) >= cap:
            dropped += 1
            continue
        counts[key] = counts.get(key, 0) + 1
        out.append(it)
    if dropped:
        log.info("per-author cap: dropped %d items (cap=%d)", dropped, cap)
    return out


def _score_and_dedup(items: list[Item], *, top_n: int, per_author_cap: int,
                     scoring: "Config | None" = None) -> list[Item]:
    """Sort by engagement, then dedupe near-duplicates via upstream Jaccard-based
    dedupe_items (which keeps the first occurrence — so the higher-engagement
    variant wins the slot), then apply per-author cap and truncate to top_n.
    """
    if not items:
        return []

    ranked_first = sorted(items, key=lambda it: _engagement_score(it, scoring=scoring), reverse=True)
    by_id = {item.id: item for item in ranked_first}
    source_items = [_item_to_source_item(it) for it in ranked_first]
    try:
        deduped = _dedupe.dedupe_items(source_items, threshold=0.7)
    except Exception:
        log.exception("upstream dedupe_items failed; falling back to raw items")
        deduped = source_items

    deduped_items = [by_id[si.item_id] for si in deduped if si.item_id in by_id]
    log.info("dedupe: %d -> %d items", len(items), len(deduped_items))

    capped = _apply_per_author_cap(deduped_items, per_author_cap)
    return capped[:top_n]


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

    queries: dict[str, Any] = {
        "polymarket_tags": topic.polymarket_tags,
        "hn_keywords": topic.hn_keywords,
        "rss_feeds": topic.rss_feeds,
    }
    if topic.kind == "watchlist":
        # Watchlist-only fields; the discriminated union narrows `topic` to
        # WatchlistTopicConfig here so `watch` and `query_symbols` resolve.
        queries["rss_symbol_feeds"] = {
            w.ticker: w.feeds for w in topic.watch if w.feeds
        }
        queries["rss_search_feeds"] = [
            {"symbol": w.ticker, "terms": [w.ticker, *w.aliases], "url": u}
            for w in topic.watch for u in w.search_feeds
        ]
        queries["symbols"] = topic.query_symbols
    else:
        # General topics: feed the rss broad-feed filter with hn_keywords
        # (the operator's signal of "what this general topic is about").
        # Sources consult `symbols` only if it's present; for general topics
        # the broad feeds are *not* symbol-filtered.
        queries["rss_symbol_feeds"] = {}
        queries["rss_search_feeds"] = []
        queries["symbols"] = []

    per_source = await _fetch_all(queries, topic.sources)
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
        fallback_msg = (
            f"news-aggregator: all sources failed for topic "
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

    # Drop items we already delivered in a recent digest for this topic.
    since = datetime.now(timezone.utc) - timedelta(days=cfg.scoring.dedup_window_days)
    # Prune rows beyond the dedup window so the table doesn't grow unbounded.
    # Cheap, idempotent; one DELETE per run, indexed by delivered_at.
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

    # Watchlist symbol search (e.g. for "SOL", "AVAX") can pull in off-domain items.
    # Drop them before they consume ranking budget or LLM tokens.
    if topic.kind == "watchlist":
        before = len(items)
        items = filter_crypto_watchlist_items(items)
        dropped = before - len(items)
        if dropped:
            log.info("watchlist relevance filter: dropped %d off-topic items; %d remain",
                     dropped, len(items))

    if topic.kind == "general":
        # Discriminated union narrows `topic` to GeneralTopicConfig here, so
        # `top_n` is `int` (not `int | None`).
        ranked = _score_and_dedup(
            items, top_n=topic.top_n,
            per_author_cap=cfg.scoring.per_author_cap,
            scoring=cfg.scoring,
        )
    else:
        # Discriminated union narrows `topic` to WatchlistTopicConfig here,
        # so `per_symbol_top_n` and `canonical_symbols` are non-None.
        # Rank with generous headroom so dedupe+per-author-cap don't
        # starve the per-symbol bucketing step that follows.
        pre_cap = topic.per_symbol_top_n * len(topic.canonical_symbols) * 4
        ranked = _score_and_dedup(
            items, top_n=pre_cap, per_author_cap=cfg.scoring.per_author_cap,
            scoring=cfg.scoring,
        )
        # Build {lower(ticker|alias) -> canonical ticker}. Watch config is
        # operator-authored and trusted: on a duplicate alias the first-registered
        # entry wins (ticker keys take precedence over aliases via direct assign +
        # setdefault). We don't guard against cross-coin alias collisions.
        alias_map: dict[str, str] = {}
        for w in topic.watch:
            alias_map[w.ticker.lower()] = w.ticker
            for a in w.aliases:
                alias_map.setdefault(a.lower(), w.ticker)
        ranked = _cap_per_symbol(
            ranked, topic.canonical_symbols, alias_map, topic.per_symbol_top_n,
        )

    # Empty-result fallback: nothing new survived fetch/filter/dedup.
    # Send a short heartbeat so the user knows the bot is alive but quiet.
    if not ranked:
        log.info("no new items to deliver for %s; sending heartbeat", topic_id)
        message_text = (
            f"news-aggregator: no new items for "
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

    try:
        message_text = await synthesize_async(
            topic_id, [i.to_dict() for i in ranked], cfg=cfg
        )
    except Exception as e:
        log.exception("synthesis failed")
        # html.escape because send_digest uses parse_mode=HTML; raw < > & in
        # the exception message would otherwise force the plain-text fallback.
        message_text = (
            f"news-aggregator: digest for {html.escape(topic_id)} "
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

    # Record the items we just delivered so the next digest skips them.
    # Only do this if Telegram actually accepted something (avoid recording on dead-letter).
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
