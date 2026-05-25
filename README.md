# news-aggregator-bot

A self-hosted Telegram bot that wakes up every morning with a digest of what actually mattered overnight — pulled from Reddit, Polymarket, and Hacker News, scored by community engagement, deduplicated, enriched with the top comments, and synthesized into a short readable summary by OpenAI.

Designed for personal use: one operator, one Telegram chat, one Linux VPS, configurable topics. The defaults ship with crypto news + a SOL / SUI / AVAX watchlist + AI/ML news, but adding a new topic is a config-only change.

## Example digest

```
📰 What moved

Bitcoin closed above $200K for the first time on heavy spot ETF demand.
Reddit's mood is cautiously skeptical; Polymarket is pricing in further upside.

🎯 Top stories

• Bitcoin closed above $200K after pension-fund ETF inflows. ↗
• Top comment thread notes the inflow figure is unverified. ↗
• Solana post-mortem blames consensus failure for the 8h outage. ↗

📊 Polymarket signals

• "Will BTC reach $250K by year end?" trades at 35% with rising volume. ↗
```

(Each `↗` is a clickable link to the source.)

## Features

- **Three live sources out of the box**: Reddit (hot listings via public JSON), Polymarket (Gamma event search), Hacker News (Algolia search).
- **Community-aware**: top Reddit items get their top comments + sentiment fetched so the LLM summarizes what the community actually said, not just the headline.
- **Near-duplicate removal** within each digest (Jaccard similarity over n-grams).
- **Per-author cap** so no single redditor dominates.
- **Cross-day memory** — items delivered in any previous digest within `dedup_window_days` are filtered out, so tomorrow doesn't repeat today.
- **Heartbeat fallback** when nothing new survives the filter, so you always get a daily signal that the bot is alive.
- **Data-driven topics** — adding a new digest stream (e.g., geopolitics, climate) is a single `[topics.<id>]` block in `config.toml`. No code changes.
- **systemd-managed** with auto-restart on failure.
- **HTML-mode Telegram delivery** with a plain-text fallback if the LLM emits malformed markup. The user never sees a silent failure.
- **Bot command surface** with `/status`, `/digest`, `/topics`, and `/help` in v2 — plus a clear extension pattern (`aggregator/bot/commands/<name>.py` + one entry in the `COMMANDS` list in `app.py`). `/digest <topic_id>` triggers a real run on demand; a per-topic lock prevents it from racing the scheduler.
- **110 offline tests** covering pipeline, sources, scoring, dedup, synthesis, delivery, and the bot.

## Architecture

```
                ┌─────────────────────────────────────────────────────────┐
                │  long-running async Python process (systemd-managed)    │
                │                                                          │
  cron tick ───►│  APScheduler ──┐                                         │
                │                │                                          │
                │                ▼                                          │
                │   ┌──────── pipeline.run_digest(topic) ─────────┐        │
                │   │ fetch (reddit, polymarket, hackernews)      │        │
                │   │ → filter recently-delivered URLs            │        │
                │   │ → dedupe near-duplicates                    │        │
                │   │ → sort by engagement, per-author cap        │        │
                │   │ → enrich top reddit items with comments     │        │
                │   │ → synthesize with OpenAI (gpt-5.x-mini)     │        │
                │   │ → deliver to Telegram (HTML, with fallback) │        │
                │   │ → record delivered URLs to SQLite           │        │
                │   └──────────────────────────────────────────────┘        │
                │                                                          │
  /status ────►│  python-telegram-bot polling loop                        │
                │                                                          │
                └─────────────────────────────────────────────────────────┘
                              │
                              ▼
                       SQLite (./data/aggregator.db)
                       — topics, run_history, delivered_findings
                       — source_health, digest_log
```

## Quickstart (local development)

Requires Python 3.12+.

```bash
git clone https://github.com/sashaflyer/news-aggregator-bot.git
cd news-aggregator-bot
python3 -m venv .venv
source .venv/bin/activate         # on Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts/vendor_last30days.py    # fetches MIT-licensed upstream modules
cp config.example.toml config.toml
cp .env.example .env
# edit config.toml (your timezone) and .env (your OpenAI + Telegram tokens)
python -m aggregator run --topic crypto_general    # one-shot test
python -m aggregator                                # long-running mode
```

You need:
- An OpenAI API key (any model that supports `chat.completions` + `max_completion_tokens`).
- A Telegram bot token from [@BotFather](https://t.me/BotFather).
- Your numeric Telegram chat ID (start a chat with the bot, then `curl https://api.telegram.org/bot<TOKEN>/getUpdates` to find it).
- Reddit OAuth is **optional** — the bot uses Reddit's public JSON endpoints by default. Add OAuth credentials only if you start hitting anonymous rate limits.

## Configuration

Two files, both gitignored:

- **`config.toml`** — non-secret preferences (topics, schedule, scoring, OpenAI model). Template: `config.example.toml`.
- **`.env`** — secrets (API keys, bot tokens). Template: `.env.example`.

### Topic configuration

Each digest is one `[topics.<id>]` table. Below is the default `config.example.toml` showing all three shipping topics:

```toml
[topics.crypto_general]
kind = "general"
sources = ["reddit", "polymarket", "hackernews"]
subreddits = ["CryptoCurrency", "CryptoMarkets", "ethfinance"]
polymarket_tags = ["crypto"]
hn_keywords = ["bitcoin", "ethereum", "stablecoin", "defi"]
prompt_template = "general_crypto.md"
top_n = 15
schedule = "0 8 * * *"

[topics.crypto_watchlist]
kind = "watchlist"
sources = ["reddit", "polymarket", "hackernews"]
symbols = ["SOL", "SUI", "AVAX"]
prompt_template = "watchlist.md"
per_symbol_top_n = 5
schedule = "0 8 * * *"

[topics.ai_general]
kind = "general"
sources = ["reddit", "hackernews"]      # Polymarket skipped — thin AI markets
subreddits = ["MachineLearning", "LocalLLaMA", "singularity"]
polymarket_tags = []
hn_keywords = ["LLM", "AI", "Claude", "GPT", "Anthropic", "OpenAI", "Mistral", "Llama", "Gemini"]
prompt_template = "ai_general.md"
top_n = 15
schedule = "30 8 * * *"
```

To add a new topic, copy a block, change the id and fields, drop a matching prompt template in `aggregator/prompts/`, restart. No Python edits.

## Extending

### Adding a bot command

```python
# aggregator/bot/commands/ping.py
from aggregator.bot._authz import is_authorized

async def handle_ping(update, context):
    if not is_authorized(update, context):
        return
    await update.message.reply_text("pong")
```

Register in `aggregator/bot/app.py` by appending one entry to `COMMANDS`:
```python
COMMANDS = [
    ("status", "Bot uptime, last runs, source health",      handle_status),
    ("digest", "Run a digest now: /digest <topic_id>",      handle_digest),
    ("topics", "List configured topics, schedule, sources", handle_topics),
    ("ping",   "Reply with pong",                           handle_ping),  # new
    ("help",   "List available commands",                   handle_help),
]
```

`/help` and the Telegram `/` autocomplete menu (`setMyCommands` at startup) both read from `COMMANDS`, so the new command shows up everywhere automatically.

### Adding a source

Create `aggregator/sources/<name>.py` implementing the `Source` ABC from `aggregator/sources/base.py` — one `async def fetch(self, queries) -> list[Item]` method. Register in `aggregator/pipeline.SOURCES`.

The MIT-licensed [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill) ships clients for X/Twitter, YouTube, GitHub, Bluesky, Instagram, TikTok, and Truth Social — adding any one is mostly a thin wrapper around the vendored upstream module (see how `aggregator/sources/hn.py` does it).

### Adding a topic

See the `config.toml` example above. Pure config + one prompt template file.

## Deployment

See [`deploy/README.md`](deploy/README.md) for the full VPS install (Debian / Ubuntu, systemd unit, `news-bot` system user). High level:

```bash
sudo useradd -r -s /usr/sbin/nologin news-bot
sudo mkdir -p /opt/news-aggregator /var/lib/news-aggregator
sudo chown -R news-bot:news-bot /opt/news-aggregator /var/lib/news-aggregator
sudo -u news-bot git clone https://github.com/sashaflyer/news-aggregator-bot.git /opt/news-aggregator
cd /opt/news-aggregator
sudo -u news-bot python3 -m venv .venv && sudo -u news-bot .venv/bin/pip install -e .
sudo -u news-bot cp config.example.toml config.toml && sudo -u news-bot $EDITOR config.toml
sudo -u news-bot cp .env.example .env && sudo -u news-bot $EDITOR .env
sudo cp deploy/news-aggregator.service /etc/systemd/system/
sudo systemctl enable --now news-aggregator
journalctl -u news-aggregator -f
```

## Tests

```bash
pytest -v
```

All 77 tests run offline (sources, OpenAI, and Telegram are mocked via `respx` / `unittest.mock`).

## Status

Stable v1 / v2 personal-use codebase. Used in production on a single VPS. Not designed for multi-tenancy — one process serves one operator.

## Project layout

```
aggregator/
├── __main__.py              # entry point: bot polling + scheduler in one event loop
├── config.py                # pydantic-validated config loader
├── pipeline.py              # run_digest orchestration
├── storage.py               # SQLite layer (project tables + vendored schema)
├── scheduler.py             # APScheduler cron registration
├── synth.py                 # OpenAI synthesis (prompt build + snippet trim)
├── prompts/                 # per-topic LLM prompts
├── sources/                 # one file per source: reddit.py, polymarket.py, hn.py
├── delivery/telegram.py     # HTML mode with plain-text fallback
├── bot/
│   ├── app.py               # PTB Application factory + COMMANDS registry + publish_commands
│   ├── _authz.py            # shared chat-id authorization check
│   ├── digest_lock.py       # per-topic asyncio.Lock (scheduler ↔ /digest)
│   └── commands/            # one file per command: status.py, digest.py, topics.py, help.py
└── vendor/last30days/       # vendored upstream (MIT)
deploy/                      # systemd unit + install guide
tests/                       # 110 offline tests
```

## Attribution

This project builds on [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill) (MIT) — the fetching, scoring, deduplication, and storage modules are vendored under [`aggregator/vendor/last30days/`](aggregator/vendor/last30days/). Provenance and any local modifications are recorded in [`UPSTREAM.md`](aggregator/vendor/last30days/UPSTREAM.md). Thanks to the upstream authors for solving the hard parts.

## License

MIT. See [`LICENSE`](LICENSE).
