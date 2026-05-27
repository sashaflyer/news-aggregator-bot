"""Hacker News source adapter — wraps vendored hackernews module.

For v1.2, HN reuses the existing config fields (no schema/config change):
- general topic: searches for each entry in ``polymarket_tags`` (default ["crypto"])
  and optionally ``hn_keywords`` if present.
- watchlist topic: searches for each symbol in ``symbols``.

Module-level ``_fetch_hn`` exists so tests can patch it without network.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from aggregator.sources.base import Item, Source
from aggregator.vendor.last30days import hackernews as _upstream

log = logging.getLogger(__name__)


def _fetch_hn(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Algolia search for the last 24h, normalized into a list of dicts.

    Lets exceptions propagate; the source-level ``fetch`` runs subqueries
    under ``asyncio.gather(return_exceptions=True)`` and the pipeline
    distinguishes 'ok' from 'partial' / 'error' based on those.
    """
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")
    response = _upstream.search_hackernews(
        topic=query, from_date=from_date, to_date=to_date, depth="default"
    )
    parsed = _upstream.parse_hackernews_response(response, query=query)
    return parsed[:limit]


def _parse_created_at(s: str | None) -> datetime | None:
    """Parse a vendor ``date`` string (YYYY-MM-DD or ISO 8601) into UTC.

    Returns ``None`` for missing or unparseable input — callers must drop
    such items rather than backfilling ``now()`` (audit M9).
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _to_item(raw: dict[str, Any]) -> Item | None:
    """Map an upstream HN dict to our Item, or ``None`` if date is unparseable.

    Vendor ``parse_hackernews_response`` output shape:
        {"id": "<objectID>", "title": ..., "url": ..., "hn_url": ...,
         "author": ..., "date": "YYYY-MM-DD",
         "engagement": {"points": int, "comments": int}, ...}
    """
    created_at = _parse_created_at(raw.get("date"))
    if created_at is None:
        return None

    native_id = str(raw.get("id") or raw.get("hn_url", ""))
    url = str(raw.get("url") or raw.get("hn_url") or "")
    eng = raw.get("engagement") or {}
    points = eng.get("points") or 0
    comments = eng.get("comments") or 0
    return Item(
        id=f"hackernews:{native_id}",
        source="hackernews",
        title=str(raw.get("title") or "").strip(),
        url=url,
        text="",  # vendor doesn't surface story_text/text
        created_at=created_at,
        engagement_raw={
            "points": points,
            "score": points,    # aliased so engagement-sum sort picks it up
            "comments": comments,
        },
        metadata={
            "author": raw.get("author") or "",
            "hn_id": native_id,
        },
    )


class HnSource(Source):
    name = "hackernews"

    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        # Prefer explicit hn_keywords; fall back to polymarket_tags (keywords
        # for the general topic are reused so we don't fork config schema).
        keywords = queries.get("hn_keywords") or queries.get("polymarket_tags") or []
        symbols = queries.get("symbols") or []
        all_queries = list(keywords) + list(symbols)
        if not all_queries:
            return []

        # Concurrent search per keyword; sequential awaited 1-3s each.
        results = await asyncio.gather(
            *(asyncio.to_thread(_fetch_hn, q, 15) for q in all_queries),
            return_exceptions=True,
        )
        items: list[Item] = []
        for raws in results:
            if isinstance(raws, Exception):
                log.warning("hn subquery failed: %s", raws)
                continue
            for r in raws:
                it = _to_item(r)
                if it is not None:
                    items.append(it)
        return items
