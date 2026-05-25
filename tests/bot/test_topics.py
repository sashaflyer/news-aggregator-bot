from unittest.mock import AsyncMock, MagicMock

import pytest

from aggregator.bot.commands.topics import handle_topics
from aggregator.config import TopicConfig, WatchEntry
from aggregator.storage import Storage


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_schema()
    s.seed_topics({
        "crypto_general": TopicConfig(
            kind="general", sources=["reddit", "hackernews"], subreddits=["X"],
            polymarket_tags=[], prompt_template="general_crypto.md",
            top_n=10, schedule="0 8 * * *",
        ),
        "crypto_watchlist": TopicConfig(
            kind="watchlist", sources=["reddit"],
            watch=[WatchEntry(ticker="SOL")],
            prompt_template="watchlist.md", per_symbol_top_n=5,
            schedule="30 8 * * *",
        ),
    })
    return s


def make_update(chat_id: int):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock()
    return upd


def make_ctx(storage):
    ctx = MagicMock()
    ctx.bot_data = {"authorized_chat_id": 12345, "storage": storage}
    return ctx


@pytest.mark.asyncio
async def test_topics_unauthorized_chat_ignored(storage):
    upd = make_update(chat_id=99999)
    ctx = make_ctx(storage)
    await handle_topics(upd, ctx)
    upd.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_topics_lists_all_configured_topics(storage):
    upd = make_update(chat_id=12345)
    ctx = make_ctx(storage)
    await handle_topics(upd, ctx)
    upd.message.reply_text.assert_awaited_once()
    text = upd.message.reply_text.await_args.args[0]
    # Each topic id, kind, schedule, and at least one source must appear.
    assert "crypto_general" in text
    assert "general" in text
    assert "0 8 * * *" in text
    assert "reddit" in text
    assert "hackernews" in text
    assert "crypto_watchlist" in text
    assert "watchlist" in text
    assert "30 8 * * *" in text
    assert upd.message.reply_text.await_args.kwargs.get("parse_mode") == "HTML"
