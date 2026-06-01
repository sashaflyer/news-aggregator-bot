<div align="center">

# 📰 news-aggregator-bot

**A self-hosted Telegram bot that delivers a twice-daily digest of what actually mattered — pulled from RSS feeds, Polymarket, and Hacker News, deduplicated, ranked by engagement, and summarized into a short, readable brief by an LLM.**

![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-yellow)
![Tests](https://img.shields.io/badge/tests-179%20passing-brightgreen)
![Delivery](https://img.shields.io/badge/delivery-Telegram-26A5E4?logo=telegram&logoColor=white)
![Status](https://img.shields.io/badge/status-running%20in%20prod-success)

</div>

---

Built for personal use: one operator, one Telegram chat, one Linux VPS, fully config-driven topics. The defaults ship with crypto news, a **SOL / SUI / AVAX / ENA** watchlist, and AI/ML news — but adding a new digest stream is a config-only change, with zero code.

## Contents

- [Example digest](#-example-digest)
- [Features](#-features)
- [Architecture](#-architecture)
- [Quickstart](#-quickstart-local-development)
- [Configuration](#-configuration)
- [Extending](#-extending)
- [Deployment](#-deployment)
- [Tests](#-tests)
- [Project layout](#-project-layout)
- [Attribution & License](#-attribution--license)

## 📬 Example digest

```
📰 What moved

Bitcoin closed above $200K for the first time on heavy spot-ETF demand;
Polymarket is pricing in further upside. Solana shipped a throughput upgrade.

🎯 Top stories

• Bitcoin closes above $200K after record pension-fund ETF inflows. ↗  (CoinDesk)
• Solana ships throughput upgrade; validators report faster finality. ↗  (Cointelegraph)
• The Block: spot-ETF net inflows hit a single-day record. ↗  (The Block)

📊 Polymarket signals

• "Will BTC reach $250K by year end?" trades at 35% with rising volume. ↗
```

*(Each `↗` is a clickable link to the source.)*

## ✨ Features

- **Three keyless live sources** — RSS feeds (broad crypto outlets + per-coin tag feeds), Polymarket (Gamma event search), and Hacker News (Algolia search). No API keys, quotas, or accounts to get revoked.
- **Per-coin watchlist** — each tracked symbol pulls from its own RSS tag feed and is bucketed by an explicit source tag, so a "Solana …" headline still lands under `SOL`.
- **Near-duplicate removal** within each digest (Jaccard similarity over n-grams).
- **Per-author cap** so no single author dominates a digest.
- **Cross-run memory** — anything delivered in a previous digest within `dedup_window_days` is filtered out, so the evening run never repeats the morning's.
- **Heartbeat fallback** when nothing new survives the filter, so you always get a signal the bot is alive.
- **Data-driven topics** — a new digest stream (geopolitics, climate, a different watchlist) is a single `[topics.<id>]` block in `config.toml`. No Python.
- **`systemd`-managed** with `Type=notify` + watchdog and auto-restart, so a wedged event loop self-heals.
- **HTML Telegram delivery** with an automatic plain-text fallback if the LLM emits malformed markup — never a silent failure.
- **Bot command surface** — `/status`, `/digest`, `/topics`, `/help`, with a one-line extension pattern. `/digest <topic_id>` triggers a real run on demand; a per-topic lock keeps it from racing the scheduler.
- **179 offline tests** covering pipeline, sources, scoring, dedup, synthesis, delivery, scheduling, and the bot.

## 📐 Architecture

```
                ┌──────────────────────────────────────────────────────────┐
                │  long-running async Python process (systemd-managed)      │
                │                                                            │
  cron tick ───►│  APScheduler ──┐                                          │
                │                │                                           │
                │                ▼                                           │
                │   ┌──────── pipeline.run_digest(topic) ──────────┐        │
                │   │ fetch (rss, polymarket, hackernews)          │        │
                │   │ → filter recently-delivered URLs             │        │
                │   │ → dedupe near-duplicates                     │        │
                │   │ → sort by engagement, per-author cap         │        │
                │   │ → synthesize with OpenAI (gpt-5.x-mini)      │        │
                │   │ → deliver to Telegram (HTML, with fallback)  │        │
                │   │ → record delivered URLs to SQLite            │        │
                │   └───────────────────────────────────────────────┘        │
                │                                                            │
  /status ────►│  python-telegram-bot polling loop                          │
                │                                                            │
                └──────────────────────────────────────────────────────────┘
                              │
                              ▼
                       SQLite (aggregator.db)
                       — topics, run_history, delivered_findings
                       — source_health, digest_log
```

## 🚀 Quickstart (local development)

Requires **Python 3.12+**.

```bash
git clone https://github.com/sashaflyer/news-aggregator-bot.git
cd news-aggregator-bot
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts/vendor_last30days.py     # fetches MIT-licensed upstream modules
cp config.example.toml config.toml
cp .env.example .env
# edit config.toml (timezone, topics) and .env (OpenAI + Telegram tokens)
python -m aggregator run --topic crypto_general   # one-shot test
python -m aggregator                               # long-running mode
```

You'll need:

- An **OpenAI API key** (any model supporting `chat.completions` + `max_completion_tokens`).
- A **Telegram bot token** from [@BotFather](https://t.me/BotFather).
- Your numeric **Telegram chat ID** (message the bot, then `curl https://api.telegram.org/bot<TOKEN>/getUpdates`).

## 🔧 Configuration

Two files, both gitignored:

| File | Holds | Template |
|------|-------|----------|
| `config.toml` | non-secret preferences (topics, schedule, scoring, model) | `config.example.toml` |
| `.env` | secrets (API keys, bot tokens) | `.env.example` |

### Topic configuration

Each digest is one `[topics.<id>]` table. The three shipping topics:

```toml
[topics.crypto_general]
kind = "general"
sources = ["rss", "polymarket", "hackernews"]
rss_feeds = [
  "https://cointelegraph.com/rss",
  "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
  "https://decrypt.co/feed",
  "https://www.theblock.co/rss.xml",
]
polymarket_tags = ["crypto", "bitcoin", "ethereum"]
hn_keywords = ["bitcoin", "ethereum", "Solana", "stablecoin", "defi"]
prompt_template = "general_crypto.md"
top_n = 15
schedule = "5 5,17 * * *"          # 05:05 & 17:05 in [schedule].timezone

[topics.crypto_watchlist]
kind = "watchlist"
sources = ["rss", "polymarket", "hackernews"]
rss_feeds = [ ... ]                # same 4 broad outlets as crypto_general, symbol-filtered
polymarket_tags = ["crypto"]       # filtered down to the watched symbols
prompt_template = "watchlist.md"
per_symbol_top_n = 5
schedule = "10 5,17 * * *"

  [[topics.crypto_watchlist.watch]]
  ticker = "SOL"
  aliases = ["Solana"]
  feeds = ["https://cointelegraph.com/rss/tag/solana"]        # curated tag feed (trusted, unfiltered)
  # Google News scoped to reputable outlets, alias-filtered to this symbol:
  search_feeds = ["https://news.google.com/rss/search?q=Solana%20(site%3Acointelegraph.com%20OR%20site%3Atheblock.co%20OR%20site%3Adecrypt.co%20OR%20site%3Acryptoslate.com)%20when%3A7d&hl=en-US&gl=US&ceid=US:en"]

  # SUI / AVAX / ENA follow the same shape, each `search_feeds` swapping in its
  # own keyword. ENA has no Cointelegraph tag, so its `feeds` uses CryptoSlate:
  [[topics.crypto_watchlist.watch]]
  ticker = "ENA"
  aliases = ["Ethena"]
  feeds = ["https://cryptoslate.com/news/ethena/feed/"]
  search_feeds = ["https://news.google.com/rss/search?q=Ethena%20(site%3A...)%20when%3A7d&hl=en-US&gl=US&ceid=US:en"]

[topics.ai_general]
kind = "general"
sources = ["polymarket", "hackernews"]
polymarket_tags = ["OpenAI", "Anthropic", "Claude", "AGI"]
hn_keywords = ["LLM", "Claude", "GPT", "Anthropic", "OpenAI", "Gemini"]
prompt_template = "ai_general.md"
top_n = 15
schedule = "0 5,17 * * *"
```

To add a topic: copy a block, change the id and fields, drop a matching prompt template in `aggregator/prompts/`, restart. No Python edits.

> **Schedule note:** cron expressions are interpreted in `[schedule].timezone`. Pin it to `"UTC"` (or any IANA zone) — the scheduler passes the configured zone explicitly, so fire times don't drift with the host clock.

## 🧩 Extending

### Add a bot command

```python
# aggregator/bot/commands/ping.py
from aggregator.bot._authz import is_authorized

async def handle_ping(update, context):
    if not is_authorized(update, context):
        return
    await update.message.reply_text("pong")
```

Register it with one line in `aggregator/bot/app.py`:

```python
COMMANDS = [
    ("status", "Bot uptime, last runs, source health",      handle_status),
    ("digest", "Run a digest now: /digest <topic_id>",      handle_digest),
    ("topics", "List configured topics, schedule, sources", handle_topics),
    ("ping",   "Reply with pong",                           handle_ping),   # new
    ("help",   "List available commands",                   handle_help),
]
```

`/help` and the Telegram `/` autocomplete menu (`setMyCommands` at startup) both read from `COMMANDS`, so the command appears everywhere automatically.

### Add a source

Create `aggregator/sources/<name>.py` implementing the `Source` ABC from `aggregator/sources/base.py` — a single `async def fetch(self, queries) -> list[Item]`. Register it in `aggregator/pipeline.SOURCES`. See `aggregator/sources/rss.py` or `hn.py` for the pattern.

### Add a topic

See the config example above — pure config plus one prompt template file.

## 📦 Deployment

See [`deploy/README.md`](deploy/README.md) for the full VPS install (Debian / Ubuntu, `systemd` unit, dedicated `news-bot` system user). High level:

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

## 🧪 Tests

```bash
pytest -q
```

All **179 tests run fully offline** — every network call (RSS, Polymarket, Hacker News, OpenAI, Telegram) is mocked via `respx` / `unittest.mock`. No keys or connectivity required to run the suite.

## 📁 Project layout

```
aggregator/
├── __main__.py              # entry point: bot polling + scheduler in one event loop
├── config.py                # pydantic-validated config loader
├── pipeline.py              # run_digest orchestration
├── storage.py               # SQLite layer (project tables + vendored schema)
├── scheduler.py             # APScheduler cron registration (timezone-explicit)
├── synth.py                 # OpenAI synthesis (prompt build + snippet trim)
├── relevance.py             # watchlist off-topic filter
├── prompts/                 # per-topic LLM prompts
├── sources/                 # one file per source: rss.py, polymarket.py, hn.py
├── delivery/telegram.py     # HTML mode with plain-text fallback
├── bot/
│   ├── app.py               # PTB Application factory + COMMANDS registry
│   ├── _authz.py            # shared chat-id authorization check
│   ├── digest_lock.py       # per-topic asyncio.Lock (scheduler ↔ /digest)
│   └── commands/            # one file per command: status.py, digest.py, topics.py, help.py
└── vendor/last30days/       # vendored upstream (MIT)
deploy/                      # systemd unit + install guide
tests/                       # 179 offline tests
```

## 📄 Attribution & License

Built on [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill) (MIT) — the fetching, scoring, deduplication, and storage modules are vendored under [`aggregator/vendor/last30days/`](aggregator/vendor/last30days/), with provenance recorded in [`UPSTREAM.md`](aggregator/vendor/last30days/UPSTREAM.md). Thanks to the upstream authors for solving the hard parts.

Released under the **MIT License** — see [`LICENSE`](LICENSE).
