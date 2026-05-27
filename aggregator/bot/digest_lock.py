"""Per-topic asyncio.Lock registry.

Shared between the scheduler (`aggregator.scheduler._job`) and `/digest`
so a manual digest cannot race a scheduled one for the same topic.
Locks are created lazily and live for the lifetime of the process.
"""
from __future__ import annotations

import asyncio

_topic_locks: dict[str, asyncio.Lock] = {}


def lock_for(topic_id: str) -> asyncio.Lock:
    lock = _topic_locks.get(topic_id)
    if lock is None:
        lock = asyncio.Lock()
        _topic_locks[topic_id] = lock
    return lock


def init_locks(topic_ids: list[str]) -> None:
    """Call once at startup before any handler runs.

    Pre-allocates an asyncio.Lock per topic so lock_for() never needs to
    create one under handler/scheduler contention. Removes the implicit
    'no await in lock_for' invariant since locks already exist.
    """
    for tid in topic_ids:
        _topic_locks.setdefault(tid, asyncio.Lock())
