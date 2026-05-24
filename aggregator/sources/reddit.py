"""Reddit source adapter — thin wrapper around vendored reddit_public.

Translates upstream normalized post dicts into our Item shape. The module-level
indirections ``_fetch_subreddit`` and ``_search_reddit`` exist so tests can patch
them without hitting the network.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from aggregator.sources.base import Item, Source
from aggregator.vendor.last30days import reddit_public


def _fetch_subreddit(sub: str, limit: int = 25) -> list[dict[str, Any]]:
    """Fetch posts from a single subreddit. Returns upstream-shaped dicts."""
    # Upstream `search` requires a query; pass the sub name itself as a broad
    # in-sub query. Live behavior can be tuned later — tests mock this.
    return reddit_public.search(query=sub, subreddit=sub, depth="default")[:limit]


def _search_reddit(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search Reddit globally for a query. Returns upstream-shaped dicts."""
    return reddit_public.search(query=query, depth="default")[:limit]


def _parse_created_at(raw: dict[str, Any]) -> datetime:
    """Extract a UTC datetime from upstream post dict.

    Upstream consistently sets ``created_utc`` as a float epoch; ``date`` as
    YYYY-MM-DD is also present. Fall back to ``created_at`` ISO if needed.
    """
    epoch = raw.get("created_utc")
    if epoch:
        try:
            return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        except (ValueError, TypeError, OSError):
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
    """Map an upstream post dict to our Item."""
    url = str(raw.get("url", ""))
    # Derive a stable id from the URL (upstream "R1"/"R2" ids are not stable).
    raw_id = str(raw.get("reddit_id") or raw.get("id") or url)
    upstream_engagement = raw.get("engagement") or {}
    score = upstream_engagement.get("score", raw.get("score", 0))
    num_comments = upstream_engagement.get("num_comments", raw.get("num_comments", 0))

    return Item(
        id=f"reddit:{raw_id}",
        source="reddit",
        title=str(raw.get("title", "")).strip(),
        url=url,
        text=str(raw.get("selftext", "")),
        created_at=_parse_created_at(raw),
        engagement_raw={
            "score": score,
            "upvotes": score,
            "comments": num_comments,
            "upvote_ratio": upstream_engagement.get("upvote_ratio"),
        },
        metadata={
            "subreddit": raw.get("subreddit", ""),
            "author": raw.get("author", ""),
        },
    )


class RedditSource(Source):
    name = "reddit"

    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        subreddits = queries.get("subreddits") or []
        symbols = queries.get("symbols") or []

        if not subreddits and not symbols:
            return []

        items: list[Item] = []

        for sub in subreddits:
            raws = await asyncio.to_thread(_fetch_subreddit, sub, 25)
            items.extend(_to_item(r) for r in raws)

        for sym in symbols:
            raws = await asyncio.to_thread(_search_reddit, sym, 15)
            items.extend(_to_item(r) for r in raws)

        return items
