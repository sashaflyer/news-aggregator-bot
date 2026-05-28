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
import sys
from pathlib import Path

from dotenv import load_dotenv

from aggregator.bot.app import build_application, publish_commands
from aggregator.bot.digest_lock import init_locks
from aggregator.config import load_config
from aggregator.pipeline import run_digest
from aggregator.scheduler import build_scheduler
from aggregator.storage import Storage
from aggregator import watchdog as _watchdog

# Half of the unit's WatchdogSec=180 so one missed ping doesn't trip a restart.
_WATCHDOG_INTERVAL_S = 60.0

log = logging.getLogger(__name__)


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(
            f"missing required environment variable {name}; "
            f"see .env.example"
        )
    return val


def _require_env_int(name: str) -> int:
    raw = _require_env(name)
    try:
        return int(raw)
    except ValueError:
        sys.exit(f"environment variable {name} must be an integer; got {raw!r}")


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, which leaks our bot token
    # (https://api.telegram.org/bot<TOKEN>/sendMessage). Pin it to WARNING.
    # Same for openai / httpcore which also log URLs.
    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _resolve_data_dir(cfg_data_dir: str) -> Path:
    override = os.environ.get("NEWS_AGGREGATOR_DATA_DIR")
    return Path(override) if override else Path(cfg_data_dir)


def _bootstrap(config_path: str) -> tuple:
    load_dotenv()
    cfg = load_config(config_path)
    data_dir = _resolve_data_dir(cfg.storage.data_dir)
    storage = Storage(data_dir / "aggregator.db")
    storage.init_schema()
    storage.seed_topics(cfg.topics)
    return cfg, storage


async def cli_run_once(*, topic_id: str, config_path: str) -> None:
    _setup_logging()
    cfg, storage = _bootstrap(config_path)
    result = await run_digest(topic_id, cfg, storage, trigger="command")
    log.info("one-shot run %s for %s: status=%s items_fetched=%d items_delivered=%d",
             result.run_id, topic_id, result.status,
             result.items_fetched, result.items_delivered)
    if result.status == "error":
        sys.exit(1)


async def serve(*, config_path: str) -> None:
    _setup_logging()
    cfg, storage = _bootstrap(config_path)
    _require_env("OPENAI_API_KEY")
    _require_env("TELEGRAM_BOT_TOKEN")
    _require_env_int("TELEGRAM_CHAT_ID")
    scheduler = build_scheduler(cfg, storage)
    init_locks(list(cfg.topics.keys()))
    app = build_application(storage=storage, scheduler=scheduler, cfg=cfg)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    log.info("starting bot + scheduler")
    await app.initialize()
    await publish_commands(app.bot)
    await app.start()
    await app.updater.start_polling()
    scheduler.start()

    # systemd Type=notify integration. READY=1 tells systemd the unit is up;
    # WATCHDOG=1 must keep arriving (every _WATCHDOG_INTERVAL_S) or systemd
    # restarts the process after WatchdogSec. A wedged event loop — the failure
    # mode that hid for 17h on 2026-05-28 — cannot run watchdog_pinger, so it
    # converts "alive but mute" into an automatic restart. No-op when running
    # outside systemd (NOTIFY_SOCKET unset).
    _watchdog.sd_notify("READY=1")
    pinger_task = asyncio.create_task(
        _watchdog.watchdog_pinger(interval_s=_WATCHDOG_INTERVAL_S)
    )
    try:
        await stop.wait()
    finally:
        log.info("shutting down")
        _watchdog.sd_notify("STOPPING=1")
        pinger_task.cancel()
        try:
            await pinger_task
        except asyncio.CancelledError:
            pass
        # wait=True so an in-flight digest finishes; otherwise its run_history
        # row stays in 'started' forever and the items it was about to record
        # as delivered can be re-delivered on next launch.
        scheduler.shutdown(wait=True)
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> None:
    # NOTE: --topic choices aren't restricted in argparse because the topic set
    # is loaded from config at runtime. We validate post-load and exit 2 with
    # a clear message if the topic is unknown (mimicking argparse's behavior).
    parser = argparse.ArgumentParser(prog="news-aggregator")
    parser.add_argument("--config", default="config.toml")
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="run pipeline once for a topic and exit")
    run_p.add_argument("--topic", required=True,
                       help="topic id from [topics.<id>] in config")

    args = parser.parse_args()
    if args.cmd == "run":
        cfg = load_config(args.config)
        if args.topic not in cfg.topics:
            known = sorted(cfg.topics.keys())
            parser.exit(2, f"error: unknown topic {args.topic!r}; "
                           f"known topics: {known}\n")
        asyncio.run(cli_run_once(topic_id=args.topic, config_path=args.config))
    else:
        asyncio.run(serve(config_path=args.config))


if __name__ == "__main__":
    main()
