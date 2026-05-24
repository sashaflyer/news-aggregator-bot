from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from aggregator.bot.commands.status import handle_status
from aggregator.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics(
        general_subreddits=["X"], general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL"], watchlist_schedule="0 8 * * *",
    )
    now = datetime.now(timezone.utc)
    rid = s.start_run("crypto_general", trigger="scheduled", at=now)
    s.finish_run(rid, status="ok", items_fetched=10, items_delivered=5, at=now)
    s.record_source_success("reddit", at=now)
    return s


def make_update(chat_id: int):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock()
    return upd


def make_ctx(storage, *, started_at, scheduler=None):
    ctx = MagicMock()
    ctx.bot_data = {
        "storage": storage,
        "started_at": started_at,
        "scheduler": scheduler,
        "authorized_chat_id": 12345,
    }
    return ctx


@pytest.mark.asyncio
async def test_status_authorized_chat_replies(storage):
    upd = make_update(chat_id=12345)
    ctx = make_ctx(storage, started_at=datetime.now(timezone.utc))
    await handle_status(upd, ctx)
    upd.message.reply_text.assert_awaited_once()
    text = upd.message.reply_text.await_args.args[0]
    assert "status" in text.lower()
    assert "crypto_general" in text
    assert "reddit" in text


@pytest.mark.asyncio
async def test_status_unauthorized_chat_ignored(storage):
    upd = make_update(chat_id=99999)
    ctx = make_ctx(storage, started_at=datetime.now(timezone.utc))
    await handle_status(upd, ctx)
    upd.message.reply_text.assert_not_awaited()
