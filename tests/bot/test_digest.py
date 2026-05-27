import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aggregator.bot.commands.digest import handle_digest
from aggregator.bot.digest_lock import _topic_locks, lock_for
from aggregator.config import TopicConfig
from aggregator.pipeline import RunResult


class _CfgStub:
    """Minimal stand-in for aggregator.config.Config; only .topics is read."""
    def __init__(self, topic_ids: list[str]):
        self.topics = {
            tid: TopicConfig(
                kind="general", sources=["reddit"], subreddits=["X"],
                polymarket_tags=[], prompt_template="general_crypto.md",
                top_n=10, schedule="0 8 * * *",
            )
            for tid in topic_ids
        }


@pytest.fixture(autouse=True)
def clean_locks():
    _topic_locks.clear()
    yield
    _topic_locks.clear()


def make_update(chat_id: int):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock()
    return upd


def make_ctx(*, args: list[str], cfg, storage=None):
    ctx = MagicMock()
    ctx.args = args
    ctx.bot_data = {
        "authorized_chat_id": 12345,
        "cfg": cfg,
        "storage": storage or MagicMock(),
    }
    return ctx


@pytest.mark.asyncio
async def test_digest_unauthorized_chat_ignored():
    cfg = _CfgStub(["crypto_general"])
    upd = make_update(chat_id=99999)
    ctx = make_ctx(args=["crypto_general"], cfg=cfg)
    with patch("aggregator.bot.commands.digest.run_digest") as run_d:
        await handle_digest(upd, ctx)
    upd.message.reply_text.assert_not_awaited()
    run_d.assert_not_called()


@pytest.mark.asyncio
async def test_digest_missing_arg_replies_with_usage():
    cfg = _CfgStub(["crypto_general", "ai_general"])
    upd = make_update(chat_id=12345)
    ctx = make_ctx(args=[], cfg=cfg)
    with patch("aggregator.bot.commands.digest.run_digest") as run_d:
        await handle_digest(upd, ctx)
    upd.message.reply_text.assert_awaited_once()
    text = upd.message.reply_text.await_args.args[0]
    assert "Usage" in text
    assert "crypto_general" in text
    assert "ai_general" in text
    run_d.assert_not_called()


@pytest.mark.asyncio
async def test_digest_unknown_topic_replies_with_usage():
    cfg = _CfgStub(["crypto_general"])
    upd = make_update(chat_id=12345)
    ctx = make_ctx(args=["does_not_exist"], cfg=cfg)
    with patch("aggregator.bot.commands.digest.run_digest") as run_d:
        await handle_digest(upd, ctx)
    upd.message.reply_text.assert_awaited_once()
    text = upd.message.reply_text.await_args.args[0]
    assert "Usage" in text
    assert "crypto_general" in text
    run_d.assert_not_called()


@pytest.mark.asyncio
async def test_digest_valid_topic_runs_pipeline_with_manual_trigger():
    cfg = _CfgStub(["crypto_general"])
    upd = make_update(chat_id=12345)
    ctx = make_ctx(args=["crypto_general"], cfg=cfg)
    fake_run = AsyncMock(return_value=RunResult(
        run_id=42, status="ok", items_fetched=10, items_delivered=5))
    with patch("aggregator.bot.commands.digest.run_digest", fake_run):
        await handle_digest(upd, ctx)
    fake_run.assert_awaited_once_with(
        "crypto_general", cfg, ctx.bot_data["storage"], trigger="manual"
    )
    # An ack message went out before the pipeline ran.
    # (The digest itself is sent inside run_digest, which we mocked.)
    assert upd.message.reply_text.await_count >= 1
    ack_text = upd.message.reply_text.await_args_list[0].args[0]
    assert "crypto_general" in ack_text
    assert "Running" in ack_text or "running" in ack_text


@pytest.mark.asyncio
async def test_digest_already_running_replies_and_does_not_call_pipeline():
    cfg = _CfgStub(["crypto_general"])
    upd = make_update(chat_id=12345)
    ctx = make_ctx(args=["crypto_general"], cfg=cfg)
    held = lock_for("crypto_general")
    await held.acquire()
    try:
        with patch("aggregator.bot.commands.digest.run_digest") as run_d:
            await handle_digest(upd, ctx)
        upd.message.reply_text.assert_awaited_once()
        text = upd.message.reply_text.await_args.args[0]
        assert "already running" in text.lower()
        run_d.assert_not_called()
    finally:
        held.release()


@pytest.mark.asyncio
async def test_digest_concurrent_invocations_get_busy_reply():
    """A second /digest while one is in flight must not queue — it gets
    'already running' immediately. Exercises the non-blocking acquire path
    where the lock is held by a concurrent task."""
    cfg = _CfgStub(["crypto_general"])
    lock = lock_for("crypto_general")

    async def hold():
        async with lock:
            await asyncio.sleep(0.5)

    holder = asyncio.create_task(hold())
    # Yield once so the holder task actually grabs the lock.
    await asyncio.sleep(0)

    upd = make_update(chat_id=12345)
    ctx = make_ctx(args=["crypto_general"], cfg=cfg)
    try:
        with patch("aggregator.bot.commands.digest.run_digest") as run_d:
            await handle_digest(upd, ctx)
        upd.message.reply_text.assert_awaited_once()
        text = upd.message.reply_text.await_args.args[0]
        assert "already running" in text.lower()
        run_d.assert_not_called()
    finally:
        holder.cancel()
        try:
            await holder
        except (asyncio.CancelledError, BaseException):
            pass


@pytest.mark.asyncio
async def test_digest_pipeline_exception_is_reported_and_lock_released():
    cfg = _CfgStub(["crypto_general"])
    upd = make_update(chat_id=12345)
    ctx = make_ctx(args=["crypto_general"], cfg=cfg)
    fake_run = AsyncMock(side_effect=RuntimeError("boom: <details>"))
    with patch("aggregator.bot.commands.digest.run_digest", fake_run):
        await handle_digest(upd, ctx)
    # At least: ack + error message.
    texts = [c.args[0] for c in upd.message.reply_text.await_args_list]
    assert any("RuntimeError" in t for t in texts)
    # `<` from the exception message must be HTML-escaped in any HTML-mode reply,
    # otherwise Telegram would reject the parse and force the plain-text fallback.
    html_replies = [c for c in upd.message.reply_text.await_args_list
                    if c.kwargs.get("parse_mode") == "HTML"]
    assert html_replies, "error reply must use HTML parse mode"
    for c in html_replies:
        assert "<details>" not in c.args[0]
        assert "&lt;details&gt;" in c.args[0]
    # Lock must be free after exception.
    assert not lock_for("crypto_general").locked()
