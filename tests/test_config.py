import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from aggregator.config import Config, TopicConfig, load_config


_BASE_SECTIONS = """
[schedule]
timezone = "UTC"

[scoring]
dedup_window_days = 7
min_score = 0.0
per_author_cap = 3

[synth]
model = "gpt-5.4-mini"
max_input_items = 40
max_output_tokens = 1200

[telegram]
parse_mode = "MarkdownV2"

[storage]
data_dir = "./data"
"""


def write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(content, encoding="utf-8")
    return p


def test_loads_valid_config_with_multiple_topics(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.crypto_general]
kind = "general"
sources = ["reddit", "polymarket", "hackernews"]
subreddits = ["CryptoCurrency"]
polymarket_tags = ["crypto"]
prompt_template = "general_crypto.md"
top_n = 10
schedule = "0 8 * * *"

[topics.crypto_watchlist]
kind = "watchlist"
sources = ["reddit", "polymarket"]
prompt_template = "watchlist.md"
per_symbol_top_n = 5
schedule = "0 8 * * *"

  [[topics.crypto_watchlist.watch]]
  ticker = "SOL"
  aliases = ["Solana"]

  [[topics.crypto_watchlist.watch]]
  ticker = "SUI"

  [[topics.crypto_watchlist.watch]]
  ticker = "AVAX"
""")
    cfg = load_config(cfg_path)
    assert isinstance(cfg, Config)
    assert cfg.schedule.timezone == "UTC"
    assert set(cfg.topics.keys()) == {"crypto_general", "crypto_watchlist"}
    g = cfg.topics["crypto_general"]
    assert g.kind == "general"
    assert g.subreddits == ["CryptoCurrency"]
    assert g.top_n == 10
    w = cfg.topics["crypto_watchlist"]
    assert w.kind == "watchlist"
    assert w.canonical_symbols == ["SOL", "SUI", "AVAX"]
    assert w.query_symbols == ["SOL", "Solana", "SUI", "AVAX"]
    assert w.per_symbol_top_n == 5


def test_rejects_general_without_top_n(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "general"
sources = ["reddit"]
subreddits = ["X"]
prompt_template = "general_crypto.md"
schedule = "0 8 * * *"
""")
    with pytest.raises(ValueError, match="top_n"):
        load_config(cfg_path)


def test_rejects_watchlist_without_watch_entries(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "watchlist"
sources = ["reddit"]
prompt_template = "watchlist.md"
per_symbol_top_n = 5
schedule = "0 8 * * *"
""")
    with pytest.raises(ValueError, match="watch"):
        load_config(cfg_path)


def test_rejects_watchlist_without_per_symbol_top_n(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "watchlist"
sources = ["reddit"]
prompt_template = "watchlist.md"
schedule = "0 8 * * *"

  [[topics.t.watch]]
  ticker = "SOL"
""")
    with pytest.raises(ValueError, match="per_symbol_top_n"):
        load_config(cfg_path)


def test_rejects_unknown_source(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "general"
sources = ["reddit", "twitter"]
subreddits = ["X"]
prompt_template = "general_crypto.md"
top_n = 10
schedule = "0 8 * * *"
""")
    with pytest.raises(ValueError, match="unknown source"):
        load_config(cfg_path)


def test_rejects_invalid_cron(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "general"
sources = ["reddit"]
subreddits = ["X"]
prompt_template = "general_crypto.md"
top_n = 10
schedule = "not a cron"
""")
    with pytest.raises(ValueError):
        load_config(cfg_path)


def test_topic_config_rejects_unknown_field():
    with pytest.raises(ValidationError) as exc:
        TopicConfig(
            kind="general",
            sources=["reddit"],
            sbreddits=["typo"],  # intentional misspelling
            prompt_template="general_crypto.md",
            top_n=15,
            schedule="0 8 * * *",
        )
    assert "sbreddits" in str(exc.value).lower() or "extra" in str(exc.value).lower()


def test_topic_config_rejects_nonsense_cron():
    with pytest.raises(ValidationError) as exc:
        TopicConfig(
            kind="general",
            sources=["reddit"],
            subreddits=["x"],
            prompt_template="general_crypto.md",
            top_n=5,
            schedule="99 99 99 99 99",
        )
    assert "schedule" in str(exc.value).lower() or "cron" in str(exc.value).lower()


def test_watch_entry_rejects_whitespace_ticker():
    from aggregator.config import WatchEntry
    with pytest.raises(ValidationError):
        WatchEntry(ticker="   ")


def test_watch_entry_rejects_single_char_ticker():
    from aggregator.config import WatchEntry
    with pytest.raises(ValidationError):
        WatchEntry(ticker="X")


def test_topic_strips_and_rejects_empty_list_items():
    with pytest.raises(ValidationError):
        TopicConfig(
            kind="general",
            sources=["reddit"],
            subreddits=["", "  "],
            prompt_template="general_crypto.md",
            top_n=5,
            schedule="0 8 * * *",
        )


def test_top_level_config_rejects_unknown_section(tmp_path):
    toml = tmp_path / "config.toml"
    toml.write_text("""
[schedule]
timezone = "UTC"
[scoring]
dedup_window_days = 7
min_score = 0.0
per_author_cap = 3
[synth]
model = "gpt-test"
max_input_items = 10
max_output_tokens = 100
[telegram]
parse_mode = "HTML"
[storage]
data_dir = "./data"
[topics.t1]
kind = "general"
sources = ["reddit"]
subreddits = ["x"]
prompt_template = "general_crypto.md"
top_n = 5
schedule = "0 8 * * *"
[bogus]
key = "value"
""")
    with pytest.raises(ValidationError):
        load_config(toml)
