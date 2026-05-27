"""Reddit source adapter.

The hot-listing path (`_fetch_subreddit`) hits Reddit's public
`/r/<sub>/hot.json` endpoint directly. The vendored upstream module's `search`
is used only for symbol-targeted queries (`_search_reddit`).

Module-level `_fetch_subreddit` / `_search_reddit` are kept as patchable
indirections so tests can mock without touching the network.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from aggregator.sources._ua import USER_AGENT
from aggregator.sources.base import Item, Source
from aggregator.vendor.last30days import reddit_public

log = logging.getLogger(__name__)


def _fetch_subreddit(sub: str, limit: int = 25) -> list[dict[str, Any]]:
    """Fetch /r/<sub>/hot.json directly. Returns dicts in the shape `_to_item` expects.

    Public endpoint — no OAuth required. Rate limit is ~10 req/min anonymous;
    for a once-daily digest pulling a handful of subs this is fine.
    """
    capped = max(1, min(int(limit), 100))
    url = f"https://www.reddit.com/r/{sub}/hot.json?limit={capped}&raw_json=1"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        retry_after = ""
        try:
            retry_after = e.headers.get("Retry-After", "") if e.headers else ""
        except AttributeError:
            retry_after = ""
        log.warning(
            "reddit hot fetch failed for r/%s: HTTP %s (retry_after=%r)",
            sub, e.code, retry_after,
        )
        return []
    except urllib.error.URLError as e:
        log.warning("reddit hot fetch network error for r/%s: %s", sub, e)
        return []
    except json.JSONDecodeError as e:
        log.warning("reddit hot fetch decode error for r/%s: %s", sub, e)
        return []

    posts: list[dict[str, Any]] = []
    for child in raw.get("data", {}).get("children", []):
        p = child.get("data", {})
        # Drop stickied (AutoModerator dailies, mod announcements) and posts
        # that listings still surface after removal/deletion.
        if p.get("stickied"):
            continue
        if p.get("removed_by_category"):
            continue
        if (p.get("selftext") or "").strip() in {"[removed]", "[deleted]"}:
            continue
        permalink = p.get("permalink") or ""
        url = f"https://www.reddit.com{permalink}" if permalink else (p.get("url") or "")
        posts.append({
            "reddit_id": p.get("id"),
            "title": p.get("title", ""),
            "url": url,
            "selftext": p.get("selftext", ""),
            "created_utc": p.get("created_utc"),
            "score": p.get("score", 0),
            "num_comments": p.get("num_comments", 0),
            "subreddit": p.get("subreddit", sub),
            "author": p.get("author", ""),
            "engagement": {
                "score": p.get("score", 0),
                "num_comments": p.get("num_comments", 0),
                "upvote_ratio": p.get("upvote_ratio"),
            },
        })
    return posts


def _search_reddit(query: str, limit: int = 15) -> list[dict[str, Any]]:
    """Search Reddit globally for a query. Used for symbol-targeted watchlist queries."""
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


_UNSTABLE_SEARCH_ID = re.compile(r"^R\d+$")


def _to_item(raw: dict[str, Any]) -> Item:
    """Map an upstream post dict to our Item."""
    url = str(raw.get("url", ""))
    # ``_fetch_subreddit`` sets ``reddit_id`` (real Reddit post id, stable).
    # ``_search_reddit`` sets ``id`` to a per-call counter "R1"/"R2"/... which
    # collides across symbol queries — fall through to URL in that case so
    # different posts from different searches don't share the same Item.id.
    reddit_id = raw.get("reddit_id")
    raw_id_candidate = raw.get("id")
    if reddit_id:
        raw_id = str(reddit_id)
    elif raw_id_candidate and not _UNSTABLE_SEARCH_ID.match(str(raw_id_candidate)):
        raw_id = str(raw_id_candidate)
    else:
        raw_id = url
    upstream_engagement = raw.get("engagement") or {}
    score = upstream_engagement.get("score", raw.get("score", 0))
    num_comments = upstream_engagement.get("num_comments", raw.get("num_comments", 0))

    return Item(
        id=f"reddit:{raw_id}",
        source="reddit",
        title=html.unescape(str(raw.get("title", "")).strip()),
        url=url,
        text=html.unescape(str(raw.get("selftext", ""))),
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

        # Run subreddit fetches and symbol searches concurrently. Each call
        # is independent; serially awaiting them added ~1-2s per query.
        tasks = [asyncio.to_thread(_fetch_subreddit, sub, 25) for sub in subreddits]
        tasks += [asyncio.to_thread(_search_reddit, sym, 15) for sym in symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        items: list[Item] = []
        for raws in results:
            if isinstance(raws, Exception):
                log.warning("reddit subquery failed: %s", raws)
                continue
            items.extend(_to_item(r) for r in raws)
        return items
