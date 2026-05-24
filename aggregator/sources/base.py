"""Item dataclass + Source ABC. Adapters convert their source-native objects to Item."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Item:
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


class Source(ABC):
    name: str

    @abstractmethod
    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        """Return items relevant to the topic's queries.

        `queries` is the JSON-decoded `topics.search_queries` for the topic
        being processed (e.g., {"subreddits": [...], "polymarket_tags": [...]}).
        Adapters use whatever subset they understand.
        """
