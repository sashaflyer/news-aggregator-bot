"""Integration tests for systemd notify + watchdog wiring in serve().

The point: when running under systemd with Type=notify+WatchdogSec, the
bot must send READY=1 after startup, ping WATCHDOG=1 periodically, and
send STOPPING=1 on shutdown. A wedged event loop cannot run the ping
task, so systemd will restart the process — which is the whole reason
this exists (2026-05-28 incident, see deploy/README.md)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_serve_sends_ready_starts_pinger_and_stopping(monkeypatch, tmp_path):
    """End-to-end check on the systemd lifecycle wiring, with PTB+APScheduler
    fully mocked so no real network/event loop interaction happens.

    Asserts:
      - sd_notify("READY=1") sent after app.start() + scheduler.start()
      - sd_notify("STOPPING=1") sent during shutdown
      - watchdog_pinger task is created and awaited as part of shutdown
    """
    from pathlib import Path

    # Minimal env so _bootstrap and _require_env don't bail.
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("NEWS_AGGREGATOR_DATA_DIR", str(tmp_path / "data"))

    from aggregator import __main__ as m

    # Stub _bootstrap to avoid touching disk / config validation.
    cfg = MagicMock()
    cfg.topics = {}
    storage = MagicMock()
    monkeypatch.setattr(m, "_bootstrap", lambda config_path: (cfg, storage))

    # Stub build_scheduler / build_application — we don't want real PTB
    # or APScheduler here; we only care about the systemd notify wiring.
    fake_scheduler = MagicMock()
    monkeypatch.setattr(m, "build_scheduler", lambda c, s: fake_scheduler)

    fake_app = MagicMock()
    fake_app.initialize = AsyncMock()
    fake_app.start = AsyncMock()
    fake_app.stop = AsyncMock()
    fake_app.shutdown = AsyncMock()
    fake_app.updater = MagicMock()
    fake_app.updater.start_polling = AsyncMock()
    fake_app.updater.stop = AsyncMock()
    monkeypatch.setattr(m, "build_application",
                        lambda *, storage, scheduler, cfg: fake_app)
    monkeypatch.setattr(m, "publish_commands", AsyncMock())
    monkeypatch.setattr(m, "init_locks", lambda *a, **k: None)

    notify_calls: list[str] = []
    monkeypatch.setattr(
        "aggregator.watchdog.sd_notify",
        lambda payload: notify_calls.append(payload) or True,
    )

    # Record whether watchdog_pinger ran by counting how many times it
    # was awaited. Replace it with a coroutine that increments a counter
    # and yields control until cancelled — this also confirms serve()
    # cancels the task during shutdown rather than leaving it pending.
    pinger_started = asyncio.Event()
    pinger_cancelled = asyncio.Event()

    async def fake_pinger(*, interval_s: float):
        pinger_started.set()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pinger_cancelled.set()
            raise

    monkeypatch.setattr("aggregator.watchdog.watchdog_pinger", fake_pinger)

    # Drive serve(): start it, wait until READY has fired and the pinger
    # task has been spawned, then trigger shutdown by setting the stop
    # Event. We patch asyncio.Event so we have a handle on the stop signal.
    real_event = asyncio.Event()
    monkeypatch.setattr("asyncio.Event", lambda: real_event)

    serve_task = asyncio.create_task(m.serve(config_path="ignored"))

    try:
        # Wait until the pinger has started (proxy for "we're past startup").
        await asyncio.wait_for(pinger_started.wait(), timeout=2.0)

        # By now READY=1 should have been sent.
        assert "READY=1" in notify_calls, notify_calls

        # Trigger graceful shutdown.
        real_event.set()
        await asyncio.wait_for(serve_task, timeout=2.0)
    finally:
        if not serve_task.done():
            serve_task.cancel()

    assert pinger_cancelled.is_set(), "pinger task must be cancelled on shutdown"
    assert "STOPPING=1" in notify_calls, notify_calls
    # Sanity: READY came before STOPPING.
    assert notify_calls.index("READY=1") < notify_calls.index("STOPPING=1")
    # Lifecycle invariants the real code must uphold.
    fake_app.updater.start_polling.assert_awaited_once()
    fake_scheduler.start.assert_called_once()
    fake_scheduler.shutdown.assert_called_once_with(wait=True)
