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
from datetime import datetime, timedelta, timezone
from typing import Any

from aggregator.sources.base import Item, Source
from aggregator.vendor.last30days import polymarket as _upstream


def _fetch_by_tag(tag: str, limit: int = 50) -> list[dict[str, Any]]:
    """Fetch markets/events for a tag. Returns a flat list of upstream dicts.

    Wraps ``search_polymarket`` using the tag as the topic query. Live behavior
    can be tuned later — tests mock this.
    """
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    to_date = now.strftime("%Y-%m-%d")
    response = _upstream.search_polymarket(
        topic=tag, from_date=from_date, to_date=to_date, depth="default"
    )
    return (response.get("events") or [])[:limit]


def _parse_created_at(raw: dict[str, Any]) -> datetime:
    """Pick an event datetime. Prefer ``endDate``, fall back to now()."""
    end = raw.get("endDate") or raw.get("end_date")
    if end:
        try:
            s = str(end).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _to_item(raw: dict[str, Any]) -> Item:
    """Map an upstream Polymarket event/market dict to our Item."""
    raw_id = str(raw.get("id") or raw.get("slug") or raw.get("url", ""))
    title = str(raw.get("title") or raw.get("question") or "").strip()
    text = str(raw.get("description") or "")

    # URL precedence:
    # 1. explicit upstream url (rare in practice for Gamma events)
    # 2. construct from slug: https://polymarket.com/event/<slug>
    # 3. fall back to empty so downstream synth can skip un-linkable items
    upstream_url = str(raw.get("url", "")).strip()
    slug = str(raw.get("slug", "")).strip()
    if upstream_url and upstream_url not in ("https://polymarket.com", "https://polymarket.com/"):
        url = upstream_url
    elif slug:
        url = f"https://polymarket.com/event/{slug}"
    else:
        url = ""

    return Item(
        id=f"polymarket:{raw_id}",
        source="polymarket",
        title=title,
        url=url,
        text=text,
        created_at=_parse_created_at(raw),
        engagement_raw={
            "volume": raw.get("volume"),
            "liquidity": raw.get("liquidity"),
            "outcomes": raw.get("outcomes"),
            "outcome_prices": raw.get("outcome_prices"),
        },
        metadata={
            "slug": raw.get("slug", ""),
            "end_date": raw.get("end_date") or raw.get("endDate"),
            "tags": raw.get("tags", []),
        },
    )


def _matches_any_symbol(item: Item, symbols: list[str]) -> bool:
    """Case-insensitive substring match against title or text."""
    hay = f"{item.title}\n{item.text}".upper()
    return any(sym.upper() in hay for sym in symbols)


class PolymarketSource(Source):
    name = "polymarket"

    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        tags = queries.get("polymarket_tags") or []
        symbols = queries.get("symbols") or []

        if not tags and not symbols:
            return []

        # When only symbols are provided, fall back to the broad "crypto" tag
        # rather than firing one upstream call per symbol.
        if not tags and symbols:
            tags = ["crypto"]

        items: list[Item] = []
        for tag in tags:
            raws = await asyncio.to_thread(_fetch_by_tag, tag, 50)
            items.extend(_to_item(r) for r in raws)

        if symbols:
            items = [it for it in items if _matches_any_symbol(it, symbols)]

        return items
