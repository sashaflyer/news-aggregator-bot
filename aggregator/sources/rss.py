"""RSS source adapter.

Drives crypto_general (untagged broad feeds, optionally symbol-filtered) and
crypto_watchlist (per-coin tag feeds via ``rss_symbol_feeds``: items get tagged
``metadata["watchlist_symbol"]`` and are not filtered, since the feed is the
curation).

RSS has no engagement signal, so each item gets a recency pseudo-score in
``engagement_raw["score"]`` (newer -> higher).

``_fetch_feed`` is a patchable indirection so tests mock without network.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx

from aggregator.sources.base import Item, Source

log = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; news-aggregator/0.1; +rss)"
_RECENCY_BASE = 200.0


def _fetch_feed(url: str) -> list[dict[str, Any]]:
    """GET a feed, parse with feedparser, return normalized entry dicts.
    Raises on HTTP/network error so the per-feed gather records it."""
    resp = httpx.get(url, timeout=20, headers={"User-Agent": _UA}, follow_redirects=True)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.content)
    out: list[dict[str, Any]] = []
    for e in parsed.entries:
        out.append({
            "id": e.get("id") or e.get("link") or "",
            "title": e.get("title", ""),
            "url": e.get("link", ""),
            "summary": e.get("summary", ""),
            "published_parsed": e.get("published_parsed") or e.get("updated_parsed"),
        })
    return out


def _to_item(raw: dict[str, Any], *, now: datetime, symbol: str | None = None) -> Item | None:
    sp = raw.get("published_parsed")
    if not sp:
        return None
    try:
        created_at = datetime(*sp[:6], tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    age_hours = max(0.0, (now - created_at).total_seconds() / 3600.0)
    score = max(1.0, _RECENCY_BASE - age_hours)
    raw_id = str(raw.get("id") or raw.get("url") or "")
    meta: dict[str, Any] = {}
    if symbol:
        meta["watchlist_symbol"] = symbol
    return Item(
        id=f"rss:{raw_id}",
        source="rss",
        title=html.unescape(str(raw.get("title", "")).strip()),
        url=str(raw.get("url", "")),
        text=html.unescape(str(raw.get("summary", ""))),
        created_at=created_at,
        engagement_raw={"score": score},
        metadata=meta,
    )


def _matches_any_symbol(item: Item, symbols: list[str]) -> bool:
    hay = f"{item.title}\n{item.text}"
    return any(re.search(rf"\b{re.escape(s)}\b", hay, flags=re.IGNORECASE) for s in symbols)


class RssSource(Source):
    name = "rss"

    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        feeds = queries.get("rss_feeds") or []
        symbol_feeds = queries.get("rss_symbol_feeds") or {}
        symbols = queries.get("symbols") or []
        now = datetime.now(timezone.utc)
        items: list[Item] = []

        if feeds:
            results = await asyncio.gather(
                *(asyncio.to_thread(_fetch_feed, u) for u in feeds),
                return_exceptions=True,
            )
            untagged: list[Item] = []
            for raws in results:
                if isinstance(raws, Exception):
                    log.warning("rss feed fetch failed: %s", raws)
                    continue
                for r in raws:
                    it = _to_item(r, now=now)
                    if it is not None:
                        untagged.append(it)
            if symbols:
                untagged = [it for it in untagged if _matches_any_symbol(it, symbols)]
            items.extend(untagged)

        pairs = [(sym, u) for sym, urls in symbol_feeds.items() for u in urls]
        if pairs:
            results = await asyncio.gather(
                *(asyncio.to_thread(_fetch_feed, u) for _, u in pairs),
                return_exceptions=True,
            )
            for (sym, _u), raws in zip(pairs, results):
                if isinstance(raws, Exception):
                    log.warning("rss symbol feed fetch failed (%s): %s", sym, raws)
                    continue
                for r in raws:
                    it = _to_item(r, now=now, symbol=sym)
                    if it is not None:
                        items.append(it)
        return items
