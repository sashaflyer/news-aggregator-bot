import os
from unittest.mock import patch

import pytest

from aggregator.config import load_config
from aggregator.storage import Storage


@pytest.fixture(autouse=True)
def telegram_env():
    with patch.dict(os.environ, {
        "TELEGRAM_BOT_TOKEN": "test:token",
        "TELEGRAM_CHAT_ID": "12345",
    }):
        yield


def test_build_application_stashes_cfg_in_bot_data(tmp_path):
    from aggregator.bot.app import build_application

    cfg = load_config("config.example.toml")
    s = Storage(str(tmp_path / "t.db"))
    s.init_schema()
    s.seed_topics(cfg.topics)

    app = build_application(storage=s, scheduler=None, cfg=cfg)
    assert app.bot_data["cfg"] is cfg
    assert app.bot_data["storage"] is s
    assert app.bot_data["authorized_chat_id"] == 12345


@pytest.mark.asyncio
async def test_publish_commands_calls_set_my_commands(tmp_path):
    from aggregator.bot.app import COMMANDS, publish_commands
    from unittest.mock import AsyncMock, MagicMock

    bot = MagicMock()
    bot.set_my_commands = AsyncMock()

    await publish_commands(bot)

    bot.set_my_commands.assert_awaited_once()
    arg = bot.set_my_commands.await_args.args[0]
    # PTB takes a list of BotCommand objects (or (name, description) tuples
    # in older versions). Accept either shape but verify content.
    pairs = [(getattr(c, "command", None) or c[0],
              getattr(c, "description", None) or c[1]) for c in arg]
    expected = [(name, desc) for name, desc, _ in COMMANDS]
    assert pairs == expected


def test_build_application_sets_explicit_httpx_timeouts(tmp_path):
    """PTB's defaults are 5s read/write/connect, 1s pool — too tight to
    survive ordinary Telegram-side hiccups and giving the underlying
    httpx pool no slack. We override with explicit values; this test
    pins them so a silent PTB default change can't undo the hardening
    (2026-05-28 incident — see deploy/README.md)."""
    import httpx
    from aggregator.bot.app import build_application

    cfg = load_config("config.example.toml")
    s = Storage(str(tmp_path / "t.db"))
    s.init_schema()
    s.seed_topics(cfg.topics)

    app = build_application(storage=s, scheduler=None, cfg=cfg)

    expected = httpx.Timeout(connect=10.0, read=30.0, write=20.0, pool=5.0)

    # Both clients (regular for sendMessage and get_updates for long-polling).
    # PTB v21 stores them as a tuple in Bot._request; assert the underlying
    # httpx Timeout config matches on both.
    requests = app.bot._request
    assert len(requests) == 2, "PTB Bot._request must hold both clients"
    for client in requests:
        assert client._client_kwargs["timeout"] == expected, (
            f"client {client!r} has timeout {client._client_kwargs['timeout']!r}, "
            f"expected {expected!r}"
        )


@pytest.mark.asyncio
async def test_publish_commands_swallows_errors():
    """A failure to publish commands must not crash startup."""
    from aggregator.bot.app import publish_commands
    from unittest.mock import AsyncMock, MagicMock

    bot = MagicMock()
    bot.set_my_commands = AsyncMock(side_effect=RuntimeError("telegram unreachable"))
    # Must not raise.
    await publish_commands(bot)
    # Confirm we actually reached the failing call (not silently short-circuited).
    bot.set_my_commands.assert_awaited_once()
