import os
from unittest.mock import MagicMock, patch

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


def test_command_handlers_have_dispatcher_level_chat_filter(tmp_path):
    """Every registered CommandHandler must carry a Chat() filter so an
    Update from any other chat is dropped before the handler is invoked.
    This makes authz unbypassable by omission — adding a new command can't
    accidentally ship a world-accessible handler."""
    from telegram.ext import filters

    from aggregator.bot.app import build_application, COMMANDS
    from aggregator.config import load_config
    from aggregator.storage import Storage

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test:token",
        "TELEGRAM_CHAT_ID": "42",
    }):
        cfg = load_config("config.example.toml")
        s = Storage(str(tmp_path / "t.db"))
        s.init_schema()
        s.seed_topics(cfg.topics)
        app = build_application(storage=s, scheduler=None, cfg=cfg)

    handlers = [h for group in app.handlers.values() for h in group]
    # Sanity: one handler per declared command.
    assert len(handlers) == len(COMMANDS)
    for h in handlers:
        # Every handler must have a Chat() filter restricting to chat_id=42.
        assert h.filters is not None, f"{h.callback.__name__} has no filter"
        assert isinstance(h.filters, filters.Chat)
        assert h.filters.chat_ids == frozenset({42})


def test_chat_filter_rejects_unauthorized_chat_via_filter_check(tmp_path):
    """The Chat filter on every handler returns False for non-matching chats.
    This is what causes PTB's dispatcher to skip the callback entirely."""
    from telegram import Chat, Message, Update, User
    from datetime import datetime, timezone

    from aggregator.bot.app import build_application
    from aggregator.config import load_config
    from aggregator.storage import Storage

    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test:token",
        "TELEGRAM_CHAT_ID": "42",
    }):
        cfg = load_config("config.example.toml")
        s = Storage(str(tmp_path / "t.db"))
        s.init_schema()
        s.seed_topics(cfg.topics)
        app = build_application(storage=s, scheduler=None, cfg=cfg)

    def _update_from(chat_id: int) -> Update:
        chat = Chat(id=chat_id, type="private")
        user = User(id=chat_id, first_name="x", is_bot=False)
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=chat,
            from_user=user,
            text="/status",
        )
        return Update(update_id=1, message=message)

    handlers = [h for group in app.handlers.values() for h in group]
    bad = _update_from(chat_id=99)
    good = _update_from(chat_id=42)
    for h in handlers:
        # filter_result(bad) is falsy → dispatcher drops the update.
        assert not h.filters.check_update(bad), (
            f"{h.callback.__name__}: filter accepted unauthorized chat 99"
        )
        assert h.filters.check_update(good), (
            f"{h.callback.__name__}: filter rejected authorized chat 42"
        )
