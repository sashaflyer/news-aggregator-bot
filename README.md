<div align="center">

# BriefBot

**Your personal AI news analyst. Twice-daily Telegram digests from RSS, Polymarket, Hacker News, and GitHub — deduplicated, ranked, and summarized by an LLM.**

![Python](https://img.shields.io/badge/python-3.12%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-yellow)
![Tests](https://img.shields.io/badge/tests-214%20passing-brightgreen)
![Delivery](https://img.shields.io/badge/delivery-Telegram-26A5E4?logo=telegram&logoColor=white)

[Quickstart](#-quickstart) · [Features](#-features) · [How it works](#-how-it-works) · [Deploy](#-deployment) · [Extend](#-extending)

</div>

---

## What is BriefBot?

BriefBot is a self-hosted Telegram bot that reads the internet so you don't have to. It pulls from **RSS feeds**, **Polymarket prediction markets**, **Hacker News**, and **GitHub trending issues** — filters out duplicates and noise, ranks by real engagement, and delivers a short, readable brief to your Telegram twice a day.

No editors. No algorithms. No paywalls. Just signal.

```
📰 What moved

Bitcoin closed above $200K on record ETF inflows. Polymarket odds
on a year-end $250K target jumped to 35%. Solana shipped a major
throughput upgrade.

🎯 Top stories

• Bitcoin closes above $200K after record pension-fund ETF inflows. ↗
• Solana ships throughput upgrade; validators report faster finality. ↗
• Spot-ETF net inflows hit a single-day record of $4.2B. ↗

📊 Polymarket signals

• "BTC above $250K by year end" trades at 35%, up 12 points on $8M volume. ↗
• "Fed cuts rates by September FOMC" sits at 62%, up 9 points week-over-week. ↗
```

Each `↗` is a clickable link to the source.

## ✨ Features

**5 config-driven topics** — no code changes needed to add more:

| Topic | What it covers | Sources |
|-------|---------------|---------|
| `ai_general` | AI/ML industry news | RSS, Polymarket, HN |
| `ai_blogs` | 18 curated tech blogs | RSS |
| `crypto_general` | Crypto market news | RSS, Polymarket, HN |
| `crypto_watchlist` | SOL, SUI, AVAX, ENA per-coin tracking | RSS, Polymarket, HN |
| `github_trending` | Trending AI/ML issues and PRs | GitHub Search API |

**What makes it different:**

- **Per-source engagement scoring** — HN points, Polymarket volume, and GitHub reactions are weighted differently using log-scaled scores with quality multipliers.
- **Near-duplicate removal** — Jaccard similarity over n-grams catches the same story across different outlets.
- **Cross-run memory** — anything delivered in the last 7 days is filtered out. Morning and evening digests never repeat.
- **Per-author cap** — no single author or outlet can dominate a digest.
- **Heartbeat fallback** — when nothing new survives, you still get a "no new items" signal so you know the bot is alive.
- **HTML delivery with fallback** — if the LLM emits malformed markup, BriefBot retries as plain text. Never a silent failure.
- **`systemd` watchdog** — a wedged event loop self-heals after 180 seconds.

**Bot commands:**

| Command | What it does |
|---------|-------------|
| `/status` | Uptime, last runs, source health |
| `/digest <topic>` | Run a digest now |
| `/topics` | List configured topics |
| `/help` | List commands |

## 🔧 How it works

```
    ┌─────────────────────────────────────────────────────┐
    │  BriefBot (long-running async Python process)       │
    │                                                     │
    │  APScheduler ──► pipeline.run_digest(topic)         │
    │                     │                               │
    │                     ├─ fetch sources (async)        │
    │                     ├─ filter delivered URLs        │
    │                     ├─ deduplicate (Jaccard)        │
    │                     ├─ score + rank (engagement)    │
    │                     ├─ synthesize (OpenAI LLM)      │
    │                     ├─ deliver (Telegram HTML)      │
    │                     └─ record to SQLite             │
    │                                                     │
    │  python-telegram-bot ◄── /status, /digest, /topics  │
    └─────────────────────────────────────────────────────┘
                          │
                          ▼
                   SQLite (aggregator.db)
```

## 🚀 Quickstart

Requires **Python 3.12+**.

```bash
git clone https://github.com/sashaflyer/briefbot.git
cd briefbot
python3 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python scripts/vendor_last30days.py    # fetches MIT-licensed upstream modules
cp config.example.toml config.toml
cp .env.example .env
```

Edit `config.toml` (topics, timezone) and `.env` (API keys), then:

```bash
python -m aggregator run --topic crypto_general   # one-shot test
python -m aggregator                               # long-running mode
```

**You'll need:**

| What | Where |
|------|-------|
| OpenAI API key | [platform.openai.com](https://platform.openai.com) |
| Telegram bot token | [@BotFather](https://t.me/BotFather) on Telegram |
| Telegram chat ID | Message your bot, then `curl https://api.telegram.org/bot<TOKEN>/getUpdates` |
| GitHub token (optional) | [github.com/settings/tokens](https://github.com/settings/tokens) — no scopes needed, for `github_trending` topic |

## 📦 Deployment

Full guide: [`deploy/README.md`](deploy/README.md)

```bash
# One-time setup
sudo useradd -r -s /usr/sbin/nologin news-bot
sudo mkdir -p /opt/news-aggregator /var/lib/news-aggregator
sudo chown -R news-bot:news-bot /opt/news-aggregator /var/lib/news-aggregator
sudo -u news-bot git clone https://github.com/sashaflyer/briefbot.git /opt/news-aggregator
cd /opt/news-aggregator
sudo -u news-bot python3 -m venv .venv && sudo -u news-bot .venv/bin/pip install -e .
sudo -u news-bot cp config.example.toml config.toml && sudo -u news-bot $EDITOR config.toml
sudo -u news-bot cp .env.example .env && sudo -u news-bot $EDITOR .env
sudo cp deploy/news-aggregator.service /etc/systemd/system/
sudo systemctl enable --now news-aggregator

# Updating
cd /opt/news-aggregator
sudo -u news-bot git pull
sudo -u news-bot .venv/bin/pip install -e .
sudo -u news-bot .venv/bin/python scripts/merge_config.py
sudo systemctl restart news-aggregator
```

## 🧩 Extending

### Add a topic (zero code)

Add a `[topics.<id>]` block to `config.toml` and a prompt template in `aggregator/prompts/`. Restart. That's it.

### Add a bot command

Create `aggregator/bot/commands/<name>.py`, register it in `aggregator/bot/app.py`:

```python
COMMANDS = [
    ("status", "Bot uptime, last runs, source health",      handle_status),
    ("digest", "Run a digest now: /digest <topic_id>",      handle_digest),
    ("topics", "List configured topics, schedule, sources", handle_topics),
    ("ping",   "Reply with pong",                           handle_ping),   # new
    ("help",   "List available commands",                   handle_help),
]
```

`/help` and Telegram's `/` autocomplete both read from `COMMANDS` automatically.

### Add a source

Create `aggregator/sources/<name>.py` implementing the `Source` ABC. Register in `pipeline.SOURCES`. See `rss.py` or `github.py` for the pattern.

## 🧪 Tests

```bash
pytest -q
```

**214 tests, fully offline.** Every network call (RSS, Polymarket, HN, GitHub, OpenAI, Telegram) is mocked. No keys or connectivity required.

## 📁 Project layout

```
aggregator/
├── __main__.py           # entry: bot polling + scheduler in one event loop
├── pipeline.py           # run_digest orchestration
├── config.py             # pydantic-validated config loader
├── storage.py            # SQLite layer
├── scheduler.py          # APScheduler cron (timezone-explicit)
├── synth.py              # OpenAI synthesis
├── relevance.py          # watchlist off-topic filter
├── sources/              # rss.py, polymarket.py, hn.py, github.py
├── delivery/             # telegram.py + HTML sanitizer
├── prompts/              # per-topic LLM prompt templates
├── bot/                  # PTB Application + commands
└── vendor/last30days/    # MIT-licensed upstream — do not hand-edit
deploy/                   # systemd unit + install guide
tests/                    # 214 offline tests
```

## 📄 License

MIT License. See [`LICENSE`](LICENSE).

Built on [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill) (MIT) — vendored under [`aggregator/vendor/last30days/`](aggregator/vendor/last30days/).
