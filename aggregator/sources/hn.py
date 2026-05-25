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
    """Algolia search for the last 24h, normalized into a list of dicts."""
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")
    try:
        response = _upstream.search_hackernews(
            topic=query, from_date=from_date, to_date=to_date, depth="default"
        )
        parsed = _upstream.parse_hackernews_response(response, query=query)
    except Exception as e:  # noqa: BLE001
        log.warning("hn search failed for %r: %s", query, e)
        return []
    return parsed[:limit]


def _parse_created_at(raw: dict[str, Any]) -> datetime:
    """HN dicts carry either ``created_at`` (ISO) or ``created_at_i`` (epoch)."""
    epoch = raw.get("created_at_i")
    if isinstance(epoch, (int, float)):
        try:
            return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        except (ValueError, OSError):
            pass
    iso = raw.get("created_at")
    if iso:
        try:
            s = str(iso).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _to_item(raw: dict[str, Any]) -> Item:
    """Map an upstream HN dict to our Item."""
    native_id = str(raw.get("objectID") or raw.get("id") or raw.get("hn_url", ""))
    url = str(raw.get("url") or raw.get("hn_url") or "")
    points = raw.get("points") or raw.get("score") or 0
    comments = raw.get("num_comments") or 0
    return Item(
        id=f"hackernews:{native_id}",
        source="hackernews",
        title=str(raw.get("title") or "").strip(),
        url=url,
        text=str(raw.get("story_text") or raw.get("text") or ""),
        created_at=_parse_created_at(raw),
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

        items: list[Item] = []
        for q in all_queries:
            raws = await asyncio.to_thread(_fetch_hn, q, 15)
            items.extend(_to_item(r) for r in raws)
        return items
