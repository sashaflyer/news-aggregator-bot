"""Pipeline orchestration. One callable: run_digest(topic_id, cfg, storage, trigger)."""
from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from aggregator.config import Config
from aggregator.sources.base import Item, Source
from aggregator.sources.hn import HnSource
from aggregator.sources.polymarket import PolymarketSource
from aggregator.sources.reddit import RedditSource
from aggregator.storage import Storage
from aggregator.relevance import filter_crypto_watchlist_items
from aggregator.synth import synthesize_async
from aggregator.url_norm import dedup_key
from aggregator.delivery.telegram import send_digest
from aggregator.vendor.last30days import dedupe as _dedupe
from aggregator.vendor.last30days import reddit_enrich as _reddit_enrich
from aggregator.vendor.last30days import schema as _schema

# Cap on how many Reddit items get enriched per run (extra HTTP call each).
# Higher = better digest quality, more risk of hitting Reddit's anonymous rate limit.
_REDDIT_ENRICH_CAP = 10
# Concurrent enrichment slots. Keep small — Reddit's anonymous rate limit is
# strict and we don't want to earn a ban from a tight burst.
_REDDIT_ENRICH_CONCURRENCY = 3
# Comment trimming applied after upstream enrichment fills the field.
_REDDIT_TOP_COMMENTS_PER_ITEM = 3
_REDDIT_COMMENT_EXCERPT_CHARS = 150

log = logging.getLogger(__name__)

SOURCES: dict[str, Source] = {
    "reddit": RedditSource(),
    "polymarket": PolymarketSource(),
    "hackernews": HnSource(),
}


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
    selected = [(n, SOURCES[n]) for n in allowed_sources if n in SOURCES]
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


async def _enrich_reddit_items(items: list[Item]) -> list[Item]:
    """For the top Reddit items, fetch top comments + insights and stash them
    on Item.metadata so the LLM can use them.

    Non-Reddit items pass through untouched. Failures (network, rate limit,
    parse) are logged but don't stop the run. Enrichment is capped to
    _REDDIT_ENRICH_CAP items to keep the per-run HTTP budget bounded, and
    runs at most _REDDIT_ENRICH_CONCURRENCY HTTP calls in parallel.
    """
    candidates = [
        it for it in items if it.source == "reddit" and it.url
    ][:_REDDIT_ENRICH_CAP]
    if not candidates:
        return items

    sem = asyncio.Semaphore(_REDDIT_ENRICH_CONCURRENCY)
    abort_rest = False

    async def _one(item: Item) -> bool:
        nonlocal abort_rest
        if abort_rest:
            return False
        async with sem:
            if abort_rest:
                return False
            try:
                result = await asyncio.to_thread(
                    _reddit_enrich.enrich_reddit_item, {"url": item.url}
                )
            except Exception as e:  # noqa: BLE001
                log.warning("reddit enrich failed for %s: %s", item.url, e)
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status == 429:
                    log.warning(
                        "reddit rate-limited; aborting enrichment for remaining items"
                    )
                    abort_rest = True
                return False

        raw_comments = (result.get("top_comments") or [])[:_REDDIT_TOP_COMMENTS_PER_ITEM]
        trimmed = [
            {
                "score": c.get("score"),
                "author": c.get("author"),
                "excerpt": (c.get("excerpt") or "")[:_REDDIT_COMMENT_EXCERPT_CHARS],
            }
            for c in raw_comments
        ]
        insights = (result.get("comment_insights") or [])[:5]
        item.metadata["top_comments"] = trimmed
        item.metadata["comment_insights"] = insights
        return True

    results = await asyncio.gather(*(_one(it) for it in candidates))
    enriched_count = sum(1 for r in results if r)
    if enriched_count:
        log.info("enriched %d reddit items with top comments", enriched_count)
    return items


def _cap_per_symbol(
    items: list[Item], symbols: list[str], per_symbol_top_n: int
) -> list[Item]:
    """Group items by best-matching canonical ticker (case-insensitive
    word-boundary in title or body); keep at most ``per_symbol_top_n`` per
    ticker. Off-topic items (no matching ticker) are dropped.

    The first symbol in `symbols` to match an item wins, so callers should
    order `symbols` by priority. Within each bucket, input order is preserved
    (so a pre-ranked input yields a pre-ranked output per ticker).
    """
    import re as _re
    buckets: dict[str, list[Item]] = {sym: [] for sym in symbols}
    for it in items:
        text = f"{it.title} {it.text or ''}".lower()
        matched = None
        for sym in symbols:
            if _re.search(rf"\b{_re.escape(sym.lower())}\b", text):
                matched = sym
                break
        if matched is None:
            continue
        if len(buckets[matched]) < per_symbol_top_n:
            buckets[matched].append(it)
    out: list[Item] = []
    for sym in symbols:
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


def _score_and_dedup(items: list[Item], *, top_n: int, per_author_cap: int) -> list[Item]:
    """Sort by engagement, then dedupe near-duplicates via upstream Jaccard-based
    dedupe_items (which keeps the first occurrence — so the higher-engagement
    variant wins the slot), then apply per-author cap and truncate to top_n.
    """
    if not items:
        return []

    ranked_first = sorted(items, key=_engagement_score, reverse=True)
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
        "subreddits": topic.subreddits,
        "polymarket_tags": topic.polymarket_tags,
        "hn_keywords": topic.hn_keywords,
        # Expand watch entries to (ticker + aliases) so source searches widen
        # recall (e.g., "SUI" + "Sui Network" both hit Reddit/HN).
        "symbols": topic.query_symbols,
    }

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

    # Watchlist symbol search (e.g. for "SOL", "AVAX") hits global Reddit/HN and
    # pulls in NHL teams, video games, perfume brands. Drop off-domain items
    # before they consume ranking budget or LLM tokens.
    if topic.kind == "watchlist":
        before = len(items)
        items = filter_crypto_watchlist_items(items, topic.subreddits)
        dropped = before - len(items)
        if dropped:
            log.info("watchlist relevance filter: dropped %d off-topic items; %d remain",
                     dropped, len(items))

    if topic.kind == "general":
        ranked = _score_and_dedup(
            items, top_n=topic.top_n,  # type: ignore[arg-type]
            per_author_cap=cfg.scoring.per_author_cap,
        )
    else:
        # Watchlist: rank with generous headroom so dedupe+per-author-cap don't
        # starve the per-symbol bucketing step that follows.
        pre_cap = topic.per_symbol_top_n * len(topic.canonical_symbols) * 4  # type: ignore[operator]
        ranked = _score_and_dedup(
            items, top_n=pre_cap, per_author_cap=cfg.scoring.per_author_cap,
        )
        ranked = _cap_per_symbol(
            ranked, topic.canonical_symbols, topic.per_symbol_top_n,  # type: ignore[arg-type]
        )

    # Enrich top Reddit items with comments so the LLM has the actual context.
    ranked = await _enrich_reddit_items(ranked)

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
