"""Config loader. Validates config.toml structure and types."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from apscheduler.triggers.cron import CronTrigger
from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

# The set of source registry keys understood by the pipeline. Kept in sync
# with aggregator.pipeline.SOURCES — adding a source means updating both.
_KNOWN_SOURCES = {"reddit", "rss", "polymarket", "hackernews"}

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


class TopicConfig(BaseModel):
    """Data-driven topic definition. One entry per [topics.<id>] table.

    `kind` switches the digest shape:
    - "general"   -> rank globally, keep top_n.
    - "watchlist" -> per_symbol_top_n * len(watch) cap, requires `watch` entries.
    """
    model_config = ConfigDict(extra="forbid")

    kind: Literal["general", "watchlist"]
    sources: list[str] = Field(min_length=1)
    prompt_template: str
    schedule: str
    top_n: int | None = Field(default=None, ge=1, le=200)
    per_symbol_top_n: int | None = Field(default=None, ge=1, le=50)
    # Per-source query inputs (all optional; each source picks what it understands).
    subreddits: list[str] = Field(default_factory=list)
    polymarket_tags: list[str] = Field(default_factory=list)
    hn_keywords: list[str] = Field(default_factory=list)
    watch: list[WatchEntry] = Field(default_factory=list)

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

    @field_validator("subreddits", "polymarket_tags", "hn_keywords")
    @classmethod
    def _v_string_lists(cls, v: list[str]) -> list[str]:
        return _strip_nonempty_list(v)

    @model_validator(mode="after")
    def _v_kind_requirements(self) -> "TopicConfig":
        if self.kind == "general" and self.top_n is None:
            raise ValueError("kind='general' requires top_n")
        if self.kind == "watchlist":
            if self.per_symbol_top_n is None:
                raise ValueError("kind='watchlist' requires per_symbol_top_n")
            if not self.watch:
                raise ValueError("kind='watchlist' requires non-empty watch entries")
        return self

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


class ScoringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dedup_window_days: int = Field(ge=1, le=365)
    min_score: float
    per_author_cap: int = Field(ge=1)


class SynthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    max_input_items: int = Field(ge=1, le=500)
    max_output_tokens: int = Field(ge=64, le=8192)


class TelegramConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parse_mode: Literal["MarkdownV2", "Markdown", "HTML"]


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
    topics: dict[str, TopicConfig] = Field(min_length=1)


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Config.model_validate(raw)
