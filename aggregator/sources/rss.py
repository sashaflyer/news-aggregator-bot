"""RSS source adapter.

Drives crypto_general (untagged broad feeds, optionally symbol-filtered) and
crypto_watchlist (per-coin tag feeds via ``rss_symbol_feeds``: items get tagged
``metadata["watchlist_symbol"]`` and are not filtered, since the feed is the
curation). Per-symbol *search* feeds via ``rss_search_feeds`` are also tagged
but alias-filtered by the symbol's own terms (a keyword search is not curation).

RSS has no engagement signal, so each item gets a recency pseudo-score in
``engagement_raw["score"]`` (newer -> higher).

Network model: one ``httpx.AsyncClient`` per ``fetch()`` call, fanning out
concurrently. The client gives us HTTP keepalive across feeds in the same
topic (most watchlist topics hit 4-5 of the same outlet's tag feeds), a
shared DNS cache, and a bounded connection pool.

Test seam: ``_parse_entries(content)`` is a pure function. Tests patch this
to return canned entries, and use ``respx`` to mock the underlying
``httpx.AsyncClient.get`` calls. No thread pool, no sync fetch, no
network round-trip in tests.
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
# Per-fetch HTTP timeout. Kept short enough that a wedged server doesn't
# block the whole batch; long enough to tolerate slow RSS endpoints.
_HTTP_TIMEOUT_S = 20.0
# Concurrent in-flight connections per `fetch()` call. Bounds the number
# of half-open connections to slow endpoints and stops a 30-feed topic
# from queueing dozens of sockets while we wait.
_MAX_CONNECTIONS = 10


def _parse_entries(content: bytes) -> list[dict[str, Any]]:
    """Run feedparser over raw response bytes. Pure function — the test seam.

    Tests patch this directly (no network, no client construction needed)
    to return canned entries. In production, the async path passes the
    response body from ``httpx.AsyncClient.get`` straight in.
    """
    parsed = feedparser.parse(content)
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


async def _fetch_one(client: httpx.AsyncClient, url: str) -> list[dict[str, Any]]:
    """One fetch + parse. The HTTP call is async on the client; feedparser
    runs in a worker thread because it is sync and CPU-bound (the loop
    must not block on it for tens of ms at a time).
    """
    resp = await client.get(url, headers={"User-Agent": _UA}, follow_redirects=True)
    resp.raise_for_status()
    return await asyncio.to_thread(_parse_entries, resp.content)


async def _gather_urls(urls: list[str]) -> list[list[dict[str, Any]] | Exception]:
    """Open one ``httpx.AsyncClient`` and fetch every URL in ``urls`` concurrently.

    Returns a list parallel to ``urls``; each entry is either the parsed
    entries or the exception that aborted that fetch. Caller is responsible
    for separating the two — this matches the per-source error contract
    used by HN/Polymarket (a single failure must not poison the batch).

    All URLs in a single call share the client's connection pool, so feeds
    to the same host (e.g. multiple tag feeds under cointelegraph.com) reuse
    a TCP/TLS connection instead of paying handshake cost per fetch.
    """
    limits = httpx.Limits(max_connections=_MAX_CONNECTIONS, max_keepalive_connections=5)
    timeout = httpx.Timeout(_HTTP_TIMEOUT_S)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        return await asyncio.gather(
            *(_fetch_one(client, u) for u in urls),
            return_exceptions=True,
        )


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
            results = await _gather_urls(feeds)
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
            results = await _gather_urls([u for _, u in pairs])
            for (sym, _u), raws in zip(pairs, results):
                if isinstance(raws, Exception):
                    log.warning("rss symbol feed fetch failed (%s): %s", sym, raws)
                    continue
                for r in raws:
                    it = _to_item(r, now=now, symbol=sym)
                    if it is not None:
                        items.append(it)

        # Per-symbol *search* feeds (e.g. Google News queries): tagged like
        # symbol feeds, but alias-filtered by the symbol's own terms because a
        # keyword search is not the curation a real outlet tag feed is.
        search_feeds = queries.get("rss_search_feeds") or []
        if search_feeds:
            results = await _gather_urls([e["url"] for e in search_feeds])
            for e, raws in zip(search_feeds, results):
                if isinstance(raws, Exception):
                    log.warning("rss search feed fetch failed (%s): %s", e["symbol"], raws)
                    continue
                for r in raws:
                    it = _to_item(r, now=now, symbol=e["symbol"])
                    if it is not None and _matches_any_symbol(it, e["terms"]):
                        items.append(it)
        return items
