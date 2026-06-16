"""Shared utilities for source adapters."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from aggregator.sources.base import Item


def parse_created_at(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def matches_any_symbol(item: Item, symbols: list[str]) -> bool:
    hay = f"{item.title}\n{item.text}"
    return any(
        re.search(rf"\b{re.escape(s)}\b", hay, flags=re.IGNORECASE)
        for s in symbols
    )
