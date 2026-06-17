"""Canonical registry of source identifiers used across config + pipeline.

`KNOWN_SOURCE_KEYS` is the single source of truth for the set of source
identifiers understood by the pipeline. Both ``config`` (which validates
``topic.sources``) and ``pipeline`` (which builds the ``SOURCES`` dict of
instances) read from here. Adding a new source means: (1) define the
adapter under ``aggregator/sources/<name>.py``, (2) register the key here,
(3) register the instance in ``pipeline.SOURCES`` — in that order.
"""
from __future__ import annotations

from typing import Final

# Source registry keys. Operators reference these from config.toml under
# ``[topics.<id>].sources = ["rss", "hackernews", ...]``. Keep this set
# authoritative: ``pipeline.SOURCES`` MUST be a superset-by-key.
KNOWN_SOURCE_KEYS: Final[frozenset[str]] = frozenset({"rss", "polymarket", "hackernews", "github"})
