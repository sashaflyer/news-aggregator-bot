"""Watchdog tests. sd_notify must be no-op without $NOTIFY_SOCKET, write
the documented protocol bytes when set, and watchdog_pinger must ping at
the configured interval until cancelled."""
from __future__ import annotations

import asyncio
import contextlib
import socket
import sys

import pytest


def test_sd_notify_no_op_without_socket(monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    from aggregator.watchdog import sd_notify

    # No socket configured → must not raise, must return False.
    assert sd_notify("READY=1") is False


def test_sd_notify_no_op_with_empty_socket(monkeypatch):
    monkeypatch.setenv("NOTIFY_SOCKET", "")
    from aggregator.watchdog import sd_notify

    assert sd_notify("WATCHDOG=1") is False


def test_sd_notify_writes_payload_to_unix_socket(monkeypatch, tmp_path):
    if sys.platform == "win32":
        pytest.skip("AF_UNIX SOCK_DGRAM is not supported on Windows")

    sock_path = tmp_path / "notify.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    server.settimeout(2.0)
    try:
        monkeypatch.setenv("NOTIFY_SOCKET", str(sock_path))

        from aggregator.watchdog import sd_notify
        assert sd_notify("READY=1\nSTATUS=up") is True

        data, _ = server.recvfrom(4096)
        assert data == b"READY=1\nSTATUS=up"
    finally:
        server.close()


def test_sd_notify_supports_abstract_socket(monkeypatch):
    """systemd uses abstract sockets (path starts with '@', mapped to NUL byte).
    Abstract sockets are Linux-only."""
    if sys.platform != "linux":
        pytest.skip("abstract unix sockets are Linux-only")

    name = "\0test-news-aggregator-notify"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(name)
    server.settimeout(2.0)
    try:
        # systemd represents abstract sockets in $NOTIFY_SOCKET with leading '@'.
        monkeypatch.setenv("NOTIFY_SOCKET", "@" + name[1:])
        from aggregator.watchdog import sd_notify
        assert sd_notify("WATCHDOG=1") is True
        data, _ = server.recvfrom(4096)
        assert data == b"WATCHDOG=1"
    finally:
        server.close()


@pytest.mark.asyncio
async def test_watchdog_pinger_calls_sd_notify_periodically(monkeypatch):
    """The pinger must call sd_notify(WATCHDOG=1) at the configured interval
    and stop cleanly on cancellation."""
    from aggregator import watchdog as wd

    calls: list[str] = []

    def fake_notify(payload: str) -> bool:
        calls.append(payload)
        return True

    monkeypatch.setattr(wd, "sd_notify", fake_notify)

    task = asyncio.create_task(wd.watchdog_pinger(interval_s=0.05))
    try:
        # Wait long enough to see ~3 pings.
        await asyncio.sleep(0.18)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(calls) >= 2, f"expected at least 2 pings, got {calls}"
    assert all(c == "WATCHDOG=1" for c in calls)


@pytest.mark.asyncio
async def test_watchdog_pinger_swallows_notify_failures(monkeypatch):
    """If sd_notify raises (e.g. socket disappeared), the pinger must keep
    going — one failed ping shouldn't take down the loop."""
    from aggregator import watchdog as wd

    call_count = 0

    def flaky_notify(payload: str) -> bool:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("socket gone")
        return True

    monkeypatch.setattr(wd, "sd_notify", flaky_notify)

    task = asyncio.create_task(wd.watchdog_pinger(interval_s=0.02))
    try:
        await asyncio.sleep(0.1)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert call_count >= 3, "pinger should have continued past the OSError"
