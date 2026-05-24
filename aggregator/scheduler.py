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
    except Exception:
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
            misfire_grace_time=3600,
            replace_existing=True,
        )
        log.info("scheduled %s with cron %s in tz %s",
                 topic["name"], topic["schedule"], cfg.schedule.timezone)
    return scheduler
