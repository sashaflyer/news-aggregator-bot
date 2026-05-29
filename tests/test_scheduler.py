import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aggregator.config import load_config
from aggregator.storage import Storage


def test_build_scheduler_registers_one_job_per_topic(tmp_path):
    from aggregator import scheduler as sched_mod

    cfg = load_config("config.example.toml")
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics(cfg.topics)

    with patch.object(sched_mod, "AsyncIOScheduler") as FakeSched:
        instance = MagicMock()
        FakeSched.return_value = instance
        result = sched_mod.build_scheduler(cfg, s)

    assert result is instance
    # One job per topic in the example config; assert the set matches what
    # config.example.toml declares (so adding/removing example topics here
    # doesn't break the test).
    expected = sorted(cfg.topics.keys())
    assert instance.add_job.call_count == len(expected)
    job_topic_args = sorted(
        call.kwargs["args"][0] for call in instance.add_job.call_args_list
    )
    assert job_topic_args == expected


def test_build_scheduler_passes_configured_timezone_to_cron_trigger(tmp_path):
    """Regression: cron triggers must be built with the configured timezone.

    ``CronTrigger.from_crontab(expr)`` ignores the scheduler's own timezone and
    defaults to the *server-local* zone unless ``timezone=`` is passed. On the
    UTC prod host that silently shifted every digest by the MSK offset (+3h):
    `0 8,20 * * *` MSK was firing at 08:00/20:00 UTC. We assert the kwarg is
    passed (machine-independent — checking the resulting trigger.timezone would
    pass spuriously on a machine whose local zone already equals the config).
    """
    from aggregator import scheduler as sched_mod

    cfg = load_config("config.example.toml")  # [schedule] timezone = Europe/Moscow
    s = Storage(str(tmp_path / "tz.db"))
    s.init_schema()
    s.seed_topics(cfg.topics)

    with patch.object(sched_mod, "AsyncIOScheduler") as FakeSched, \
         patch.object(sched_mod.CronTrigger, "from_crontab",
                      wraps=sched_mod.CronTrigger.from_crontab) as spy:
        FakeSched.return_value = MagicMock()
        sched_mod.build_scheduler(cfg, s)

    assert spy.call_count >= 1
    for call in spy.call_args_list:
        assert call.kwargs.get("timezone") == cfg.schedule.timezone, (
            "CronTrigger.from_crontab must be called with "
            "timezone=cfg.schedule.timezone so digests fire in the configured zone"
        )


@pytest.mark.asyncio
async def test_scheduler_job_acquires_topic_lock():
    """If the lock for a topic is already held, _job must wait for release
    before invoking run_digest — preventing /digest and a scheduled firing
    from running concurrently for the same topic."""
    from aggregator import scheduler as sched_mod
    from aggregator.bot.digest_lock import lock_for, _topic_locks

    _topic_locks.clear()
    held_lock = lock_for("crypto_general")
    await held_lock.acquire()

    # A real RunResult so the log line inside _job doesn't blow up.
    from aggregator.pipeline import RunResult
    fake_run_digest = AsyncMock(return_value=RunResult(
        run_id=1, status="ok", items_fetched=0, items_delivered=0))

    cfg = object()
    storage = object()

    with patch.object(sched_mod, "run_digest", fake_run_digest):
        task = asyncio.create_task(sched_mod._job("crypto_general", cfg, storage))
        # Give the task a chance to run; it should be parked waiting on the lock.
        await asyncio.sleep(0.05)
        assert not task.done(), "job should be blocked on held lock"
        assert fake_run_digest.await_count == 0

        held_lock.release()
        await asyncio.wait_for(task, timeout=1.0)

    fake_run_digest.assert_awaited_once_with("crypto_general", cfg, storage,
                                              trigger="scheduled")
    _topic_locks.clear()
