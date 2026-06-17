"""Config loader. Validates config.toml structure and types."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Annotated, Any, Literal, Union

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from aggregator.sources.registry import KNOWN_SOURCE_KEYS

# The set of source registry keys understood by the pipeline. Imported from
# ``aggregator.sources.registry`` — the canonical list — and aliased for
# existing call sites that reference ``_KNOWN_SOURCES``.
_KNOWN_SOURCES = KNOWN_SOURCE_KEYS

# Prompt template filenames are constrained to a safe character class so a
# config writer cannot escape PROMPTS_DIR via path-traversal sequences.
_PROMPT_FILE_RE = re.compile(r"^[A-Za-z0-9_\-]+\.md$")


def _validate_cron(v: str) -> str:
    try:
        CronTrigger.from_crontab(v)
    except ValueError as e:
        raise ValueError(f"invalid cron expression {v!r}: {e}") from e
    return v


def _strip_nonempty_str(v: str) -> str:
    s = (v or "").strip()
    if not s:
        raise ValueError("must be a non-empty, non-whitespace string")
    return s


def _strip_nonempty_list(v: list[str]) -> list[str]:
    out = []
    for item in v:
        s = (item or "").strip()
        if not s:
            raise ValueError("list items must be non-empty after stripping whitespace")
        out.append(s)
    return out


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str


class WatchEntry(BaseModel):
    """One coin/project the watchlist tracks. `ticker` is the canonical name
    shown in the digest; `aliases` are extra strings (full name, variants)
    fed to source searches to widen recall without diluting the prompt.
    """
    model_config = ConfigDict(extra="forbid")

    ticker: str
    aliases: list[str] = Field(default_factory=list)
    feeds: list[str] = Field(default_factory=list)
    search_feeds: list[str] = Field(default_factory=list)

    @field_validator("ticker")
    @classmethod
    def _v_ticker(cls, v: str) -> str:
        s = _strip_nonempty_str(v)
        # Single-character tickers over-match in Polymarket's word-boundary
        # title regex (sources/polymarket.py:_matches_any_symbol) — e.g. "X"
        # would fire on every X-prefixed token. Two chars is the practical floor.
        if len(s) < 2:
            raise ValueError("ticker must be at least 2 characters")
        return s

    @field_validator("aliases")
    @classmethod
    def _v_aliases(cls, v: list[str]) -> list[str]:
        return _strip_nonempty_list(v)

    @field_validator("feeds")
    @classmethod
    def _v_feeds(cls, v: list[str]) -> list[str]:
        return _strip_nonempty_list(v)

    @field_validator("search_feeds")
    @classmethod
    def _v_search_feeds(cls, v: list[str]) -> list[str]:
        return _strip_nonempty_list(v)


class _BaseTopicConfig(BaseModel):
    """Shared fields for the topic-discriminated union. Constructing this
    class directly is a programming error; use ``TopicConfig`` (the factory)
    or one of the concrete subclasses ``GeneralTopicConfig`` /
    ``WatchlistTopicConfig``.
    """
    model_config = ConfigDict(extra="forbid")

    sources: list[str] = Field(min_length=1)
    prompt_template: str
    schedule: str
    # Per-source query inputs (all optional; each source picks what it understands).
    polymarket_tags: list[str] = Field(default_factory=list)
    hn_keywords: list[str] = Field(default_factory=list)
    github_keywords: list[str] = Field(default_factory=list)
    rss_feeds: list[str] = Field(default_factory=list)

    @field_validator("schedule")
    @classmethod
    def _v_schedule(cls, v: str) -> str:
        return _validate_cron(v)

    @field_validator("prompt_template")
    @classmethod
    def _v_prompt_template(cls, v: str) -> str:
        if not _PROMPT_FILE_RE.fullmatch(v):
            raise ValueError(
                f"prompt_template must match {_PROMPT_FILE_RE.pattern}; got {v!r}"
            )
        return v

    @field_validator("sources")
    @classmethod
    def _v_sources(cls, v: list[str]) -> list[str]:
        unknown = [s for s in v if s not in _KNOWN_SOURCES]
        if unknown:
            raise ValueError(
                f"unknown source(s) {unknown!r}; known: {sorted(_KNOWN_SOURCES)}"
            )
        return v

    @field_validator("polymarket_tags", "hn_keywords", "github_keywords", "rss_feeds")
    @classmethod
    def _v_string_lists(cls, v: list[str]) -> list[str]:
        return _strip_nonempty_list(v)


class GeneralTopicConfig(_BaseTopicConfig):
    """Topic with a single global rank: keep the top-N items by engagement."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["general"] = "general"
    top_n: int = Field(ge=1, le=200)


class WatchlistTopicConfig(_BaseTopicConfig):
    """Topic with per-symbol bucketing: keep per_symbol_top_n items per coin."""
    model_config = ConfigDict(extra="forbid")

    kind: Literal["watchlist"] = "watchlist"
    per_symbol_top_n: int = Field(ge=1, le=50)
    watch: list[WatchEntry] = Field(min_length=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def canonical_symbols(self) -> list[str]:
        """Tickers as shown in the digest (one per coin/project)."""
        return [w.ticker for w in self.watch]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def query_symbols(self) -> list[str]:
        """Tickers + aliases — the union fed to source searches."""
        out: list[str] = []
        for w in self.watch:
            out.append(w.ticker)
            out.extend(w.aliases)
        return out


# The user-facing constructor. Routes to the right concrete subclass based on
# `kind` so existing call sites (``TopicConfig(kind="general", ...)``) keep
# working without importing two separate names. Pydantic v2's discriminated
# union takes care of the same dispatch for TOML-loaded configs.
_AnyTopicConfig = Annotated[
    Union[GeneralTopicConfig, WatchlistTopicConfig],
    Field(discriminator="kind"),
]


def TopicConfig(**data: Any) -> _AnyTopicConfig:  # type: ignore[valid-type]
    """Factory mirroring the old class-constructor API.

    Dispatches to ``GeneralTopicConfig`` (kind="general") or
    ``WatchlistTopicConfig`` (kind="watchlist") based on `data["kind"]`. Lets
    callers keep using ``TopicConfig(kind=..., top_n=...)`` and get back the
    narrowed concrete type.
    """
    kind = data.get("kind")
    if kind == "general":
        return GeneralTopicConfig(**data)
    if kind == "watchlist":
        return WatchlistTopicConfig(**data)
    raise ValueError(
        f"TopicConfig: kind must be 'general' or 'watchlist'; got {kind!r}"
    )


# Type alias for typing the topics dict on Config (and on test fixtures).
AnyTopicConfig = Union[GeneralTopicConfig, WatchlistTopicConfig]


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dedup_window_days: int = Field(ge=1, le=365)
    min_score: float
    per_author_cap: int = Field(ge=1)
    weight_upvotes: float = 1.0
    weight_score: float = 1.0
    weight_comments: float = 0.1
    weight_volume: float = 0.001


class SynthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    max_input_items: int = Field(ge=1, le=500)
    max_output_tokens: int = Field(ge=64, le=8192)


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The LLM prompt templates emit HTML. The plain-text and Markdown modes
    # have different escape rules and would silently mismatch the LLM output,
    # so we restrict the option set to the one we actually use.
    parse_mode: Literal["HTML"]


class StorageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    data_dir: str


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule: ScheduleConfig
    scoring: ScoringConfig
    synth: SynthConfig
    telegram: TelegramConfig
    storage: StorageConfig
    # Discriminated union: Pydantic parses each entry into the right concrete
    # topic class based on `kind`, giving downstream code a narrowed type
    # without `# type: ignore` workarounds.
    topics: dict[str, _AnyTopicConfig] = Field(min_length=1)  # type: ignore[valid-type]


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Config.model_validate(raw)
