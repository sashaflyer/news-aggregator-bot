import asyncio

import pytest

from aggregator.bot.digest_lock import lock_for, _topic_locks


@pytest.fixture(autouse=True)
def clean_locks():
    _topic_locks.clear()
    yield
    _topic_locks.clear()


def test_lock_for_returns_same_instance_for_same_topic():
    a = lock_for("crypto_general")
    b = lock_for("crypto_general")
    assert a is b


def test_lock_for_returns_different_instances_for_different_topics():
    a = lock_for("crypto_general")
    b = lock_for("ai_general")
    assert a is not b


@pytest.mark.asyncio
async def test_lock_is_an_asyncio_lock():
    lock = lock_for("crypto_general")
    assert isinstance(lock, asyncio.Lock)
    assert not lock.locked()
    async with lock:
        assert lock.locked()
    assert not lock.locked()


def test_init_locks_for_topics_preallocates_one_per_topic():
    """init_locks() seeds the registry so lock_for() at handler time never
    has to create a Lock under contention."""
    from aggregator.bot.digest_lock import init_locks

    init_locks(["t1", "t2"])
    assert "t1" in _topic_locks
    assert "t2" in _topic_locks
    assert lock_for("t1") is _topic_locks["t1"]


def test_init_locks_is_idempotent_and_preserves_existing_locks():
    """Calling init_locks twice (or after lock_for) must not replace existing
    Locks — otherwise an in-flight digest could lose its lock identity."""
    from aggregator.bot.digest_lock import init_locks

    existing = lock_for("t1")
    init_locks(["t1", "t2"])
    assert lock_for("t1") is existing
    assert "t2" in _topic_locks
