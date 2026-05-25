from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from aggregator.bot.commands.status import handle_status
from aggregator.config import TopicConfig, WatchEntry
from aggregator.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics({
        "crypto_general": TopicConfig(
            kind="general", sources=["reddit"], subreddits=["X"],
            polymarket_tags=["crypto"], prompt_template="general_crypto.md",
            top_n=10, schedule="0 8 * * *",
        ),
        "crypto_watchlist": TopicConfig(
            kind="watchlist", sources=["reddit"],
            watch=[WatchEntry(ticker="SOL")],
            prompt_template="watchlist.md", per_symbol_top_n=5,
            schedule="0 8 * * *",
        ),
    })
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
    call = upd.message.reply_text.await_args
    text = call.args[0]
    assert "status" in text.lower()
    assert "crypto_general" in text
    assert "reddit" in text
    # Reply must use HTML parse_mode (topic names contain underscores that
    # would break legacy Markdown).
    assert call.kwargs.get("parse_mode") == "HTML"
    # Must not emit Markdown-style markup that would render as literal asterisks.
    assert "*news-aggregator" not in text
    assert "<b>news-aggregator status</b>" in text


@pytest.mark.asyncio
async def test_status_unauthorized_chat_ignored(storage):
    upd = make_update(chat_id=99999)
    ctx = make_ctx(storage, started_at=datetime.now(timezone.utc))
    await handle_status(upd, ctx)
    upd.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_long_reply_is_chunked(tmp_path):
    """Many topics + many source_health rows can push the reply past 4096
    chars; must split into chunks rather than fail with HTTP 400.
    """
    from aggregator.config import TopicConfig
    s = Storage(str(tmp_path / "t.db"))
    s.init_schema()
    topics = {
        f"topic_{i:03d}": TopicConfig(
            kind="general", sources=["reddit"], subreddits=["X"],
            polymarket_tags=["crypto"], prompt_template="general_crypto.md",
            top_n=10, schedule="0 8 * * *",
        )
        for i in range(150)
    }
    s.seed_topics(topics)
    now = datetime.now(timezone.utc)
    for i in range(30):
        s.record_source_success(f"src_{i:03d}", at=now)

    upd = make_update(chat_id=12345)
    ctx = make_ctx(s, started_at=now)
    await handle_status(upd, ctx)
    # Must have been called more than once because content exceeds 4000 chars.
    assert upd.message.reply_text.await_count >= 2
    for call in upd.message.reply_text.await_args_list:
        assert len(call.args[0]) <= 4096
        assert call.kwargs.get("parse_mode") == "HTML"
