"""Polymarket source adapter — thin wrapper around vendored polymarket.

Translates upstream market/event dicts into our Item shape. The module-level
indirection ``_fetch_by_tag`` exists so tests can patch it without hitting the
Gamma API.

Upstream entrypoint is ``search_polymarket(topic, from_date, to_date, depth)``
which takes a topic string and returns ``{"events": [...], "_cap": N}``. We
treat each upstream "tag" as a topic query and flatten to a list of dicts.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

from aggregator.sources.base import Item, Source
from aggregator.vendor.last30days import polymarket as _upstream

log = logging.getLogger(__name__)


def _fetch_by_tag(tag: str, limit: int = 50) -> list[dict[str, Any]]:
    """Fetch markets/events for a tag. Returns a flat list of upstream dicts.

    Wraps ``search_polymarket`` using the tag as the topic query. Live behavior
    can be tuned later — tests mock this.

    Note: Gamma's ``public-search`` endpoint ignores ``from_date``/``to_date``,
    so we don't pass them. Date filtering happens downstream from ``date``.
    """
    response = _upstream.search_polymarket(topic=tag, depth="default")
    return (response.get("events") or [])[:limit]


def _parse_created_at(s: str | None) -> datetime | None:
    """Parse a vendor ``date`` string (YYYY-MM-DD or ISO 8601) into UTC.

    Returns ``None`` for missing or unparseable input — callers must drop
    such items rather than backfilling ``now()`` (which would falsely
    resurface stale items as fresh; audit M9).

    Note: ``end_date`` is the market resolution date — usually in the future —
    so it must NOT be used as the item's creation timestamp.
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
    """Map an upstream Polymarket event dict to our Item.

    Returns ``None`` when the upstream dict has no parseable date — Item's
    ``created_at`` is required and a now() fallback would make stale markets
    look fresh.

    Vendor ``parse_polymarket_response`` output shape:
        {"event_id", "title", "question", "url", "outcome_prices",
         "volume24hr", "volume1mo", "liquidity", "date", "end_date", ...}
    Legacy/test-fixture keys (``id``, ``slug``, ``volume``, ``outcomes``,
    ``description``) are still tolerated as fallbacks.
    """
    created_at = _parse_created_at(raw.get("date"))
    if created_at is None:
        return None

    raw_id = str(raw.get("event_id") or raw.get("id") or raw.get("slug") or raw.get("url", ""))
    title = str(raw.get("title") or raw.get("question") or "").strip()
    text = str(raw.get("description") or raw.get("question") or "")

    upstream_url = str(raw.get("url", "")).strip()
    slug = str(raw.get("slug", "")).strip()
    if upstream_url and upstream_url not in ("https://polymarket.com", "https://polymarket.com/"):
        url = upstream_url
    elif slug:
        url = f"https://polymarket.com/event/{slug}"
    else:
        url = ""

    # Volume: vendor exposes volume1mo/volume24hr; prefer 1mo as the more
    # stable signal, fall back to 24hr, then a legacy top-level ``volume``.
    volume = raw.get("volume1mo") or raw.get("volume24hr") or raw.get("volume")

    return Item(
        id=f"polymarket:{raw_id}",
        source="polymarket",
        title=title,
        url=url,
        text=text,
        created_at=created_at,
        engagement_raw={
            "volume": volume,
            "volume24hr": raw.get("volume24hr"),
            "volume1mo": raw.get("volume1mo"),
            "liquidity": raw.get("liquidity"),
            "outcome_prices": raw.get("outcome_prices") or raw.get("outcomes"),
        },
        metadata={
            "slug": slug,
            "end_date": raw.get("end_date") or raw.get("endDate"),
            "question": raw.get("question", ""),
        },
    )


def _matches_any_symbol(item: Item, symbols: list[str]) -> bool:
    """Word-boundary, case-insensitive match against title or text.

    Substring matching would false-positive on ``ETH`` in ``ETHICS``,
    ``ADA`` in ``Canada``, ``SOL`` in ``solely``, etc.
    """
    hay = f"{item.title}\n{item.text}"
    for sym in symbols:
        if re.search(rf"\b{re.escape(sym)}\b", hay, flags=re.IGNORECASE):
            return True
    return False


class PolymarketSource(Source):
    name = "polymarket"

    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        tags = queries.get("polymarket_tags") or []
        symbols = queries.get("symbols") or []

        # Tags are operator-explicit. Topics without polymarket_tags skip
        # Polymarket entirely rather than silently routing to a default tag.
        if not tags:
            log.info(
                "polymarket: no polymarket_tags configured; skipping "
                "(symbols=%r)", symbols,
            )
            return []

        # Concurrent fetch per tag; one tag's failure shouldn't kill the rest.
        results = await asyncio.gather(
            *(asyncio.to_thread(_fetch_by_tag, tag, 50) for tag in tags),
            return_exceptions=True,
        )
        items: list[Item] = []
        for raws in results:
            if isinstance(raws, Exception):
                log.warning("polymarket subquery failed: %s", raws)
                continue
            for r in raws:
                it = _to_item(r)
                if it is not None:
                    items.append(it)

        if symbols:
            items = [it for it in items if _matches_any_symbol(it, symbols)]

        return items
