"""Config loader. Validates config.toml structure and types."""
from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_CRON_RE = re.compile(r"^\S+\s+\S+\s+\S+\s+\S+\s+\S+$")


def _validate_cron(v: str) -> str:
    if not _CRON_RE.match(v):
        raise ValueError(f"not a 5-field cron expression: {v!r}")
    return v


class ScheduleConfig(BaseModel):
    timezone: str


class GeneralConfig(BaseModel):
    subreddits: list[str] = Field(min_length=1)
    polymarket_tags: list[str]
    top_n: int = Field(ge=1, le=200)
    schedule: str

    @field_validator("schedule")
    @classmethod
    def _v_schedule(cls, v: str) -> str:
        return _validate_cron(v)


class WatchlistConfig(BaseModel):
    symbols: list[str] = Field(min_length=1)
    per_symbol_top_n: int = Field(ge=1, le=50)
    schedule: str

    @field_validator("schedule")
    @classmethod
    def _v_schedule(cls, v: str) -> str:
        return _validate_cron(v)


class ScoringConfig(BaseModel):
    dedup_window_days: int = Field(ge=1, le=365)
    min_score: float
    per_author_cap: int = Field(ge=1)


class SynthConfig(BaseModel):
    model: str
    max_input_items: int = Field(ge=1, le=500)
    max_output_tokens: int = Field(ge=64, le=8192)


class TelegramConfig(BaseModel):
    parse_mode: Literal["MarkdownV2", "Markdown", "HTML"]


class StorageConfig(BaseModel):
    data_dir: str


class Config(BaseModel):
    schedule: ScheduleConfig
    crypto_general: GeneralConfig
    crypto_watchlist: WatchlistConfig
    scoring: ScoringConfig
    synth: SynthConfig
    telegram: TelegramConfig
    storage: StorageConfig


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Config(
        schedule=ScheduleConfig(**raw["schedule"]),
        crypto_general=GeneralConfig(**raw["crypto"]["general"]),
        crypto_watchlist=WatchlistConfig(**raw["crypto"]["watchlist"]),
        scoring=ScoringConfig(**raw["scoring"]),
        synth=SynthConfig(**raw["synth"]),
        telegram=TelegramConfig(**raw["telegram"]),
        storage=StorageConfig(**raw["storage"]),
    )
