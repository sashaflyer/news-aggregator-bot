# news-aggregator

Personal AI crypto research aggregator. Delivers a daily Telegram digest of crypto signals (general + SOL/SUI/AVAX watchlist) from Reddit and Polymarket, synthesized by OpenAI.

## How it works

A single long-running Python process runs on a Linux VPS under systemd. It holds:

- A Telegram bot polling loop (`/status` command in v1)
- An APScheduler cron job that fires the daily digest pipeline at the configured time

The pipeline fetches the last 24h of items from Reddit and Polymarket, deduplicates and ranks them by engagement, asks OpenAI to synthesize a readable digest, and sends it to the configured Telegram chat.

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

- `config.toml` — non-secret preferences (subreddits, watchlist symbols, schedule). Gitignored. See `config.example.toml`.
- `.env` — secrets (OpenAI key, Telegram token + chat ID, Reddit OAuth). Gitignored. See `.env.example`.

## Adding a new bot command

1. Create `aggregator/bot/commands/<name>.py` with `async def handle_<name>(update, context)`.
2. Register in `aggregator/bot/app.py`: `app.add_handler(CommandHandler("<name>", handle_<name>))`

## Adding a new source

1. Create `aggregator/sources/<name>.py` implementing the `Source` ABC.
2. Register in `aggregator/pipeline.SOURCES`.

## Tests

```powershell
pytest -v
```

All tests run offline.

## Attribution

This project builds on [`mvanhorn/last30days-skill`](https://github.com/mvanhorn/last30days-skill) (MIT). The fetching, scoring, deduplication, and storage modules are vendored under [`aggregator/vendor/last30days/`](aggregator/vendor/last30days/); upstream commit and vendoring notes are recorded in [`UPSTREAM.md`](aggregator/vendor/last30days/UPSTREAM.md). Thanks to the upstream authors.

## License

MIT. See [`LICENSE`](LICENSE).
