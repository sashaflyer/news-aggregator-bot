from unittest.mock import MagicMock

from aggregator.bot._authz import is_authorized


def _ctx(authorized_chat_id: int):
    ctx = MagicMock()
    ctx.bot_data = {"authorized_chat_id": authorized_chat_id}
    return ctx


def _update(chat_id: int | None):
    upd = MagicMock()
    if chat_id is None:
        upd.effective_chat = None
    else:
        upd.effective_chat.id = chat_id
    return upd


def test_authorized_chat_returns_true():
    assert is_authorized(_update(12345), _ctx(12345)) is True


def test_other_chat_returns_false():
    assert is_authorized(_update(99999), _ctx(12345)) is False


def test_no_chat_returns_false():
    assert is_authorized(_update(None), _ctx(12345)) is False
