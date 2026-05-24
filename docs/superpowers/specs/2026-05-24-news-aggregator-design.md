# Personal AI Crypto Research Aggregator — Design Spec

**Date:** 2026-05-24
**Author:** sashaflyer@tutanota.com
**Status:** Draft, awaiting review

## 1. Summary

A self-hosted Telegram bot that delivers a daily AI-synthesized digest of crypto signals from Reddit and Polymarket. The bot runs as a single long-running Python process on a Linux VPS under systemd. Internally it polls Telegram for commands (v1: `/status`), and an in-process scheduler triggers the daily digest pipeline. The research engine is built on vendored modules from the MIT-licensed `mvanhorn/last30days-skill` (fetchers, normalization, scoring, dedup); we add the delivery shell (Telegram, scheduling, bot, systemd integration).

## 2. Goals

- Wake up to a single Telegram message each morning summarizing the last 24h of crypto signals.
- Two-section digest: **general crypto** (broad subreddits + Polymarket crypto tag) and **watchlist** (per-symbol: SOL, SUI, AVAX).
- One bot command in v1: `/status` reports liveness, last run, next run, source health.
- Architecture must be cleanly extensible: adding bot commands, sources, or topics later should be a localized change.
- Develop and test on Windows (Python venv). Deploy to a Linux VPS via systemd. No Docker for v1.

## 3. Non-goals (v1)

- No interactive bot commands beyond `/status`.
- No support for X/Twitter, YouTube, TikTok, HN, GitHub, Bluesky, Perplexity, etc. (vendored code supports them; we just don't wire them in for v1).
- No web UI, HTML export, or push channels other than Telegram.
- No multi-user support — single-user single-chat bot.
- No real-time / sub-hourly updates.

## 4. Architecture

One long-running async Python process. Two coroutines share the same `asyncio` event loop:

1. **Telegram bot polling loop** (`python-telegram-bot` v21+ Application) — receives `/status`.
2. **APScheduler `AsyncIOScheduler`** — fires `pipeline.run_digest()` on a cron schedule.

Both call into the same modules (`sources/`, `scoring`, `synth`, `storage`, `delivery`). `pipeline.run_digest(topic_id)` is a pure callable: scheduler invokes it on the cron, future `/digest` command can invoke it on demand, tests invoke it directly.

systemd manages the process lifecycle on the VPS: `Restart=on-failure`, `RestartSec=10s`. The process catches SIGTERM for graceful shutdown.

## 5. Module Layout

```
news_aggregator/
├── aggregator/
│   ├── __init__.py
│   ├── __main__.py              # entrypoint: build bot+scheduler, run until SIGTERM
│   ├── config.py                # load config.toml + .env, validate
│   ├── pipeline.py              # run_digest(topic_id): fetch -> score -> dedup -> synth -> send
│   ├── storage.py               # thin wrapper around vendored store; adds digest_log + source_health
│   ├── scheduler.py             # APScheduler setup; registers cron jobs from config
│   ├── synth.py                 # OpenAI call, prompt templates
│   ├── delivery/
│   │   ├── __init__.py
│   │   └── telegram.py          # httpx POST /sendMessage; chunk >4096 chars
│   ├── bot/
│   │   ├── __init__.py
│   │   ├── app.py               # PTB Application factory, command registration
│   │   └── commands/
│   │       ├── __init__.py
│   │       └── status.py        # /status handler
│   └── vendor/
│       └── last30days/          # copied from upstream lib/, only modules we use
│           ├── __init__.py
│           ├── reddit.py
│           ├── reddit_public.py
│           ├── reddit_enrich.py
│           ├── polymarket.py
│           ├── dedupe.py
│           ├── cluster.py
│           ├── rerank.py
│           ├── signals.py
│           ├── relevance.py
│           ├── normalize.py
│           ├── schema.py
│           ├── store.py
│           ├── http.py
│           ├── dates.py
│           ├── env.py
│           └── log.py
├── deploy/
│   ├── news-aggregator.service  # systemd unit
│   └── README.md                # VPS install steps
├── tests/
│   ├── conftest.py
│   ├── fixtures/                # recorded JSON responses for source adapters
│   ├── test_pipeline.py
│   ├── test_scoring.py
│   ├── test_synth.py
│   ├── test_telegram.py
│   └── test_status_command.py
├── config.example.toml
├── .env.example
├── pyproject.toml
├── LICENSE                      # MIT
└── README.md                    # includes upstream attribution
```

**Why this shape:** Adding a new source is one file under `vendor/last30days/` plus one line of registration. Adding a new bot command is one file under `bot/commands/` plus one line in `app.py`. Adding a new topic is a row in `config.toml` plus (optionally) a new prompt template in `synth.py`. The pipeline and delivery code don't change.

## 6. Data Model

### 6.1 Database

Single SQLite file at `${DATA_DIR}/aggregator.db` (default `./data/aggregator.db` in dev; `/var/lib/news-aggregator/aggregator.db` on VPS). Uses the vendored schema from `vendor/last30days/store.py` plus two additions defined in `aggregator/storage.py`.

### 6.2 Vendored tables (used as-is)

- `topics` — name, search_queries (JSON), schedule (cron string), enabled flag.
- `research_runs` — token counts, duration, status, finding tallies per execution.
- `findings` — discovered items, URL-keyed, engagement + relevance scores, re-sighting update on duplicate URL.
- `finding_sightings` — append-only ledger of which findings appeared in which runs (enables delta computation).
- `settings` — key/value config store.
- `findings_fts` — FTS5 virtual table for full-text search (kept for future `/search` command).

### 6.3 Added tables

```sql
CREATE TABLE IF NOT EXISTS digest_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER NOT NULL REFERENCES research_runs(id),
    topic_id        TEXT NOT NULL,
    sent_at         TIMESTAMP NOT NULL,
    message_text    TEXT NOT NULL,
    telegram_message_ids TEXT      -- JSON array; multiple if chunked over 4096 chars
);

CREATE TABLE IF NOT EXISTS source_health (
    source              TEXT PRIMARY KEY,
    last_attempt_at     TIMESTAMP,
    last_success_at     TIMESTAMP,
    last_error_at       TIMESTAMP,
    last_error_message  TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);
```

### 6.4 Topic seeding

On first start, `aggregator/storage.py` seeds two topics if absent:

- `crypto_general` — `search_queries` = subreddit list + polymarket tag (from `config.toml`).
- `crypto_watchlist` — `search_queries` = `["SOL", "SUI", "AVAX"]`.

Watchlist symbols are config-driven; changing `config.toml` updates the row on next start.

## 7. Source Adapters

### 7.1 Reddit

Uses vendored `reddit_public.py` (preferred — auth via OAuth client credentials) and `reddit_enrich.py` for comment counts/top comments. Credentials in `.env`:
- `REDDIT_CLIENT_ID`
- `REDDIT_CLIENT_SECRET`
- `REDDIT_USER_AGENT` (format: `news-aggregator/0.1 by <reddit_username>`)

Subreddit list comes from `config.toml [crypto.general] subreddits`. Default: `CryptoCurrency`, `CryptoMarkets`, `ethfinance`.

For the watchlist topic: per-symbol Reddit search via the same module's search endpoint (e.g., `q=SOL` constrained to crypto subs).

### 7.2 Polymarket

Uses vendored `polymarket.py`. No auth. Pulls active markets matching configured tags (default `["crypto"]`) for general; per-symbol substring match against market questions for the watchlist.

### 7.3 Adapter contract

Every adapter, via vendored `normalize.py`/`schema.py`, returns dicts shaped like:

```python
{
    "id": "<source>:<native_id>",
    "source": "reddit" | "polymarket",
    "title": str,
    "url": str,
    "text": str,
    "created_at": iso8601,
    "engagement_raw": {...},        # upvotes/comments for reddit; volume/odds for polymarket
    "metadata": {...},              # subreddit, symbol tags, etc.
}
```

`pipeline.py` wraps each adapter call in try/except. On failure: log to stdout, increment `source_health.consecutive_failures`, write `last_error_*`, continue with remaining sources. A run succeeds with `status="partial"` if at least one source returned items.

## 8. Scoring & Dedup

- **Scoring** — vendored `rerank.py` + `signals.py`. Multi-factor: engagement (source-normalized z-score), freshness (decay over 24h), relevance to topic query. No custom scoring code in v1.
- **Dedup** — vendored `dedupe.py` + `cluster.py`. URL identity first, then content clustering. Per-author cap (default 3) prevents one voice dominating.
- **Filters** — `config.toml [scoring] min_score` cuts low-score items; `top_n` per section bounds what reaches the LLM.

## 9. Synthesis (LLM)

`aggregator/synth.py`:

```python
def synthesize(topic_id: str, items: list[dict]) -> str:
    """Return Markdown digest text for one topic."""
```

- Model: `gpt-5.4-mini` (configurable via `config.toml [synth] model`).
- Two prompt templates: `general_crypto.md` and `watchlist.md`. Watchlist prompt is parameterized by symbol list and produces per-symbol sub-sections.
- Input cap: top 40 items per topic by default; bounds token cost.
- Output cap: `max_output_tokens = 1200`.
- Output format: Telegram MarkdownV2-compatible (or plain Markdown then escaped at delivery time — decided in implementation).
- All OpenAI calls go through one `_call_openai(prompt, max_tokens) -> str` helper so swapping providers is a single-file change.
- Cost + token usage logged to `research_runs` (vendored schema already has the columns).

## 10. Delivery (Telegram)

`aggregator/delivery/telegram.py`:

```python
async def send_digest(message_text: str, topic_id: str) -> list[int]:
    """Send to TELEGRAM_CHAT_ID. Chunk if >4096 chars. Return Telegram message_ids."""
```

- Uses `httpx.AsyncClient` POST to `https://api.telegram.org/bot<TOKEN>/sendMessage`.
- `parse_mode=MarkdownV2` with proper escaping of special chars.
- Chunks on paragraph boundary if message exceeds 4096 chars; appends `(1/2)`, `(2/2)` etc.
- On HTTP error: retry with exponential backoff (3 attempts, 2/4/8s); on final failure, log and write to `research_runs.error_message`. Does not crash the bot.
- Returns the list of `message_id`s for storage in `digest_log.telegram_message_ids`.

**Why not use `python-telegram-bot` for sending too?** Sending is a one-shot POST; the PTB library is heavier than needed and complicates testing. The bot library is reserved for the polling/command side.

## 11. Bot Commands

### v1: `/status`

Replies with a Markdown summary:

```
*news-aggregator status*

 Uptime: 3d 14h 22m
 Last digest (crypto_general):  2026-05-24 08:01 UTC  ok  (12 items)
 Last digest (crypto_watchlist): 2026-05-24 08:01 UTC  ok  (9 items)
 Next scheduled run:             2026-05-25 08:00 UTC

 Source health:
   reddit:      ok    last success 2026-05-24 08:01
   polymarket:  ok    last success 2026-05-24 08:01
```

Implemented in `aggregator/bot/commands/status.py`. Registered in `bot/app.py` via PTB's `CommandHandler("status", handle_status)`.

Authorization: the handler checks `update.effective_chat.id == TELEGRAM_CHAT_ID` and ignores commands from any other chat. This is the only access control in v1 — the bot is single-user.

### Adding a command later

1. Create `bot/commands/<name>.py` with `async def handle_<name>(update, context)`.
2. In `bot/app.py`, add `application.add_handler(CommandHandler("<name>", handle_<name>))`.

## 12. Scheduling

`aggregator/scheduler.py` builds an `AsyncIOScheduler`. On startup it reads `topics.schedule` from the DB (cron string per topic) and registers one job per topic that calls `pipeline.run_digest(topic_id)`.

Default schedule (from `config.toml`): both topics fire at `0 8 * * *` (08:00 server timezone). Server timezone configurable via `[schedule] timezone`.

In v1 both topics fire at the same time; pipeline implementation may choose to send them as one combined Telegram message or two separate messages. Decision deferred to implementation; spec requires only that both topics' content reaches the chat.

## 13. Configuration

### 13.1 `config.example.toml`

```toml
[schedule]
timezone = "Europe/Warsaw"          # placeholder; user sets real TZ

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
data_dir = "./data"                  # overridden on VPS to /var/lib/news-aggregator
```

### 13.2 `.env.example` (secrets only)

```
OPENAI_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=news-aggregator/0.1 by <your_reddit_username>
```

`.env` is gitignored. `config.toml` is gitignored (only the example is committed) so non-secret personal preferences (your symbols, your subreddits) don't end up in version control.

## 14. Error Handling & Resilience

| Failure | Behavior |
|---|---|
| Single source fetch fails | Logged, `source_health` updated, run continues with other sources |
| All sources fail | Run marked `error`, no Telegram send, error written to `research_runs` |
| OpenAI call fails | 3 retries with backoff; on final failure, run marked `error`, Telegram receives a short "digest failed: <reason>" message instead of silence |
| Telegram send fails | 3 retries with backoff; final failure logged but does not raise (next day's run will report via `/status`) |
| Bot polling drops | PTB handles reconnection automatically |
| Process crashes | systemd `Restart=on-failure` brings it back within 10s |
| Database corruption | Out of scope for v1; SQLite is robust enough for this workload |

Logs go to stdout (systemd's journal captures them); use Python `logging` with structured `extra` fields for source name, topic, run_id.

## 15. Testing

- **pytest**, all tests offline.
- **Source adapters**: feed recorded JSON fixtures into `reddit_public.py` / `polymarket.py` (monkeypatch their HTTP layer), assert normalized output matches expected.
- **Pipeline**: mock sources + mock OpenAI + mock Telegram send; assert the full run writes the expected records to a temp SQLite.
- **Scoring**: lightweight tests around `rerank.py` behavior on synthetic inputs (confirm vendored module works as we expect).
- **Telegram chunking**: build a 9000-char message, assert two POSTs with correct continuation suffixes.
- **`/status`**: build a fake `Update` + `Context`, run handler against a seeded DB, assert the reply text contains expected fields and that commands from unauthorized chats are ignored.

No live integration tests in v1.

## 16. Deployment

### 16.1 Dev (Windows)

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
copy config.example.toml config.toml
copy .env.example .env
# edit config.toml and .env with real values
python -m aggregator
```

Bot starts polling Telegram. Scheduled job fires per the configured cron. Ctrl+C exits cleanly.

For ad-hoc testing of the digest without waiting for cron, the implementation will expose `python -m aggregator.pipeline --topic crypto_general` as a one-shot CLI that runs the pipeline once and exits (does not start the bot).

### 16.2 Prod (Linux VPS)

```
sudo useradd -r -s /usr/sbin/nologin news-bot
sudo mkdir -p /opt/news-aggregator /var/lib/news-aggregator
sudo chown -R news-bot:news-bot /opt/news-aggregator /var/lib/news-aggregator

# deploy code
sudo -u news-bot git clone <repo-url> /opt/news-aggregator
cd /opt/news-aggregator
sudo -u news-bot python3 -m venv .venv
sudo -u news-bot .venv/bin/pip install -e .
sudo -u news-bot cp config.example.toml config.toml
sudo -u news-bot cp .env.example .env
# edit /opt/news-aggregator/config.toml and .env

sudo cp deploy/news-aggregator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now news-aggregator
sudo systemctl status news-aggregator
journalctl -u news-aggregator -f
```

### 16.3 systemd unit

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
ExecStart=/opt/news-aggregator/.venv/bin/python -m aggregator
Restart=on-failure
RestartSec=10s
StandardOutput=journal
StandardError=journal
# data dir override
Environment=NEWS_AGGREGATOR_DATA_DIR=/var/lib/news-aggregator

[Install]
WantedBy=multi-user.target
```

## 17. Vendoring & Attribution

- Copy only the `lib/` modules listed in §5 from `mvanhorn/last30days-skill` (commit hash recorded in `aggregator/vendor/last30days/UPSTREAM.md`).
- Preserve any copyright/license headers in each file.
- Repo root `LICENSE` is MIT.
- `aggregator/vendor/last30days/LICENSE` contains the upstream MIT license verbatim.
- `README.md` includes: "This project builds on `mvanhorn/last30days-skill` (MIT). Vendored modules live under `aggregator/vendor/last30days/`; upstream commit recorded in `UPSTREAM.md`. Thanks to the upstream authors."
- No upstream code is modified except for: (a) import path adjustments to work as `aggregator.vendor.last30days.<module>`, (b) removal of unused functions only if needed for dead-code hygiene. All modifications noted in `UPSTREAM.md`.

## 18. Out of scope (v2+ candidates)

- Interactive commands: `/digest` (run now), `/last` (replay most recent), `/sources` (list current sources + health), `/symbol <SYM>` (one-off symbol summary), `/addsymbol`, `/rmsymbol`.
- Additional sources: HN, YouTube, X/Twitter (cost-dependent), GitHub, Bluesky.
- AI topic (separate digest schedule).
- Per-symbol digest scheduled separately from general.
- Web UI for config and history.
- Backfill/replay tooling.
- Cost dashboard.

## 19. Open decisions deferred to implementation

These don't need user input now; the implementer (plan + code) chooses based on what the data looks like in practice.

1. **One combined Telegram message vs two separate messages for the two topics** — decided after seeing real digest lengths.
2. **MarkdownV2 escape strategy** — escape at synth time (simpler prompt enforcement) or at delivery time (more forgiving of LLM drift). Try delivery-time first.
3. **APScheduler `misfire_grace_time`** — pick a value that swallows a missed run if the bot was down briefly but doesn't run a stale digest hours late.
4. **Whether watchlist queries hit Reddit `/search` per-symbol or one combined query** — depends on per-call rate-limit behavior; profile during implementation.

## 20. Success criteria for v1

- `python -m aggregator` on Windows: bot reachable, `/status` returns expected fields, scheduled run produces a Telegram digest with both general and watchlist sections.
- Deployed to VPS via systemd: survives a reboot, survives a forced crash (systemd restarts within 10s), runs unattended for at least 7 consecutive days delivering daily digests.
- Test suite (~20–40 tests) passes offline.
- A new bot command can be added by writing one file and one registration line (verified by adding a stub `/ping` during code review, then removing it).
