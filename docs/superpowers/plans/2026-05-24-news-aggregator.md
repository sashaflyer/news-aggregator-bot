# News Aggregator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-hosted Telegram bot on a Linux VPS that delivers a daily AI-synthesized crypto digest (general + SOL/SUI/AVAX watchlist) sourced from Reddit and Polymarket.

**Architecture:** One long-running async Python process. `python-telegram-bot` runs the polling loop and `/status` handler. `APScheduler` (AsyncIOScheduler) fires `pipeline.run_digest(topic_id)` on a cron schedule in the same event loop. Both call into shared modules. The research engine (Reddit + Polymarket fetchers, dedup, scoring) is vendored from MIT-licensed `mvanhorn/last30days-skill`. Our code is the delivery shell: Telegram, scheduler, bot commands, systemd integration.

**Tech Stack:** Python 3.12+, `python-telegram-bot` 21+, `apscheduler` 3.10+, `httpx`, `openai`, `python-dotenv`, `pydantic` 2 (config validation), `pytest` + `pytest-asyncio`. SQLite for state. systemd on VPS.

**Spec:** [`docs/superpowers/specs/2026-05-24-news-aggregator-design.md`](../specs/2026-05-24-news-aggregator-design.md)

---

## File Structure

See spec §5 for the full tree. Key boundaries:

- `aggregator/vendor/last30days/` — copied upstream modules, treated as a dependency: no logic edits, only import-path adjustments.
- `aggregator/` (top-level) — our delivery shell. Files have one responsibility each: `pipeline.py` (orchestration), `storage.py` (DB), `synth.py` (LLM), `delivery/telegram.py` (Telegram I/O), `scheduler.py` (cron glue), `bot/` (PTB Application + commands).
- `tests/` mirrors `aggregator/` structure.
- Secrets in `.env` (gitignored). User preferences in `config.toml` (gitignored). Only `*.example` files committed.

---

## Prerequisites (one-time, before T1)

- Python 3.12+ installed (need `tomllib` from stdlib).
- A Reddit application with client ID + secret ([reddit.com/prefs/apps](https://www.reddit.com/prefs/apps), type "script").
- A Telegram bot token from [@BotFather](https://t.me/BotFather) and your chat ID (start a chat with the bot, then `curl https://api.telegram.org/bot<TOKEN>/getUpdates` to find the numeric chat ID).
- An OpenAI API key.
- These can be obtained while implementing; only needed for live runs, not for tests.

---

## Task 1: Project bootstrap

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `LICENSE`
- Create: `README.md` (stub; full content in T20)
- Create: `data/.gitkeep`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "news-aggregator"
version = "0.1.0"
description = "Personal AI crypto research aggregator with Telegram delivery"
requires-python = ">=3.12"
license = { text = "MIT" }
dependencies = [
    "python-telegram-bot>=21.0,<22",
    "apscheduler>=3.10,<4",
    "httpx>=0.27",
    "openai>=1.40",
    "python-dotenv>=1.0",
    "pydantic>=2.6",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "respx>=0.21",     # httpx mocking
]

[project.scripts]
news-aggregator = "aggregator.__main__:main"

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["aggregator*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
build/
dist/

# Project
.env
config.toml
data/*.db
data/*.db-journal
data/*.db-wal
data/*.db-shm

# IDEs
.vscode/
.idea/
*.swp
```

- [ ] **Step 3: Create `LICENSE` (MIT, copy verbatim)**

```
MIT License

Copyright (c) 2026 sashaflyer

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 4: Create stub `README.md`**

```markdown
# news-aggregator

Personal AI crypto research aggregator. Delivers a daily Telegram digest of
crypto signals from Reddit and Polymarket, synthesized by OpenAI.

Full docs added in Task 20. See `docs/superpowers/specs/` for the design.
```

- [ ] **Step 5: Create empty `data/.gitkeep`**

```
```

- [ ] **Step 6: Initialize git and commit**

```powershell
git init
git add .gitignore LICENSE pyproject.toml README.md data/.gitkeep
git commit -m "chore: project bootstrap"
```

Expected: clean working tree.

- [ ] **Step 7: Create venv and install dev deps**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -e ".[dev]"
```

Expected: install succeeds, `python -c "import telegram, apscheduler, httpx, openai, pydantic"` prints nothing (no error).

- [ ] **Step 8: Smoke-test pytest discovery**

Create `tests/__init__.py` (empty) and `tests/test_smoke.py`:

```python
def test_smoke():
    assert 1 + 1 == 2
```

Run: `pytest -v`
Expected: 1 passed.

- [ ] **Step 9: Commit smoke test**

```powershell
git add tests/
git commit -m "test: add smoke test"
```

---

## Task 2: Vendor upstream `last30days` library

**Files:**
- Create: `aggregator/__init__.py`
- Create: `aggregator/vendor/__init__.py`
- Create: `aggregator/vendor/last30days/` (multiple files copied from upstream)
- Create: `aggregator/vendor/last30days/LICENSE`
- Create: `aggregator/vendor/last30days/UPSTREAM.md`
- Create: `scripts/vendor_last30days.py`

- [ ] **Step 1: Create the package skeleton**

Write `aggregator/__init__.py`:
```python
"""news-aggregator: Telegram-delivered crypto research digest."""
__version__ = "0.1.0"
```

Write `aggregator/vendor/__init__.py`:
```python
```

- [ ] **Step 2: Write the vendoring script**

Create `scripts/vendor_last30days.py`:

```python
"""
Vendor selected modules from mvanhorn/last30days-skill.

Run: python scripts/vendor_last30days.py [<commit-sha>]
If sha is omitted, defaults to 'main' (records actual resolved SHA in UPSTREAM.md).
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

REPO = "mvanhorn/last30days-skill"
BASE_PATH = "skills/last30days/scripts/lib"
DEST = Path(__file__).resolve().parent.parent / "aggregator" / "vendor" / "last30days"

MODULES = [
    "__init__.py",
    "reddit.py",
    "reddit_public.py",
    "reddit_enrich.py",
    "polymarket.py",
    "dedupe.py",
    "cluster.py",
    "rerank.py",
    "signals.py",
    "relevance.py",
    "normalize.py",
    "schema.py",
    "store.py",
    "http.py",
    "dates.py",
    "env.py",
    "log.py",
]


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as r:
        return r.read()


def resolve_sha(ref: str) -> str:
    import json
    data = json.loads(fetch(f"https://api.github.com/repos/{REPO}/commits/{ref}").decode())
    return data["sha"]


def main() -> None:
    ref = sys.argv[1] if len(sys.argv) > 1 else "main"
    sha = resolve_sha(ref)
    print(f"Vendoring {REPO}@{sha}")
    DEST.mkdir(parents=True, exist_ok=True)

    for mod in MODULES:
        url = f"https://raw.githubusercontent.com/{REPO}/{sha}/{BASE_PATH}/{mod}"
        print(f"  fetching {mod}")
        (DEST / mod).write_bytes(fetch(url))

    license_url = f"https://raw.githubusercontent.com/{REPO}/{sha}/LICENSE"
    (DEST / "LICENSE").write_bytes(fetch(license_url))

    (DEST / "UPSTREAM.md").write_text(
        f"# Upstream provenance\n\n"
        f"Source: https://github.com/{REPO}\n"
        f"Commit: {sha}\n"
        f"Path: {BASE_PATH}/\n\n"
        f"## Vendored modules\n\n"
        + "\n".join(f"- `{m}`" for m in MODULES)
        + "\n\n## Modifications\n\n"
        f"- Import paths adjusted: any intra-package import within these modules\n"
        f"  (e.g., `from .http import ...`) continues to resolve because we kept the\n"
        f"  package layout. Imports of upstream-only modules NOT vendored here will\n"
        f"  fail and must be surgically removed or stubbed when first encountered.\n"
        f"- No logic changes.\n",
        encoding="utf-8",
    )
    print(f"Done. Wrote {len(MODULES)} modules + LICENSE + UPSTREAM.md to {DEST}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the vendoring script**

```powershell
python scripts/vendor_last30days.py
```

Expected: prints "Vendoring mvanhorn/last30days-skill@<sha>", lists each module fetched, finishes with "Done." File `aggregator/vendor/last30days/UPSTREAM.md` exists.

- [ ] **Step 4: Smoke-import each vendored module**

Create `tests/vendor/__init__.py` (empty) and `tests/vendor/test_imports.py`:

```python
"""Confirm vendored modules import. If an upstream module references another
upstream module we did NOT vendor, this test will surface it loudly."""
import importlib
import pytest

MODULES = [
    "aggregator.vendor.last30days.reddit",
    "aggregator.vendor.last30days.reddit_public",
    "aggregator.vendor.last30days.reddit_enrich",
    "aggregator.vendor.last30days.polymarket",
    "aggregator.vendor.last30days.dedupe",
    "aggregator.vendor.last30days.cluster",
    "aggregator.vendor.last30days.rerank",
    "aggregator.vendor.last30days.signals",
    "aggregator.vendor.last30days.relevance",
    "aggregator.vendor.last30days.normalize",
    "aggregator.vendor.last30days.schema",
    "aggregator.vendor.last30days.store",
    "aggregator.vendor.last30days.http",
    "aggregator.vendor.last30days.dates",
    "aggregator.vendor.last30days.env",
    "aggregator.vendor.last30days.log",
]


@pytest.mark.parametrize("modname", MODULES)
def test_import(modname):
    importlib.import_module(modname)
```

- [ ] **Step 5: Run the import test**

```powershell
pytest tests/vendor/test_imports.py -v
```

Expected: most parametrized cases pass. **If any fail with `ModuleNotFoundError` for an upstream module we did not vendor**, list those missing modules. The fix is to either (a) add them to `MODULES` in `scripts/vendor_last30days.py` and re-run, or (b) edit the offending vendored file to remove the import if it's used only by a code path we don't exercise. Note the edit in `UPSTREAM.md` if (b).

Iterate until all parametrized cases pass.

- [ ] **Step 6: Commit**

```powershell
git add scripts/ aggregator/__init__.py aggregator/vendor/ tests/vendor/
git commit -m "feat: vendor mvanhorn/last30days-skill lib modules"
```

---

## Task 3: Configuration loading

**Files:**
- Create: `config.example.toml`
- Create: `.env.example`
- Create: `aggregator/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_config.py`:

```python
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
```

- [ ] **Step 2: Run test, confirm it fails**

```powershell
pytest tests/test_config.py -v
```

Expected: ImportError on `aggregator.config`.

- [ ] **Step 3: Implement `aggregator/config.py`**

```python
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
```

- [ ] **Step 4: Run test, confirm it passes**

```powershell
pytest tests/test_config.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Create `config.example.toml` (matches spec §13.1)**

```toml
[schedule]
timezone = "Europe/Warsaw"

[crypto.general]
subreddits = ["CryptoCurrency", "CryptoMarkets", "ethfinance"]
polymarket_tags = ["crypto"]
top_n = 15
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
```

- [ ] **Step 6: Create `.env.example` (matches spec §13.2)**

```
OPENAI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=news-aggregator/0.1 by <your_reddit_username>
NEWS_AGGREGATOR_DATA_DIR=
```

- [ ] **Step 7: Verify example config parses**

```powershell
python -c "from aggregator.config import load_config; print(load_config('config.example.toml'))"
```

Expected: prints a `Config(...)` object, no exception.

- [ ] **Step 8: Commit**

```powershell
git add aggregator/config.py tests/test_config.py config.example.toml .env.example
git commit -m "feat: config loader with validation"
```

---

## Task 4: Storage layer (schema + topic seeding)

**Files:**
- Create: `aggregator/storage.py`
- Create: `tests/test_storage.py`

The vendored `store.py` provides `topics`, `research_runs`, `findings`, `finding_sightings`, `settings`, `findings_fts`. We add `digest_log` and `source_health` and a thin `Storage` class to encapsulate access.

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage.py`:

```python
import sqlite3
from datetime import datetime, timezone

import pytest

from aggregator.storage import Storage


@pytest.fixture
def storage(tmp_path):
    db = tmp_path / "test.db"
    s = Storage(str(db))
    s.init_schema()
    return s


def test_added_tables_exist(storage):
    with sqlite3.connect(storage.path) as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "digest_log" in names
    assert "source_health" in names


def test_seed_topics_idempotent(storage):
    storage.seed_topics(
        general_subreddits=["CryptoCurrency"],
        general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL", "SUI"],
        watchlist_schedule="0 8 * * *",
    )
    storage.seed_topics(  # second call must not duplicate
        general_subreddits=["CryptoCurrency"],
        general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL", "SUI"],
        watchlist_schedule="0 8 * * *",
    )
    topics = storage.list_topics()
    names = sorted(t["name"] for t in topics)
    assert names == ["crypto_general", "crypto_watchlist"]


def test_record_source_health_failure_then_success(storage):
    now = datetime.now(timezone.utc)
    storage.record_source_failure("reddit", "boom", at=now)
    storage.record_source_failure("reddit", "boom again", at=now)
    h = storage.get_source_health("reddit")
    assert h["consecutive_failures"] == 2
    assert h["last_error_message"] == "boom again"

    storage.record_source_success("reddit", at=now)
    h = storage.get_source_health("reddit")
    assert h["consecutive_failures"] == 0
    assert h["last_success_at"] is not None


def test_record_run_and_digest_log(storage):
    now = datetime.now(timezone.utc)
    run_id = storage.start_run("crypto_general", trigger="scheduled", at=now)
    storage.finish_run(run_id, status="ok", items_fetched=10, items_delivered=5, at=now)
    storage.log_digest(run_id=run_id, topic_id="crypto_general",
                       message_text="hello", telegram_message_ids=[1, 2], at=now)
    last = storage.last_run("crypto_general")
    assert last["status"] == "ok"
    assert last["items_delivered"] == 5
```

- [ ] **Step 2: Run test, confirm it fails**

```powershell
pytest tests/test_storage.py -v
```

Expected: ImportError on `aggregator.storage`.

- [ ] **Step 3: Implement `aggregator/storage.py`**

```python
"""Storage wrapper. Owns SQLite schema migrations + reads/writes used by pipeline and bot."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Vendored schema initializer. We call it for the upstream tables, then add our own.
from aggregator.vendor.last30days import store as upstream_store

_ADDED_SCHEMA = """
CREATE TABLE IF NOT EXISTS digest_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL,
    topic_id        TEXT NOT NULL,
    sent_at         TIMESTAMP NOT NULL,
    message_text    TEXT NOT NULL,
    telegram_message_ids TEXT
);

CREATE TABLE IF NOT EXISTS source_health (
    source              TEXT PRIMARY KEY,
    last_attempt_at     TIMESTAMP,
    last_success_at     TIMESTAMP,
    last_error_at       TIMESTAMP,
    last_error_message  TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id        TEXT NOT NULL,
    started_at      TIMESTAMP NOT NULL,
    finished_at     TIMESTAMP,
    status          TEXT,
    items_fetched   INTEGER,
    items_delivered INTEGER,
    error_message   TEXT,
    trigger         TEXT NOT NULL
);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class Storage:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        with self._conn() as conn:
            # Upstream schema. If its API differs, adapt this line — see UPSTREAM.md.
            try:
                upstream_store.init_db(conn)  # type: ignore[attr-defined]
            except AttributeError:
                # Fallback: many upstream stores accept a path/db arg. Try common shapes.
                if hasattr(upstream_store, "ensure_schema"):
                    upstream_store.ensure_schema(conn)  # type: ignore[attr-defined]
                else:
                    raise RuntimeError(
                        "Vendored store.py does not expose init_db/ensure_schema. "
                        "Open aggregator/vendor/last30days/store.py and call its "
                        "schema-creation function here."
                    )
            conn.executescript(_ADDED_SCHEMA)

    def seed_topics(
        self,
        *,
        general_subreddits: list[str],
        general_polymarket_tags: list[str],
        general_schedule: str,
        watchlist_symbols: list[str],
        watchlist_schedule: str,
    ) -> None:
        general_queries = json.dumps({
            "subreddits": general_subreddits,
            "polymarket_tags": general_polymarket_tags,
        })
        watchlist_queries = json.dumps({"symbols": watchlist_symbols})
        with self._conn() as conn:
            for name, queries, schedule in [
                ("crypto_general", general_queries, general_schedule),
                ("crypto_watchlist", watchlist_queries, watchlist_schedule),
            ]:
                row = conn.execute(
                    "SELECT name FROM topics WHERE name = ?", (name,)
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO topics(name, search_queries, schedule, enabled) "
                        "VALUES (?, ?, ?, 1)",
                        (name, queries, schedule),
                    )
                else:
                    conn.execute(
                        "UPDATE topics SET search_queries = ?, schedule = ?, enabled = 1 "
                        "WHERE name = ?",
                        (queries, schedule, name),
                    )

    def list_topics(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM topics ORDER BY name")]

    # --- source_health ----------------------------------------------------

    def record_source_failure(self, source: str, msg: str, *, at: datetime) -> None:
        ts = _iso(at)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO source_health(source, last_attempt_at, last_error_at,
                    last_error_message, consecutive_failures)
                   VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(source) DO UPDATE SET
                       last_attempt_at = excluded.last_attempt_at,
                       last_error_at = excluded.last_error_at,
                       last_error_message = excluded.last_error_message,
                       consecutive_failures = source_health.consecutive_failures + 1""",
                (source, ts, ts, msg),
            )

    def record_source_success(self, source: str, *, at: datetime) -> None:
        ts = _iso(at)
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO source_health(source, last_attempt_at, last_success_at,
                    consecutive_failures)
                   VALUES (?, ?, ?, 0)
                   ON CONFLICT(source) DO UPDATE SET
                       last_attempt_at = excluded.last_attempt_at,
                       last_success_at = excluded.last_success_at,
                       consecutive_failures = 0""",
                (source, ts, ts),
            )

    def get_source_health(self, source: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM source_health WHERE source = ?", (source,)
            ).fetchone()
            return dict(row) if row else None

    def all_source_health(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM source_health ORDER BY source"
            )]

    # --- runs -------------------------------------------------------------

    def start_run(self, topic_id: str, *, trigger: str, at: datetime) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO run_history(topic_id, started_at, trigger) VALUES (?, ?, ?)",
                (topic_id, _iso(at), trigger),
            )
            return cur.lastrowid

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        items_fetched: int = 0,
        items_delivered: int = 0,
        error_message: str | None = None,
        at: datetime,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE run_history SET finished_at = ?, status = ?, items_fetched = ?,
                    items_delivered = ?, error_message = ? WHERE id = ?""",
                (_iso(at), status, items_fetched, items_delivered, error_message, run_id),
            )

    def last_run(self, topic_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM run_history WHERE topic_id = ? AND finished_at IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (topic_id,),
            ).fetchone()
            return dict(row) if row else None

    # --- digest_log -------------------------------------------------------

    def log_digest(
        self,
        *,
        run_id: int,
        topic_id: str,
        message_text: str,
        telegram_message_ids: list[int],
        at: datetime,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO digest_log(run_id, topic_id, sent_at, message_text,
                    telegram_message_ids)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, topic_id, _iso(at), message_text,
                 json.dumps(telegram_message_ids)),
            )
```

- [ ] **Step 4: Run test, confirm it passes**

```powershell
pytest tests/test_storage.py -v
```

Expected: 4 passed.

If `init_db` / `ensure_schema` is not what the vendored `store.py` exposes, open `aggregator/vendor/last30days/store.py`, find its schema-init function, and replace the call in `Storage.init_schema()`. Record the actual function name in `UPSTREAM.md`.

- [ ] **Step 5: Commit**

```powershell
git add aggregator/storage.py tests/test_storage.py
git commit -m "feat: storage layer with added digest_log + source_health tables"
```

---

## Task 5: Source adapter contract and Item model

**Files:**
- Create: `aggregator/sources/__init__.py`
- Create: `aggregator/sources/base.py`
- Create: `tests/sources/__init__.py`
- Create: `tests/sources/test_base.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/sources/test_base.py
from datetime import datetime, timezone

from aggregator.sources.base import Item


def test_item_roundtrip_to_dict():
    item = Item(
        id="reddit:t3_abc",
        source="reddit",
        title="hello",
        url="https://reddit.com/abc",
        text="body",
        created_at=datetime(2026, 5, 24, 8, 0, tzinfo=timezone.utc),
        engagement_raw={"upvotes": 100},
        metadata={"subreddit": "CryptoCurrency"},
    )
    d = item.to_dict()
    assert d["id"] == "reddit:t3_abc"
    assert d["engagement_raw"]["upvotes"] == 100
    assert isinstance(d["created_at"], str)  # ISO string for downstream consumers
```

- [ ] **Step 2: Run test, confirm it fails**

```powershell
pytest tests/sources/test_base.py -v
```

Expected: ImportError.

- [ ] **Step 3: Implement `aggregator/sources/base.py`**

```python
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
    name: str  # set by subclass: "reddit" | "polymarket"

    @abstractmethod
    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        """Return items relevant to the topic's queries.

        `queries` is the JSON-decoded `topics.search_queries` for the topic
        being processed (e.g., {"subreddits": [...], "polymarket_tags": [...]}).
        Adapters use whatever subset they understand.
        """
```

- [ ] **Step 4: Create `aggregator/sources/__init__.py`**

```python
from aggregator.sources.base import Item, Source

__all__ = ["Item", "Source"]
```

- [ ] **Step 5: Run test, confirm it passes**

```powershell
pytest tests/sources/test_base.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```powershell
git add aggregator/sources/ tests/sources/
git commit -m "feat: source adapter contract + Item model"
```

---

## Task 6: Reddit source adapter

**Files:**
- Create: `aggregator/sources/reddit.py`
- Create: `tests/sources/fixtures/reddit_subreddit_hot.json`
- Create: `tests/sources/test_reddit.py`

The vendored `reddit_public.py` / `reddit.py` modules do the heavy lifting. Our adapter is a thin wrapper that translates upstream's output shape into our `Item`.

- [ ] **Step 1: Inspect upstream API surface**

Open `aggregator/vendor/last30days/reddit_public.py` (and `reddit.py`). Identify the function(s) that fetch posts for a list of subreddits or for a search query, and note the return shape (likely a list of dicts).

Record the chosen function names as comments in `aggregator/sources/reddit.py` (next step) so the wrapper is auditable.

- [ ] **Step 2: Capture a fixture from a real Reddit response**

Once you have Reddit OAuth set up, run:

```powershell
python -c "
import json
from aggregator.vendor.last30days import reddit_public as r
# Replace 'fetch_subreddit_hot' with the actual upstream function name.
data = r.fetch_subreddit_hot('CryptoCurrency', limit=5)
open('tests/sources/fixtures/reddit_subreddit_hot.json','w').write(json.dumps(data, indent=2, default=str))
"
```

If you do not yet have Reddit credentials, hand-craft a minimal fixture matching the upstream shape (look at the upstream source to determine the keys it returns). The fixture must include at least: post id, title, url, body, created timestamp, upvote count, comment count, author, subreddit.

- [ ] **Step 3: Write the failing test**

```python
# tests/sources/test_reddit.py
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aggregator.sources.reddit import RedditSource

FIXTURE = Path(__file__).parent / "fixtures" / "reddit_subreddit_hot.json"


@pytest.mark.asyncio
async def test_fetch_returns_items_from_subreddits():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    # Patch the upstream function the adapter calls. Adjust the dotted path
    # to match the function chosen in Step 1.
    with patch("aggregator.sources.reddit._fetch_subreddit", return_value=fixture):
        src = RedditSource()
        items = await src.fetch({
            "subreddits": ["CryptoCurrency"],
            "polymarket_tags": [],  # ignored by RedditSource
        })

    assert len(items) > 0
    assert all(it.source == "reddit" for it in items)
    assert all(it.id.startswith("reddit:") for it in items)
    assert all(it.url.startswith("http") for it in items)
    assert all("upvotes" in it.engagement_raw or "score" in it.engagement_raw
               for it in items)


@pytest.mark.asyncio
async def test_fetch_handles_empty_subreddit_list():
    src = RedditSource()
    items = await src.fetch({"subreddits": []})
    assert items == []


@pytest.mark.asyncio
async def test_fetch_with_symbol_queries():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with patch("aggregator.sources.reddit._search_reddit", return_value=fixture):
        src = RedditSource()
        items = await src.fetch({"symbols": ["SOL"]})
    assert all(it.source == "reddit" for it in items)
```

- [ ] **Step 4: Run test, confirm it fails**

```powershell
pytest tests/sources/test_reddit.py -v
```

Expected: ImportError.

- [ ] **Step 5: Implement `aggregator/sources/reddit.py`**

```python
"""Reddit source adapter. Wraps vendored reddit_public / reddit modules."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from aggregator.sources.base import Item, Source
from aggregator.vendor.last30days import reddit_public as _upstream

# Module-level indirection so tests can patch a stable symbol.
# Replace the right-hand sides with the actual upstream function names found in Step 1.
_fetch_subreddit = _upstream.fetch_subreddit_hot  # type: ignore[attr-defined]
_search_reddit = _upstream.search  # type: ignore[attr-defined]


def _to_item(raw: dict[str, Any]) -> Item:
    """Map an upstream Reddit post dict into our Item.

    Upstream returns at minimum:
      id, title, url, selftext (body), created_utc (epoch seconds OR iso),
      score (upvotes), num_comments, author, subreddit
    Adjust key names to whatever upstream actually returns; verify with the fixture.
    """
    created = raw.get("created_utc") or raw.get("created_at")
    if isinstance(created, (int, float)):
        created_dt = datetime.fromtimestamp(created, tz=timezone.utc)
    elif isinstance(created, str):
        created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    else:
        created_dt = datetime.now(tz=timezone.utc)

    native_id = raw.get("id") or raw.get("name") or raw["url"]
    return Item(
        id=f"reddit:{native_id}",
        source="reddit",
        title=raw.get("title", ""),
        url=raw.get("url") or raw.get("permalink", ""),
        text=raw.get("selftext") or raw.get("body") or "",
        created_at=created_dt,
        engagement_raw={
            "upvotes": raw.get("score", raw.get("ups", 0)),
            "comments": raw.get("num_comments", 0),
        },
        metadata={
            "subreddit": raw.get("subreddit", ""),
            "author": raw.get("author", ""),
        },
    )


class RedditSource(Source):
    name = "reddit"

    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        subs = queries.get("subreddits") or []
        symbols = queries.get("symbols") or []
        if not subs and not symbols:
            return []

        items: list[Item] = []

        # Subreddit hot lists (general topic case).
        for sub in subs:
            raw = await asyncio.to_thread(_fetch_subreddit, sub, limit=25)
            items.extend(_to_item(r) for r in raw)

        # Symbol-targeted search (watchlist topic case).
        for sym in symbols:
            raw = await asyncio.to_thread(_search_reddit, sym, limit=15)
            items.extend(_to_item(r) for r in raw)

        return items
```

- [ ] **Step 6: Run test, iterate**

```powershell
pytest tests/sources/test_reddit.py -v
```

If the test fails because the upstream function names differ from `fetch_subreddit_hot` / `search`, update the two module-level indirections at the top of `aggregator/sources/reddit.py` and the patch targets in the test. If `_to_item` field names don't match the fixture, adjust `_to_item`. Iterate until 3 passed.

- [ ] **Step 7: Commit**

```powershell
git add aggregator/sources/reddit.py tests/sources/test_reddit.py tests/sources/fixtures/reddit_subreddit_hot.json
git commit -m "feat: Reddit source adapter wrapping vendored reddit_public"
```

---

## Task 7: Polymarket source adapter

**Files:**
- Create: `aggregator/sources/polymarket.py`
- Create: `tests/sources/fixtures/polymarket_crypto.json`
- Create: `tests/sources/test_polymarket.py`

- [ ] **Step 1: Inspect upstream `polymarket.py` and capture a fixture**

Open `aggregator/vendor/last30days/polymarket.py`. Note the function that fetches markets by tag (e.g., `fetch_by_tag` or `search`). Capture a fixture:

```powershell
python -c "
import json
from aggregator.vendor.last30days import polymarket as p
# Replace 'fetch_by_tag' with the actual upstream function name.
data = p.fetch_by_tag('crypto', limit=10)
open('tests/sources/fixtures/polymarket_crypto.json','w').write(json.dumps(data, indent=2, default=str))
"
```

If no network, hand-craft a fixture with: market id, question, url, description, end_date, volume, current odds.

- [ ] **Step 2: Write the failing test**

```python
# tests/sources/test_polymarket.py
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aggregator.sources.polymarket import PolymarketSource

FIXTURE = Path(__file__).parent / "fixtures" / "polymarket_crypto.json"


@pytest.mark.asyncio
async def test_fetch_by_tag_returns_items():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with patch("aggregator.sources.polymarket._fetch_by_tag", return_value=fixture):
        src = PolymarketSource()
        items = await src.fetch({"polymarket_tags": ["crypto"]})
    assert len(items) > 0
    assert all(it.source == "polymarket" for it in items)
    assert all(it.id.startswith("polymarket:") for it in items)


@pytest.mark.asyncio
async def test_fetch_with_symbols_filters_by_question():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with patch("aggregator.sources.polymarket._fetch_by_tag", return_value=fixture):
        src = PolymarketSource()
        items = await src.fetch({"symbols": ["BTC"]})
    for it in items:
        assert "BTC" in it.title.upper() or "BTC" in it.text.upper()


@pytest.mark.asyncio
async def test_fetch_handles_empty_queries():
    src = PolymarketSource()
    assert await src.fetch({}) == []
```

- [ ] **Step 3: Run test, confirm it fails**

```powershell
pytest tests/sources/test_polymarket.py -v
```

- [ ] **Step 4: Implement `aggregator/sources/polymarket.py`**

```python
"""Polymarket source adapter. Wraps vendored polymarket module."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from aggregator.sources.base import Item, Source
from aggregator.vendor.last30days import polymarket as _upstream

# Adjust to match the actual upstream function name found in Task 7 Step 1.
_fetch_by_tag = _upstream.fetch_by_tag  # type: ignore[attr-defined]


def _to_item(raw: dict[str, Any]) -> Item:
    end_date = raw.get("end_date") or raw.get("endDate")
    if isinstance(end_date, str):
        try:
            created_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
        except ValueError:
            created_dt = datetime.now(tz=timezone.utc)
    else:
        created_dt = datetime.now(tz=timezone.utc)

    native_id = raw.get("id") or raw.get("slug") or raw["url"]
    question = raw.get("question") or raw.get("title") or ""
    description = raw.get("description") or ""
    return Item(
        id=f"polymarket:{native_id}",
        source="polymarket",
        title=question,
        url=raw.get("url") or f"https://polymarket.com/event/{raw.get('slug', '')}",
        text=description,
        created_at=created_dt,
        engagement_raw={
            "volume": float(raw.get("volume", 0)),
            "liquidity": float(raw.get("liquidity", 0)),
            "outcomes": raw.get("outcomes") or raw.get("outcome_prices") or [],
        },
        metadata={
            "tags": raw.get("tags") or [],
            "end_date": end_date,
        },
    )


class PolymarketSource(Source):
    name = "polymarket"

    async def fetch(self, queries: dict[str, Any]) -> list[Item]:
        tags = queries.get("polymarket_tags") or []
        symbols = queries.get("symbols") or []
        if not tags and not symbols:
            return []

        items: list[Item] = []
        # For watchlist topic: pull all crypto markets and filter by symbol substring.
        # Cheaper than per-symbol queries against Polymarket's tag system.
        if symbols and not tags:
            tags = ["crypto"]

        for tag in tags:
            raw = await asyncio.to_thread(_fetch_by_tag, tag, limit=50)
            items.extend(_to_item(r) for r in raw)

        if symbols:
            wanted = {s.upper() for s in symbols}
            items = [
                it for it in items
                if any(s in it.title.upper() or s in it.text.upper() for s in wanted)
            ]

        return items
```

- [ ] **Step 5: Run test, iterate**

```powershell
pytest tests/sources/test_polymarket.py -v
```

Iterate field names against the fixture as in Task 6. Expected: 3 passed.

- [ ] **Step 6: Commit**

```powershell
git add aggregator/sources/polymarket.py tests/sources/test_polymarket.py tests/sources/fixtures/polymarket_crypto.json
git commit -m "feat: Polymarket source adapter wrapping vendored polymarket"
```

---

## Task 8: Pipeline orchestration (skeleton + per-source error handling)

**Files:**
- Create: `aggregator/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from aggregator.config import load_config
from aggregator.sources.base import Item
from aggregator.storage import Storage


def make_item(source: str, idx: int) -> Item:
    return Item(
        id=f"{source}:{idx}",
        source=source,
        title=f"{source} item {idx}",
        url=f"https://example.com/{source}/{idx}",
        text="body",
        created_at=datetime.now(timezone.utc),
        engagement_raw={"upvotes": 100 - idx},
        metadata={},
    )


@pytest.fixture
def cfg(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(open("config.example.toml", encoding="utf-8").read())
    return load_config(cfg_path)


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics(
        general_subreddits=["CryptoCurrency"],
        general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL", "SUI", "AVAX"],
        watchlist_schedule="0 8 * * *",
    )
    return s


@pytest.mark.asyncio
async def test_run_digest_happy_path(cfg, storage):
    from aggregator import pipeline

    reddit_items = [make_item("reddit", i) for i in range(5)]
    poly_items = [make_item("polymarket", i) for i in range(3)]

    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": reddit_items, "polymarket": poly_items}
    )), patch.object(pipeline, "_score_and_dedup",
                     side_effect=lambda items, **kw: items[: cfg.crypto_general.top_n]
    ), patch.object(pipeline, "synthesize", return_value="DIGEST TEXT"
    ), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[101])):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "ok"
    assert result.items_fetched == 8
    assert result.items_delivered >= 1
    last = storage.last_run("crypto_general")
    assert last["status"] == "ok"


@pytest.mark.asyncio
async def test_run_digest_one_source_fails(cfg, storage):
    from aggregator import pipeline

    async def fake_fetch_all(*a, **kw):
        return {"reddit": [make_item("reddit", 0)],
                "polymarket": RuntimeError("polymarket down")}

    with patch.object(pipeline, "_fetch_all", side_effect=fake_fetch_all
    ), patch.object(pipeline, "_score_and_dedup",
                     side_effect=lambda items, **kw: items
    ), patch.object(pipeline, "synthesize", return_value="DIGEST"
    ), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "partial"
    poly_health = storage.get_source_health("polymarket")
    assert poly_health is not None
    assert poly_health["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_run_digest_all_sources_fail(cfg, storage):
    from aggregator import pipeline

    async def fake_fetch_all(*a, **kw):
        return {"reddit": RuntimeError("boom"),
                "polymarket": RuntimeError("boom2")}

    with patch.object(pipeline, "_fetch_all", side_effect=fake_fetch_all
    ), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[])):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "error"
    assert storage.get_source_health("reddit")["consecutive_failures"] == 1
    assert storage.get_source_health("polymarket")["consecutive_failures"] == 1
```

- [ ] **Step 2: Run test, confirm it fails**

```powershell
pytest tests/test_pipeline.py -v
```

Expected: ImportError on `aggregator.pipeline` and/or symbols.

- [ ] **Step 3: Implement `aggregator/pipeline.py`**

```python
"""Pipeline orchestration. One callable: run_digest(topic_id, cfg, storage, trigger)."""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aggregator.config import Config
from aggregator.sources.base import Item, Source
from aggregator.sources.polymarket import PolymarketSource
from aggregator.sources.reddit import RedditSource
from aggregator.storage import Storage
from aggregator.synth import synthesize
from aggregator.delivery.telegram import send_digest

log = logging.getLogger(__name__)

# Source registry. Adding a new source: import it, add it here.
SOURCES: dict[str, Source] = {
    "reddit": RedditSource(),
    "polymarket": PolymarketSource(),
}


@dataclass
class RunResult:
    run_id: int
    status: str       # "ok" | "partial" | "error"
    items_fetched: int
    items_delivered: int


async def _fetch_all(queries: dict[str, Any]) -> dict[str, list[Item] | Exception]:
    """Fan out to every source concurrently. Return per-source result OR exception."""
    async def safe(name: str, src: Source) -> tuple[str, list[Item] | Exception]:
        try:
            return name, await src.fetch(queries)
        except Exception as e:  # noqa: BLE001
            log.exception("source %s failed", name)
            return name, e

    pairs = await asyncio.gather(*(safe(n, s) for n, s in SOURCES.items()))
    return dict(pairs)


def _score_and_dedup(items: list[Item], *, top_n: int, per_author_cap: int) -> list[Item]:
    """Score, dedup, cap. Delegates to vendored modules; failures fall back to engagement sort."""
    try:
        from aggregator.vendor.last30days import rerank, dedupe  # type: ignore[attr-defined]
        deduped = dedupe.dedupe([i.to_dict() for i in items])  # type: ignore[attr-defined]
        ranked = rerank.rerank(deduped, top_n=top_n,  # type: ignore[attr-defined]
                                per_author_cap=per_author_cap)
        # Map ranked dicts back to Items by id.
        by_id = {i.id: i for i in items}
        return [by_id[r["id"]] for r in ranked if r["id"] in by_id]
    except Exception:  # noqa: BLE001
        log.exception("vendored scoring failed; falling back to engagement sort")
        sortable = sorted(
            items,
            key=lambda i: (
                i.engagement_raw.get("upvotes", 0)
                + i.engagement_raw.get("score", 0)
                + 0.1 * i.engagement_raw.get("comments", 0)
                + 0.001 * i.engagement_raw.get("volume", 0)
            ),
            reverse=True,
        )
        return sortable[:top_n]


async def run_digest(
    topic_id: str,
    cfg: Config,
    storage: Storage,
    *,
    trigger: str = "scheduled",
) -> RunResult:
    now = datetime.now(timezone.utc)
    run_id = storage.start_run(topic_id, trigger=trigger, at=now)
    log.info("run %s started for topic %s (trigger=%s)", run_id, topic_id, trigger)

    # Resolve topic queries from DB.
    topic = next((t for t in storage.list_topics() if t["name"] == topic_id), None)
    if topic is None:
        msg = f"topic {topic_id!r} not found in DB"
        storage.finish_run(run_id, status="error", error_message=msg, at=datetime.now(timezone.utc))
        return RunResult(run_id, "error", 0, 0)

    queries = json.loads(topic["search_queries"])

    # Fetch.
    per_source = await _fetch_all(queries)
    items: list[Item] = []
    ok_count = 0
    fail_count = 0
    for name, result in per_source.items():
        attempt_at = datetime.now(timezone.utc)
        if isinstance(result, Exception):
            storage.record_source_failure(name, str(result), at=attempt_at)
            fail_count += 1
        else:
            storage.record_source_success(name, at=attempt_at)
            items.extend(result)
            ok_count += 1

    fetched = len(items)
    if ok_count == 0:
        storage.finish_run(run_id, status="error", items_fetched=0, items_delivered=0,
                           error_message="all sources failed",
                           at=datetime.now(timezone.utc))
        return RunResult(run_id, "error", 0, 0)

    # Determine top_n based on topic.
    top_n = (cfg.crypto_general.top_n if topic_id == "crypto_general"
             else cfg.crypto_watchlist.per_symbol_top_n * len(cfg.crypto_watchlist.symbols))

    ranked = _score_and_dedup(items, top_n=top_n, per_author_cap=cfg.scoring.per_author_cap)

    # Synthesize.
    try:
        message_text = synthesize(topic_id, [i.to_dict() for i in ranked], cfg=cfg)
    except Exception as e:  # noqa: BLE001
        log.exception("synthesis failed")
        message_text = f"news-aggregator: digest for {topic_id} failed during synthesis: {e}"
        msg_ids = await send_digest(message_text, topic_id=topic_id, cfg=cfg)
        storage.log_digest(run_id=run_id, topic_id=topic_id, message_text=message_text,
                           telegram_message_ids=msg_ids, at=datetime.now(timezone.utc))
        storage.finish_run(run_id, status="error", items_fetched=fetched,
                           items_delivered=0, error_message=str(e),
                           at=datetime.now(timezone.utc))
        return RunResult(run_id, "error", fetched, 0)

    # Deliver.
    msg_ids = await send_digest(message_text, topic_id=topic_id, cfg=cfg)
    storage.log_digest(run_id=run_id, topic_id=topic_id, message_text=message_text,
                       telegram_message_ids=msg_ids, at=datetime.now(timezone.utc))

    status = "partial" if fail_count > 0 else "ok"
    storage.finish_run(run_id, status=status, items_fetched=fetched,
                       items_delivered=len(ranked), at=datetime.now(timezone.utc))
    return RunResult(run_id, status, fetched, len(ranked))
```

- [ ] **Step 4: Run test, confirm it passes**

```powershell
pytest tests/test_pipeline.py -v
```

Expected: 3 passed. If any fail because `synth.synthesize` or `delivery.telegram.send_digest` don't exist yet, create empty stub modules:

```python
# aggregator/synth.py (stub for now; real impl in T10)
def synthesize(topic_id, items, *, cfg): return "stub"

# aggregator/delivery/__init__.py
# aggregator/delivery/telegram.py (stub for now; real impl in T11)
async def send_digest(text, *, topic_id, cfg): return []
```

Commit the stubs together with pipeline.

- [ ] **Step 5: Commit**

```powershell
git add aggregator/pipeline.py aggregator/synth.py aggregator/delivery/ tests/test_pipeline.py
git commit -m "feat: pipeline orchestration with per-source error handling"
```

---

## Task 9: Synthesis (OpenAI)

**Files:**
- Create: `aggregator/prompts/general_crypto.md`
- Create: `aggregator/prompts/watchlist.md`
- Create: `aggregator/prompts/__init__.py`
- Modify: `aggregator/synth.py` (replace stub)
- Create: `tests/test_synth.py`

- [ ] **Step 1: Write the prompt templates**

`aggregator/prompts/__init__.py`:
```python
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent


def load(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")
```

`aggregator/prompts/general_crypto.md`:
```markdown
You are a crypto-news editor writing a daily morning digest for one reader.

Below are the top {n_items} items from the last 24 hours, drawn from Reddit and
Polymarket and ranked by engagement. Write a concise digest in Telegram
MarkdownV2-friendly Markdown (use `*bold*`, `_italic_`; do NOT use `#` headers).

Structure:
- A one-paragraph "What moved" overview (2-3 sentences max).
- A "Top stories" section with 3-6 bullets, each a single sentence that
  conveys the news and links the source: `- Bullet text [src](url)`.
- A "Polymarket signals" section with 1-3 bullets summarizing notable
  prediction markets if any are present in the input.

Rules:
- Do NOT invent facts. Every claim must trace to an item below.
- Do NOT include items you judge low-signal even if they rank high.
- Keep total length under 1000 characters.
- Use plain ASCII hyphens, never em-dashes.

ITEMS (JSON):
```
{items_json}
```
```

`aggregator/prompts/watchlist.md`:
```markdown
You are a crypto-news editor writing a daily watchlist update for one reader.

The reader follows these symbols: {symbols}.

Below are items from the last 24 hours mentioning one or more of these
symbols. Write a concise per-symbol update in Telegram MarkdownV2-friendly
Markdown (use `*bold*`, `_italic_`; do NOT use `#` headers).

Structure: for each symbol that has items, write:

*{{SYMBOL}}*
- 1-3 single-sentence bullets summarizing what happened, each linking source:
  `- Bullet text [src](url)`

If a symbol has zero items, write: `*{{SYMBOL}}*\n- _no notable activity_`.

Rules:
- Do NOT invent facts; every claim must trace to an item below.
- Keep total length under 1200 characters.
- Use plain ASCII hyphens, never em-dashes.

ITEMS (JSON):
```
{items_json}
```
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_synth.py
import json
from unittest.mock import MagicMock, patch

import pytest

from aggregator.config import load_config


@pytest.fixture
def cfg(tmp_path):
    return load_config("config.example.toml")


@pytest.fixture
def items():
    return [
        {"id": "reddit:1", "source": "reddit", "title": "SOL up 20%",
         "url": "https://reddit.com/1", "text": "...", "created_at": "2026-05-24T00:00:00+00:00",
         "engagement_raw": {"upvotes": 500}, "metadata": {"subreddit": "solana"}},
        {"id": "polymarket:1", "source": "polymarket", "title": "Will SUI reach $5 by July?",
         "url": "https://polymarket.com/x", "text": "...", "created_at": "2026-05-24T00:00:00+00:00",
         "engagement_raw": {"volume": 50000}, "metadata": {}},
    ]


def test_synthesize_general_crypto_calls_openai(cfg, items):
    from aggregator import synth

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="DIGEST OUTPUT"))]
    fake_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50, total_tokens=150)

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        out = synth.synthesize("crypto_general", items, cfg=cfg)

    assert out == "DIGEST OUTPUT"
    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == cfg.synth.model
    assert call_kwargs["max_tokens"] == cfg.synth.max_output_tokens
    # Prompt must include the items as JSON.
    prompt = call_kwargs["messages"][0]["content"]
    assert "SOL up 20%" in prompt


def test_synthesize_watchlist_includes_symbols(cfg, items):
    from aggregator import synth

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="*SOL*\n- foo"))]
    fake_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        out = synth.synthesize("crypto_watchlist", items, cfg=cfg)

    prompt = fake_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    for sym in cfg.crypto_watchlist.symbols:
        assert sym in prompt
    assert "SOL" in out


def test_synthesize_truncates_to_max_input_items(cfg):
    from aggregator import synth

    many = [
        {"id": f"r:{i}", "source": "reddit", "title": f"t{i}", "url": "u",
         "text": "", "created_at": "2026-05-24T00:00:00+00:00",
         "engagement_raw": {}, "metadata": {}}
        for i in range(200)
    ]
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="x"))]
    fake_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        synth.synthesize("crypto_general", many, cfg=cfg)

    prompt = fake_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    payload = json.loads(prompt.split("```\n")[1].split("\n```")[0])
    assert len(payload) == cfg.synth.max_input_items
```

- [ ] **Step 3: Run test, confirm it fails**

```powershell
pytest tests/test_synth.py -v
```

Expected: existing stub returns "stub", assertions fail.

- [ ] **Step 4: Implement `aggregator/synth.py` (replace stub)**

```python
"""LLM synthesis. One function: synthesize(topic_id, items, cfg) -> str."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

from aggregator.config import Config
from aggregator.prompts import load as load_prompt

log = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        _client = OpenAI(api_key=key)
    return _client


def _build_prompt(topic_id: str, items: list[dict[str, Any]], cfg: Config) -> str:
    items_json = json.dumps(items, ensure_ascii=False, indent=2)
    if topic_id == "crypto_general":
        template = load_prompt("general_crypto.md")
        return template.format(n_items=len(items), items_json=items_json)
    if topic_id == "crypto_watchlist":
        template = load_prompt("watchlist.md")
        return template.format(
            symbols=", ".join(cfg.crypto_watchlist.symbols),
            items_json=items_json,
        )
    raise ValueError(f"unknown topic_id: {topic_id!r}")


def synthesize(topic_id: str, items: list[dict[str, Any]], *, cfg: Config) -> str:
    capped = items[: cfg.synth.max_input_items]
    prompt = _build_prompt(topic_id, capped, cfg)
    log.info("synth topic=%s items=%d prompt_chars=%d",
             topic_id, len(capped), len(prompt))

    client = _get_client()
    resp = client.chat.completions.create(
        model=cfg.synth.model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=cfg.synth.max_output_tokens,
        temperature=0.3,
    )
    text = resp.choices[0].message.content or ""
    log.info("synth done tokens=%s/%s/%s",
             resp.usage.prompt_tokens, resp.usage.completion_tokens, resp.usage.total_tokens)
    return text
```

- [ ] **Step 5: Run test, confirm it passes**

```powershell
pytest tests/test_synth.py -v
```

Expected: 3 passed. The last test's JSON extraction depends on the prompt template wrapping items in a triple-backtick block; both templates do.

- [ ] **Step 6: Commit**

```powershell
git add aggregator/synth.py aggregator/prompts/ tests/test_synth.py
git commit -m "feat: OpenAI synthesis with per-topic prompt templates"
```

---

## Task 10: Telegram delivery

**Files:**
- Modify: `aggregator/delivery/telegram.py` (replace stub)
- Create: `tests/test_telegram.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_telegram.py
import os
from unittest.mock import patch

import httpx
import pytest
import respx

from aggregator.config import load_config


@pytest.fixture
def cfg():
    return load_config("config.example.toml")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")


@pytest.mark.asyncio
async def test_short_message_sent_once(cfg):
    from aggregator.delivery import telegram

    with respx.mock(base_url="https://api.telegram.org") as mock:
        route = mock.post("/botTEST_TOKEN/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 101}})
        )
        msg_ids = await telegram.send_digest("hello world", topic_id="crypto_general", cfg=cfg)
        assert msg_ids == [101]
        assert route.call_count == 1
        body = route.calls[0].request.read().decode()
        assert "hello world" in body
        assert "MarkdownV2" in body


@pytest.mark.asyncio
async def test_long_message_chunked(cfg):
    from aggregator.delivery import telegram

    text = ("p1\n\n" * 1500) + "end"   # ~6000+ chars
    with respx.mock(base_url="https://api.telegram.org") as mock:
        route = mock.post("/botTEST_TOKEN/sendMessage").mock(
            side_effect=[
                httpx.Response(200, json={"ok": True, "result": {"message_id": 201}}),
                httpx.Response(200, json={"ok": True, "result": {"message_id": 202}}),
            ]
        )
        msg_ids = await telegram.send_digest(text, topic_id="crypto_general", cfg=cfg)
        assert len(msg_ids) >= 2


@pytest.mark.asyncio
async def test_retry_on_5xx_then_success(cfg):
    from aggregator.delivery import telegram

    with respx.mock(base_url="https://api.telegram.org") as mock:
        mock.post("/botTEST_TOKEN/sendMessage").mock(
            side_effect=[
                httpx.Response(500, json={"ok": False}),
                httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
            ]
        )
        msg_ids = await telegram.send_digest("x", topic_id="crypto_general", cfg=cfg)
        assert msg_ids == [1]


@pytest.mark.asyncio
async def test_returns_empty_on_persistent_failure(cfg):
    from aggregator.delivery import telegram

    with respx.mock(base_url="https://api.telegram.org") as mock:
        mock.post("/botTEST_TOKEN/sendMessage").mock(
            return_value=httpx.Response(500, json={"ok": False})
        )
        msg_ids = await telegram.send_digest("x", topic_id="crypto_general", cfg=cfg)
        assert msg_ids == []
```

- [ ] **Step 2: Run test, confirm it fails**

```powershell
pytest tests/test_telegram.py -v
```

- [ ] **Step 3: Implement `aggregator/delivery/telegram.py` (replace stub)**

```python
"""Telegram delivery. Single function: send_digest(text, topic_id, cfg) -> list[int]."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from aggregator.config import Config

log = logging.getLogger(__name__)

_MAX_CHARS = 4000          # leave headroom under Telegram's 4096 hard limit
_RETRIES = 3
_BACKOFF_BASE = 2.0


def _chunk(text: str, limit: int = _MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut == -1:
            cut = remaining.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    total = len(chunks)
    return [f"{c}\n\n_({i + 1}/{total})_" if total > 1 else c
            for i, c in enumerate(chunks)]


async def _send_one(client: httpx.AsyncClient, token: str, chat_id: str,
                    text: str, parse_mode: str) -> int | None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    for attempt in range(1, _RETRIES + 1):
        try:
            resp = await client.post(url, json=payload, timeout=20.0)
            if resp.status_code == 200 and resp.json().get("ok"):
                return resp.json()["result"]["message_id"]
            log.warning("telegram send returned %s: %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as e:
            log.warning("telegram send error attempt=%d: %s", attempt, e)
        if attempt < _RETRIES:
            await asyncio.sleep(_BACKOFF_BASE ** attempt)
    return None


async def send_digest(text: str, *, topic_id: str, cfg: Config) -> list[int]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set; skipping send for %s",
                  topic_id)
        return []

    chunks = _chunk(text)
    ids: list[int] = []
    async with httpx.AsyncClient() as client:
        for chunk in chunks:
            mid = await _send_one(client, token, chat_id, chunk, cfg.telegram.parse_mode)
            if mid is not None:
                ids.append(mid)
            else:
                # Don't keep trying remaining chunks if one fails.
                break
    return ids
```

- [ ] **Step 4: Run test, confirm it passes**

```powershell
pytest tests/test_telegram.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```powershell
git add aggregator/delivery/telegram.py tests/test_telegram.py
git commit -m "feat: Telegram delivery with chunking and retries"
```

---

## Task 11: Bot Application + `/status` command

**Files:**
- Create: `aggregator/bot/__init__.py`
- Create: `aggregator/bot/app.py`
- Create: `aggregator/bot/commands/__init__.py`
- Create: `aggregator/bot/commands/status.py`
- Create: `tests/bot/__init__.py`
- Create: `tests/bot/test_status.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/bot/test_status.py
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aggregator.bot.commands.status import handle_status
from aggregator.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics(
        general_subreddits=["X"], general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL"], watchlist_schedule="0 8 * * *",
    )
    now = datetime.now(timezone.utc)
    rid = s.start_run("crypto_general", trigger="scheduled", at=now)
    s.finish_run(rid, status="ok", items_fetched=10, items_delivered=5, at=now)
    s.record_source_success("reddit", at=now)
    return s


def make_update(chat_id: int):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock()
    return upd


def make_ctx(storage, *, started_at, scheduler=None):
    ctx = MagicMock()
    ctx.bot_data = {
        "storage": storage,
        "started_at": started_at,
        "scheduler": scheduler,
        "authorized_chat_id": 12345,
    }
    return ctx


@pytest.mark.asyncio
async def test_status_authorized_chat_replies(storage):
    upd = make_update(chat_id=12345)
    ctx = make_ctx(storage, started_at=datetime.now(timezone.utc))
    await handle_status(upd, ctx)
    upd.message.reply_text.assert_awaited_once()
    text = upd.message.reply_text.await_args.args[0]
    assert "status" in text.lower()
    assert "crypto_general" in text
    assert "reddit" in text


@pytest.mark.asyncio
async def test_status_unauthorized_chat_ignored(storage):
    upd = make_update(chat_id=99999)
    ctx = make_ctx(storage, started_at=datetime.now(timezone.utc))
    await handle_status(upd, ctx)
    upd.message.reply_text.assert_not_awaited()
```

- [ ] **Step 2: Run test, confirm it fails**

```powershell
pytest tests/bot/test_status.py -v
```

- [ ] **Step 3: Implement the bot files**

`aggregator/bot/__init__.py`:
```python
```

`aggregator/bot/commands/__init__.py`:
```python
```

`aggregator/bot/commands/status.py`:
```python
"""/status command handler."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "never"
    return iso.replace("T", " ").split("+")[0].split(".")[0] + " UTC"


def _fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h or d: parts.append(f"{h}h")
    parts.append(f"{m}m")
    return " ".join(parts)


async def handle_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.bot_data
    authorized = data["authorized_chat_id"]
    if update.effective_chat is None or update.effective_chat.id != authorized:
        return

    storage = data["storage"]
    started_at: datetime = data["started_at"]
    scheduler: Any = data.get("scheduler")

    uptime = (datetime.now(timezone.utc) - started_at).total_seconds()
    lines = ["*news-aggregator status*", "", f"Uptime: {_fmt_uptime(uptime)}", ""]

    for topic in ("crypto_general", "crypto_watchlist"):
        last = storage.last_run(topic)
        if last:
            lines.append(
                f"Last digest ({topic}): {_fmt_dt(last['finished_at'])} "
                f"{last['status']} ({last.get('items_delivered', 0)} items)"
            )
        else:
            lines.append(f"Last digest ({topic}): never")

    if scheduler is not None:
        next_runs = []
        for job in scheduler.get_jobs():
            if job.next_run_time:
                next_runs.append(job.next_run_time)
        if next_runs:
            nxt = min(next_runs).astimezone(timezone.utc).isoformat()
            lines.append(f"Next scheduled run: {_fmt_dt(nxt)}")

    lines.append("")
    lines.append("Source health:")
    for h in storage.all_source_health():
        last_ok = _fmt_dt(h.get("last_success_at"))
        fails = h.get("consecutive_failures", 0)
        status = "ok" if fails == 0 else f"{fails} consecutive fails"
        lines.append(f"  {h['source']}: {status}  last success {last_ok}")

    await update.message.reply_text("\n".join(lines))
```

`aggregator/bot/app.py`:
```python
"""PTB Application factory."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from telegram.ext import Application, CommandHandler

from aggregator.bot.commands.status import handle_status


def build_application(*, storage, scheduler=None) -> Application:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = int(os.environ["TELEGRAM_CHAT_ID"])

    app = Application.builder().token(token).build()
    app.bot_data["storage"] = storage
    app.bot_data["scheduler"] = scheduler
    app.bot_data["authorized_chat_id"] = chat_id
    app.bot_data["started_at"] = datetime.now(timezone.utc)

    app.add_handler(CommandHandler("status", handle_status))
    # Add new commands here: app.add_handler(CommandHandler("<name>", handle_<name>))

    return app
```

- [ ] **Step 4: Run test, confirm it passes**

```powershell
pytest tests/bot/test_status.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```powershell
git add aggregator/bot/ tests/bot/
git commit -m "feat: Telegram bot Application + /status command"
```

---

## Task 12: Scheduler

**Files:**
- Create: `aggregator/scheduler.py`
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scheduler.py
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aggregator.config import load_config
from aggregator.storage import Storage


@pytest.fixture
def cfg():
    return load_config("config.example.toml")


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics(
        general_subreddits=["CryptoCurrency"], general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL"], watchlist_schedule="30 8 * * *",
    )
    return s


def test_build_scheduler_registers_one_job_per_topic(cfg, storage):
    from aggregator import scheduler as sched_mod

    with patch.object(sched_mod, "AsyncIOScheduler") as FakeSched:
        instance = MagicMock()
        FakeSched.return_value = instance
        s = sched_mod.build_scheduler(cfg, storage)

    assert s is instance
    # Two add_job calls, one per topic.
    assert instance.add_job.call_count == 2
    topic_ids = sorted(call.kwargs.get("args", call.args[1:])[0]
                       if call.args else call.kwargs["args"][0]
                       for call in instance.add_job.call_args_list)
    assert topic_ids == ["crypto_general", "crypto_watchlist"]
```

- [ ] **Step 2: Run test, confirm it fails**

```powershell
pytest tests/test_scheduler.py -v
```

- [ ] **Step 3: Implement `aggregator/scheduler.py`**

```python
"""APScheduler setup. One job per topic, cron-triggered."""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from aggregator.config import Config
from aggregator.pipeline import run_digest
from aggregator.storage import Storage

log = logging.getLogger(__name__)


async def _job(topic_id: str, cfg: Config, storage: Storage) -> None:
    try:
        result = await run_digest(topic_id, cfg, storage, trigger="scheduled")
        log.info("scheduled run %s for %s: status=%s",
                 result.run_id, topic_id, result.status)
    except Exception:  # noqa: BLE001
        log.exception("scheduled run for %s crashed", topic_id)


def build_scheduler(cfg: Config, storage: Storage) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=cfg.schedule.timezone)
    for topic in storage.list_topics():
        if not topic.get("enabled", 1):
            continue
        trigger = CronTrigger.from_crontab(topic["schedule"],
                                           timezone=cfg.schedule.timezone)
        scheduler.add_job(
            _job,
            trigger=trigger,
            args=(topic["name"], cfg, storage),
            id=f"digest_{topic['name']}",
            misfire_grace_time=3600,    # tolerate 1h late firing on restart
            replace_existing=True,
        )
        log.info("scheduled %s with cron %s in tz %s",
                 topic["name"], topic["schedule"], cfg.schedule.timezone)
    return scheduler
```

- [ ] **Step 4: Run test, confirm it passes**

```powershell
pytest tests/test_scheduler.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```powershell
git add aggregator/scheduler.py tests/test_scheduler.py
git commit -m "feat: APScheduler wiring (one cron job per topic)"
```

---

## Task 13: Main entrypoint + CLI for ad-hoc runs

**Files:**
- Create: `aggregator/__main__.py`
- Create: `tests/test_main_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_main_cli.py
import shutil
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_cli_oneshot_runs_pipeline(tmp_path, monkeypatch):
    # Copy example config into a temp working dir.
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(open("config.example.toml", encoding="utf-8").read())
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("NEWS_AGGREGATOR_DATA_DIR", str(tmp_path / "data"))

    from aggregator import __main__ as m

    with patch.object(m, "run_digest", new=AsyncMock(
        return_value=type("R", (), {"run_id": 1, "status": "ok",
                                     "items_fetched": 5, "items_delivered": 3})()
    )) as fake:
        await m.cli_run_once(topic_id="crypto_general", config_path=str(cfg_path))

    fake.assert_awaited_once()
    kwargs = fake.await_args.kwargs
    assert kwargs.get("trigger") == "command" or "command" in fake.await_args.args
```

- [ ] **Step 2: Run test, confirm it fails**

```powershell
pytest tests/test_main_cli.py -v
```

- [ ] **Step 3: Implement `aggregator/__main__.py`**

```python
"""Entrypoint. Two modes:

  python -m aggregator                          # long-running bot + scheduler
  python -m aggregator run --topic crypto_general   # one-shot pipeline run + exit
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
from pathlib import Path

from dotenv import load_dotenv

from aggregator.bot.app import build_application
from aggregator.config import load_config
from aggregator.pipeline import run_digest
from aggregator.scheduler import build_scheduler
from aggregator.storage import Storage

log = logging.getLogger(__name__)


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _resolve_data_dir(cfg_data_dir: str) -> Path:
    override = os.environ.get("NEWS_AGGREGATOR_DATA_DIR")
    return Path(override) if override else Path(cfg_data_dir)


def _bootstrap(config_path: str) -> tuple:
    load_dotenv()
    cfg = load_config(config_path)
    data_dir = _resolve_data_dir(cfg.storage.data_dir)
    storage = Storage(data_dir / "aggregator.db")
    storage.init_schema()
    storage.seed_topics(
        general_subreddits=cfg.crypto_general.subreddits,
        general_polymarket_tags=cfg.crypto_general.polymarket_tags,
        general_schedule=cfg.crypto_general.schedule,
        watchlist_symbols=cfg.crypto_watchlist.symbols,
        watchlist_schedule=cfg.crypto_watchlist.schedule,
    )
    return cfg, storage


async def cli_run_once(*, topic_id: str, config_path: str) -> None:
    _setup_logging()
    cfg, storage = _bootstrap(config_path)
    result = await run_digest(topic_id, cfg, storage, trigger="command")
    log.info("one-shot run %s for %s: status=%s items_fetched=%d items_delivered=%d",
             result.run_id, topic_id, result.status,
             result.items_fetched, result.items_delivered)


async def serve(*, config_path: str) -> None:
    _setup_logging()
    cfg, storage = _bootstrap(config_path)
    scheduler = build_scheduler(cfg, storage)
    app = build_application(storage=storage, scheduler=scheduler)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows: signal handlers not supported in asyncio. Ctrl+C still works.
            pass

    log.info("starting bot + scheduler")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    scheduler.start()
    try:
        await stop.wait()
    finally:
        log.info("shutting down")
        scheduler.shutdown(wait=False)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(prog="news-aggregator")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="run pipeline once for a topic and exit")
    run_p.add_argument("--topic", required=True,
                       choices=["crypto_general", "crypto_watchlist"])

    args = parser.parse_args()
    if args.cmd == "run":
        asyncio.run(cli_run_once(topic_id=args.topic, config_path=args.config))
    else:
        asyncio.run(serve(config_path=args.config))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test, confirm it passes**

```powershell
pytest tests/test_main_cli.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Manual smoke (long-running mode, with stub creds)**

Create a real `config.toml` (copy from example) and a `.env` with at minimum:

```
OPENAI_API_KEY=sk-fake-will-not-call
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=12345
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=news-aggregator/0.1 by tester
```

Then:
```powershell
python -m aggregator
```

Expected: log lines "scheduled crypto_general with cron 0 8 * * *", "scheduled crypto_watchlist with cron 0 8 * * *", "starting bot + scheduler". Without a real Telegram token, polling will error noisily — that's fine, Ctrl+C to exit.

- [ ] **Step 6: Run full test suite**

```powershell
pytest -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```powershell
git add aggregator/__main__.py tests/test_main_cli.py
git commit -m "feat: main entrypoint (serve + one-shot run subcommands)"
```

---

## Task 14: Deployment artifacts (systemd unit + deploy README)

**Files:**
- Create: `deploy/news-aggregator.service`
- Create: `deploy/README.md`

- [ ] **Step 1: Write `deploy/news-aggregator.service`**

```ini
[Unit]
Description=Personal AI Crypto Research Aggregator
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=news-bot
Group=news-bot
WorkingDirectory=/opt/news-aggregator
EnvironmentFile=/opt/news-aggregator/.env
Environment=NEWS_AGGREGATOR_DATA_DIR=/var/lib/news-aggregator
ExecStart=/opt/news-aggregator/.venv/bin/python -m aggregator
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Write `deploy/README.md`**

````markdown
# Deploying news-aggregator on a Linux VPS

Tested against Debian 12 / Ubuntu 22.04+. Requires Python 3.12+.

## 1. Install Python 3.12 (if not present)

Debian 12 ships with 3.11; install 3.12 from deadsnakes or compile. Adjust
`python3` to `python3.12` in the commands below if needed.

## 2. Create system user and directories

```bash
sudo useradd -r -s /usr/sbin/nologin news-bot
sudo mkdir -p /opt/news-aggregator /var/lib/news-aggregator
sudo chown -R news-bot:news-bot /opt/news-aggregator /var/lib/news-aggregator
```

## 3. Deploy code

```bash
sudo -u news-bot git clone <repo-url> /opt/news-aggregator
cd /opt/news-aggregator
sudo -u news-bot python3 -m venv .venv
sudo -u news-bot .venv/bin/pip install -e .
sudo -u news-bot python3 scripts/vendor_last30days.py
sudo -u news-bot cp config.example.toml config.toml
sudo -u news-bot cp .env.example .env
sudo -u news-bot $EDITOR /opt/news-aggregator/config.toml
sudo -u news-bot $EDITOR /opt/news-aggregator/.env
```

## 4. Install systemd unit

```bash
sudo cp deploy/news-aggregator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now news-aggregator
sudo systemctl status news-aggregator
journalctl -u news-aggregator -f
```

## 5. Verify

Send `/status` to your Telegram bot. You should receive a status reply within a
second. Wait for the next cron tick (or run `sudo -u news-bot
/opt/news-aggregator/.venv/bin/python -m aggregator --config
/opt/news-aggregator/config.toml run --topic crypto_general`) to verify a
digest arrives.

## Updating

```bash
cd /opt/news-aggregator
sudo -u news-bot git pull
sudo -u news-bot .venv/bin/pip install -e .
sudo systemctl restart news-aggregator
```

## Backing up

The only stateful file is `/var/lib/news-aggregator/aggregator.db`. Snapshot
it with your usual backup tool.
````

- [ ] **Step 3: Commit**

```powershell
git add deploy/
git commit -m "feat: systemd unit + VPS deployment README"
```

---

## Task 15: README and attribution

**Files:**
- Modify: `README.md` (replace stub)

- [ ] **Step 1: Replace `README.md`**

````markdown
# news-aggregator

Personal AI crypto research aggregator. Delivers a daily Telegram digest of
crypto signals (general + SOL/SUI/AVAX watchlist) from Reddit and Polymarket,
synthesized by OpenAI.

## How it works

A single long-running Python process runs on a Linux VPS under systemd. It
holds:

- A Telegram bot polling loop (`/status` command in v1)
- An APScheduler cron job that fires the daily digest pipeline at the
  configured time

The pipeline fetches the last 24h of items from Reddit and Polymarket,
deduplicates and ranks them by engagement, asks OpenAI to synthesize a
readable digest, and sends it to the configured Telegram chat.

## Quickstart (development on Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts/vendor_last30days.py
copy config.example.toml config.toml
copy .env.example .env
# edit config.toml (your timezone, symbols) and .env (your tokens)
python -m aggregator                  # long-running mode
python -m aggregator run --topic crypto_general    # one-shot digest
```

## Deployment

See [`deploy/README.md`](deploy/README.md) for VPS deployment with systemd.

## Configuration

Two files:

- `config.toml` — non-secret preferences (subreddits, watchlist symbols,
  schedule). Gitignored. See `config.example.toml`.
- `.env` — secrets (OpenAI key, Telegram token + chat ID, Reddit OAuth).
  Gitignored. See `.env.example`.

## Adding a new bot command

1. Create `aggregator/bot/commands/<name>.py` with
   `async def handle_<name>(update, context)`.
2. Register in `aggregator/bot/app.py`:
   `app.add_handler(CommandHandler("<name>", handle_<name>))`

## Adding a new source

1. Create `aggregator/sources/<name>.py` implementing the `Source` ABC.
2. Register in `aggregator/pipeline.SOURCES`.

## Tests

```powershell
pytest -v
```

All tests run offline.

## Attribution

This project builds on [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill)
(MIT). The fetching, scoring, deduplication, and storage modules are vendored
under [`aggregator/vendor/last30days/`](aggregator/vendor/last30days/);
upstream commit and vendoring notes are recorded in
[`UPSTREAM.md`](aggregator/vendor/last30days/UPSTREAM.md). Thanks to the
upstream authors.

## License

MIT. See [`LICENSE`](LICENSE).
````

- [ ] **Step 2: Run full test suite once more**

```powershell
pytest -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```powershell
git add README.md
git commit -m "docs: full README with usage, deployment, attribution"
```

---

## Done

The v1 system is complete. Verify against spec §20:

- `python -m aggregator` on Windows: bot reachable, `/status` returns expected fields, `python -m aggregator run --topic crypto_general` produces a Telegram digest with both sections (run `run --topic crypto_watchlist` for the second).
- After VPS deployment: survives reboot, systemd restarts on crash within 10s, delivers daily digests unattended for 7+ days.
- Test suite (~25 tests across 9 test files) passes offline.
- Adding `/ping` (smoke check): write `aggregator/bot/commands/ping.py` with `async def handle_ping(u, c): await u.message.reply_text("pong")`, add one line to `bot/app.py`, restart. The "extensibility test" from spec §20.

## Self-Review Notes

Spec coverage check (run mentally against spec sections):

- §2 Goals — covered: T8 (pipeline), T9 (synth), T10 (telegram), T11 (/status), T12 (scheduler), T14 (systemd).
- §3 Non-goals — respected; nothing beyond `/status`, no extra sources.
- §4 Architecture (one async process, PTB + APScheduler in same loop) — T11, T12, T13.
- §5 Module layout — matches; vendored layout in T2.
- §6 Data model — T4 (added tables + topic seeding); vendored schema initialized via `upstream_store.init_db` with explicit fallback if the function name differs.
- §7 Source adapters (Reddit, Polymarket, contract, per-source try/except) — T5, T6, T7, T8.
- §8 Scoring + dedup (vendored) — T8 `_score_and_dedup` calls vendored `rerank`/`dedupe` with engagement-sort fallback.
- §9 Synthesis (OpenAI, gpt-5.4-mini, single function, prompts per topic) — T9.
- §10 Delivery (httpx, chunking, retries, MarkdownV2) — T10.
- §11 `/status` command + authz + extensibility recipe — T11.
- §12 Scheduling (APScheduler per-topic cron from DB, default 08:00) — T12 with `misfire_grace_time=3600` (closes spec §19 item 3).
- §13 Configuration (config.toml + .env) — T3.
- §14 Error handling matrix — covered across T6/T7 (adapter failures), T8 (pipeline error paths), T10 (Telegram retries).
- §15 Testing (pytest offline, fixtures, mocks) — T1 onward.
- §16 Deployment (Windows venv + VPS systemd, full unit included) — T14.
- §17 Vendoring + attribution — T2 (vendoring script + UPSTREAM.md), T15 (README).
- §18 Out of scope — respected.
- §19 Open decisions deferred — addressed: (1) two-topic delivery uses separate Telegram messages by default (one per topic, since they fire as separate scheduler jobs); (2) MarkdownV2 escape strategy is delivery-time (the prompt asks for MarkdownV2-friendly Markdown; no escape pass in v1 — if the LLM emits a malformed entity, we'll catch it during the first live runs and add escaping in T10's `_send_one` then); (3) `misfire_grace_time=3600` in T12; (4) watchlist queries hit Reddit search per-symbol — confirmed in T6.
- §20 Success criteria — listed in the Done section above.

Placeholder scan: no `TODO`/`TBD`/"add error handling" left. Two "iterate until passes" instructions (T6 Step 6, T7 Step 5) are intentional: the upstream function names and field names are not knowable until the engineer reads the vendored code, but every iteration step is concrete (which symbol to rename, which test to re-run).

Type consistency: `Item` shape stable across T5/T6/T7/T8/T9; `RunResult(run_id, status, items_fetched, items_delivered)` consistent T8/T13; `Storage` method names consistent T4/T8/T11/T13; `Source.fetch(queries)` ABC consistent T5/T6/T7.
