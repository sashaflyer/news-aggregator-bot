# AGENTS.md

Guidance for AI coding agents working on this repository.

## Project

Self-hosted Telegram bot that delivers twice-daily digests from RSS, Polymarket, and Hacker News, deduplicated and summarized by an LLM. Python 3.12+, single process, SQLite-backed, `systemd`-managed. One operator, one chat — keep the design simple.

## Quick reference

```bash
# Setup
python3 -m venv .venv
.venv\Scripts\Activate.ps1          # Windows
pip install -e ".[dev]"
python scripts/vendor_last30days.py  # fetches MIT-licensed vendored deps

# Run
python -m aggregator run --topic <id>   # one-shot test
python -m aggregator                    # long-running

# Verify (run before claiming done)
pytest -q
```

All 243 tests are offline. Network calls (RSS, Polymarket, HN, OpenAI, Telegram) are mocked via `respx` / `unittest.mock` — never require real keys to run the suite.

## Layout

```
aggregator/
  __main__.py        # entry: bot polling + scheduler in one event loop
  pipeline.py        # run_digest orchestration
  ranking.py         # engagement scoring, dedup, per-author cap
  text.py            # text chunking for Telegram message limits
  config.py          # pydantic-validated config loader
  storage.py         # SQLite layer
  scheduler.py       # APScheduler cron (timezone-explicit)
  synth.py           # OpenAI synthesis
  relevance.py       # watchlist off-topic filter
  sources/           # rss.py, polymarket.py, hn.py — one per source
  delivery/          # telegram.py + _html_filter.py
  bot/
    app.py           # PTB Application factory + COMMANDS registry
    _authz.py        # shared chat-id authorization
    digest_lock.py   # per-topic asyncio.Lock
    commands/        # one file per command
  prompts/           # per-topic LLM prompt templates
  vendor/last30days/ # MIT-licensed upstream — do not hand-edit
deploy/              # systemd unit + install guide
tests/               # mirrors aggregator/ structure
```

## Conventions

- **Match existing style.** Read a neighboring file before editing. No comments unless asked.
- **No new abstractions for single-use code.** If it fits in one function, leave it.
- **Configuration is data, not code.** New digest streams are a `[topics.<id>]` block in `config.toml` + one prompt template in `aggregator/prompts/`. No Python edits required.
- **Bot commands are registry entries.** Add `aggregator/bot/commands/<name>.py`, then append one tuple to `COMMANDS` in `aggregator/bot/app.py`. `/help` and the Telegram `/` menu both read from that list.
- **Sources implement `Source` ABC** in `aggregator/sources/base.py` (single `async def fetch(self, queries) -> list[Item]`). Register in `aggregator/pipeline.SOURCES`.
- **Prompt templates** use `aggregator/prompts/<name>.md`. Shared rules live in `_rules_telegram_html.md` and are included by templates — don't duplicate them.
- **Telegram delivery** is HTML with an automatic plain-text fallback in `aggregator/delivery/_html_filter.py`. If you change the LLM output format, update the filter too.

## Testing patterns

- `pytest-asyncio` is set to `auto` mode — no `@pytest.mark.asyncio` needed.
- HTTP mocks go through `respx`; see `tests/sources/test_*.py` for examples.
- Fixture data for source responses lives in `tests/sources/fixtures/`.
- Pipeline tests mock at the source level, not at `httpx` — preferred unless testing transport itself.
- When adding a new bot command, also add a test in `tests/bot/test_<command>.py` covering auth (allowed/denied chat id) and the happy path.

## Common tasks

| Task | Where |
|------|-------|
| Add a topic | `config.toml` + `aggregator/prompts/<name>.md` |
| Add a bot command | `aggregator/bot/commands/<name>.py` + entry in `COMMANDS` in `app.py` |
| Add a source | `aggregator/sources/<name>.py` (extend `Source` ABC) + register in `pipeline.SOURCES` |
| Change scoring/dedup | `aggregator/ranking.py` (vendored logic in `vendor/last30days/`) |
| Change deploy | `deploy/news-aggregator.service`, then update `deploy/README.md` |

## Things to avoid

- **Don't edit `aggregator/vendor/last30days/`** — it is upstream code re-synced via `scripts/vendor_last30days.py`. If you need a change, patch it locally and note it; long-term, upstream it.
- **Don't hardcode secrets or chat IDs.** Both go in `.env` / `config.toml`, both gitignored.
- **Don't bypass `Source` ABC** when adding fetchers — the pipeline depends on the contract.
- **Don't introduce sync HTTP or blocking I/O** in the event loop. Use `httpx.AsyncClient` or the existing async sources.
- **Don't pin the LLM to a specific model name** in code — it comes from `config.toml` (`[model].name`).
- **Don't add per-topic code paths to the scheduler** — schedule is config-driven via cron expressions in each topic.

## Verifying a change

1. `pytest -q` — must stay green.
2. If you touched a source, confirm the matching test in `tests/sources/` still mocks realistically (status codes, pagination edges).
3. If you touched the prompt format, run a real digest against a topic and eyeball the Telegram output — the tests can't catch "the LLM now produces ugly markup."
4. If you changed the systemd unit, re-read `deploy/README.md` for the install sequence.
