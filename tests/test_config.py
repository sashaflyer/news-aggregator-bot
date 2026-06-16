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
parse_mode = "HTML"

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
sources = ["rss", "polymarket", "hackernews"]
rss_feeds = ["https://cointelegraph.com/rss"]
polymarket_tags = ["crypto"]
prompt_template = "general_crypto.md"
top_n = 10
schedule = "0 8 * * *"

[topics.crypto_watchlist]
kind = "watchlist"
sources = ["rss", "polymarket"]
prompt_template = "watchlist.md"
per_symbol_top_n = 5
schedule = "0 8 * * *"

  [[topics.crypto_watchlist.watch]]
  ticker = "SOL"
  aliases = ["Solana"]
  feeds = ["https://cointelegraph.com/rss/tag/solana"]

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
    assert g.rss_feeds == ["https://cointelegraph.com/rss"]
    assert g.top_n == 10
    w = cfg.topics["crypto_watchlist"]
    assert w.kind == "watchlist"
    assert w.canonical_symbols == ["SOL", "SUI", "AVAX"]
    assert w.query_symbols == ["SOL", "Solana", "SUI", "AVAX"]
    assert w.per_symbol_top_n == 5
    assert w.watch[0].feeds == ["https://cointelegraph.com/rss/tag/solana"]


def test_rejects_general_without_top_n(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "general"
sources = ["rss"]
rss_feeds = ["https://example.com/rss"]
prompt_template = "general_crypto.md"
schedule = "0 8 * * *"
""")
    with pytest.raises(ValueError, match="top_n"):
        load_config(cfg_path)


def test_rejects_watchlist_without_watch_entries(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "watchlist"
sources = ["rss"]
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
sources = ["rss"]
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
sources = ["rss", "twitter"]
rss_feeds = ["https://example.com/rss"]
prompt_template = "general_crypto.md"
top_n = 10
schedule = "0 8 * * *"
""")
    with pytest.raises(ValueError, match="unknown source"):
        load_config(cfg_path)


def test_rejects_reddit_as_unknown_source(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "general"
sources = ["reddit"]
prompt_template = "general_crypto.md"
top_n = 10
schedule = "0 8 * * *"
""")
    with pytest.raises(ValueError, match="unknown source"):
        load_config(cfg_path)


def test_rejects_cryptopanic_as_unknown_source(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "general"
sources = ["cryptopanic"]
prompt_template = "general_crypto.md"
top_n = 10
schedule = "0 8 * * *"
""")
    with pytest.raises(ValueError, match="unknown source"):
        load_config(cfg_path)


def test_accepts_rss_as_known_source(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "general"
sources = ["rss"]
rss_feeds = ["https://cointelegraph.com/rss"]
prompt_template = "general_crypto.md"
top_n = 10
schedule = "0 8 * * *"
""")
    cfg = load_config(cfg_path)
    assert cfg.topics["t"].sources == ["rss"]


def test_rss_feeds_validates_nonempty_strings(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "general"
sources = ["rss"]
rss_feeds = [""]
prompt_template = "general_crypto.md"
top_n = 10
schedule = "0 8 * * *"
""")
    with pytest.raises(ValueError):
        load_config(cfg_path)


def test_watch_feeds_validates_nonempty_strings(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "watchlist"
sources = ["rss"]
prompt_template = "watchlist.md"
per_symbol_top_n = 5
schedule = "0 8 * * *"

  [[topics.t.watch]]
  ticker = "SOL"
  feeds = [""]
""")
    with pytest.raises(ValueError):
        load_config(cfg_path)


def test_rejects_invalid_cron(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "general"
sources = ["rss"]
rss_feeds = ["https://example.com/rss"]
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
            sources=["rss"],
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
            sources=["rss"],
            rss_feeds=["https://example.com/rss"],
            prompt_template="general_crypto.md",
            top_n=5,
            schedule="99 99 99 99 99",
        )
    assert "schedule" in str(exc.value).lower() or "cron" in str(exc.value).lower()


def test_watch_entry_accepts_search_feeds():
    from aggregator.config import WatchEntry
    w = WatchEntry(ticker="ENA", aliases=["Ethena"],
                   search_feeds=["https://news.google.com/rss/search?q=Ethena"])
    assert w.search_feeds == ["https://news.google.com/rss/search?q=Ethena"]


def test_watch_search_feeds_validates_nonempty_strings(tmp_path):
    cfg_path = write_toml(tmp_path, _BASE_SECTIONS + """
[topics.t]
kind = "watchlist"
sources = ["rss"]
prompt_template = "watchlist.md"
per_symbol_top_n = 5
schedule = "0 8 * * *"

  [[topics.t.watch]]
  ticker = "SOL"
  search_feeds = [""]
""")
    with pytest.raises(ValueError):
        load_config(cfg_path)


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
            sources=["rss"],
            rss_feeds=["", "  "],
            prompt_template="general_crypto.md",
            top_n=5,
            schedule="0 8 * * *",
        )


def test_topic_config_rejects_path_in_prompt_template():
    with pytest.raises(ValidationError):
        TopicConfig(
            kind="general",
            sources=["rss"],
            rss_feeds=["https://example.com/rss"],
            prompt_template="../etc/passwd",
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
sources = ["rss"]
rss_feeds = ["https://example.com/rss"]
prompt_template = "general_crypto.md"
top_n = 5
schedule = "0 8 * * *"
[bogus]
key = "value"
""")
    with pytest.raises(ValidationError):
        load_config(toml)
