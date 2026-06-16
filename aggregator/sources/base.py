"""Item dataclass + Source ABC. Adapters convert their source-native objects to Item."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Item:
    """Immutable container for a fetched news item.

    ``frozen=True`` prevents field reassignment, but ``engagement_raw`` and
    ``metadata`` are plain dicts whose *contents* remain mutable. Treat them
    as read-only after construction; use ``with_metadata`` to derive a new
    instance with additional metadata keys.
    """
    id: str
    source: str
    title: str
    url: str
    text: str
    created_at: datetime
    engagement_raw: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        return d

    def with_metadata(self, **extra: Any) -> "Item":
        """Return a copy of this item with `extra` merged into metadata.

        `frozen=True` prevents the in-place mutation that previously made
        items non-replayable across pipeline passes; callers that need to
        stamp a new field (e.g. ``watchlist_symbol``) get a new instance
        back, leaving the original untouched.
        """
        return replace(self, metadata={**self.metadata, **extra})


class Source(ABC):
    name: str

    @abstractmethod
    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        """Return items relevant to the topic's queries.

        `queries` is the JSON-decoded `topics.search_queries` for the topic
        being processed (e.g., {"rss_feeds": [...], "polymarket_tags": [...]}).
        Adapters use whatever subset they understand.
        """
