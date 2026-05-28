"""systemd watchdog integration.

Why: a transient Telegram polling error left the bot process alive but mute
for 17+ hours (2026-05-28 incident). systemd `Restart=on-failure` requires
an actual crash, so a wedged-but-alive process never recovers.

What this provides: `sd_notify()` writes the standard systemd notification
protocol to `$NOTIFY_SOCKET`, and `watchdog_pinger()` is an asyncio task
that pings every interval. With `Type=notify` + `WatchdogSec=N` in the
unit file, systemd will restart the process if the loop stops pinging —
because a wedged event loop cannot run this task.

No new dependencies: the protocol is a single datagram on a Unix socket.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

log = logging.getLogger(__name__)


def sd_notify(state: str) -> bool:
    """Send a state line to systemd. Returns True if delivered, False if no
    socket is configured (e.g. running locally / under pytest). All failures
    are swallowed and logged at WARNING — sd_notify is best-effort by design.

    Protocol: https://www.freedesktop.org/software/systemd/man/sd_notify.html
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return False

    # systemd encodes abstract namespace sockets as a leading '@', which we
    # must translate back to a leading NUL byte for bind/sendto. Path-based
    # sockets pass through unchanged.
    if addr.startswith("@"):
        addr = "\0" + addr[1:]

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.sendto(state.encode("utf-8"), addr)
        return True
    except OSError as e:
        log.warning("sd_notify failed (%s): %s", state.splitlines()[0], e)
        return False


async def watchdog_pinger(*, interval_s: float = 30.0) -> None:
    """Ping systemd's watchdog every `interval_s` seconds until cancelled.

    `interval_s` should be roughly half of the unit's `WatchdogSec=` so a
    single missed ping doesn't trigger a restart. A wedged event loop won't
    run this coroutine and systemd will kill the process after WatchdogSec.
    """
    while True:
        try:
            sd_notify("WATCHDOG=1")
        except Exception as e:  # noqa: BLE001 - never let the pinger die
            log.warning("watchdog ping crashed: %s", e)
        await asyncio.sleep(interval_s)
