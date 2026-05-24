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
