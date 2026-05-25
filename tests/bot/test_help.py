from unittest.mock import AsyncMock, MagicMock

import pytest

from aggregator.bot.commands.help import handle_help


def make_update(chat_id: int):
    upd = MagicMock()
    upd.effective_chat.id = chat_id
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock()
    return upd


def make_ctx():
    ctx = MagicMock()
    ctx.bot_data = {"authorized_chat_id": 12345}
    return ctx


@pytest.mark.asyncio
async def test_help_unauthorized_chat_ignored():
    upd = make_update(chat_id=99999)
    ctx = make_ctx()
    await handle_help(upd, ctx)
    upd.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_help_lists_every_registered_command():
    upd = make_update(chat_id=12345)
    ctx = make_ctx()
    await handle_help(upd, ctx)
    upd.message.reply_text.assert_awaited_once()
    text = upd.message.reply_text.await_args.args[0]
    # Every command in the registry must appear by name.
    from aggregator.bot.app import COMMANDS
    for name, description, _handler in COMMANDS:
        assert f"/{name}" in text
        assert description in text
    # Reply must use HTML parse mode (consistent with /status).
    assert upd.message.reply_text.await_args.kwargs.get("parse_mode") == "HTML"
