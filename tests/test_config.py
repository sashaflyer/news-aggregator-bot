import tomllib
from pathlib import Path

import pytest

from aggregator.config import Config, load_config


def write_toml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(content, encoding="utf-8")
    return p


def test_loads_valid_config(tmp_path):
    cfg_path = write_toml(tmp_path, """
[schedule]
timezone = "UTC"

[crypto.general]
subreddits = ["CryptoCurrency"]
polymarket_tags = ["crypto"]
top_n = 10
schedule = "0 8 * * *"

[crypto.watchlist]
symbols = ["SOL", "SUI", "AVAX"]
per_symbol_top_n = 5
schedule = "0 8 * * *"

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
""")
    cfg = load_config(cfg_path)
    assert isinstance(cfg, Config)
    assert cfg.schedule.timezone == "UTC"
    assert cfg.crypto_general.subreddits == ["CryptoCurrency"]
    assert cfg.crypto_watchlist.symbols == ["SOL", "SUI", "AVAX"]
    assert cfg.synth.model == "gpt-5.4-mini"


def test_rejects_empty_symbols(tmp_path):
    cfg_path = write_toml(tmp_path, """
[schedule]
timezone = "UTC"
[crypto.general]
subreddits = ["X"]
polymarket_tags = []
top_n = 10
schedule = "0 8 * * *"
[crypto.watchlist]
symbols = []
per_symbol_top_n = 5
schedule = "0 8 * * *"
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
""")
    with pytest.raises(ValueError):
        load_config(cfg_path)


def test_rejects_invalid_cron(tmp_path):
    cfg_path = write_toml(tmp_path, """
[schedule]
timezone = "UTC"
[crypto.general]
subreddits = ["X"]
polymarket_tags = []
top_n = 10
schedule = "not a cron"
[crypto.watchlist]
symbols = ["SOL"]
per_symbol_top_n = 5
schedule = "0 8 * * *"
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
""")
    with pytest.raises(ValueError):
        load_config(cfg_path)
