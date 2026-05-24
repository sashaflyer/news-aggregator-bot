import httpx
import pytest
import respx

from aggregator.config import load_config


@pytest.fixture
def cfg():
    return load_config("config.example.toml")


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    from aggregator.delivery import telegram
    monkeypatch.setattr(telegram, "_BACKOFF_BASE", 1.0001)


@pytest.mark.asyncio
async def test_short_message_sent_once(cfg):
    from aggregator.delivery import telegram

    with respx.mock(base_url="https://api.telegram.org") as mock:
        route = mock.post("/botTEST_TOKEN/sendMessage").mock(
            return_value=httpx.Response(200, json={"ok": True, "result": {"message_id": 101}})
        )
        msg_ids = await telegram.send_digest("hello world", topic_id="crypto_general", cfg=cfg)
        assert msg_ids == [101]
        assert route.call_count == 1
        body = route.calls[0].request.read().decode()
        assert "hello world" in body
        assert "MarkdownV2" in body


@pytest.mark.asyncio
async def test_long_message_chunked(cfg):
    from aggregator.delivery import telegram

    text = ("p1\n\n" * 1500) + "end"
    with respx.mock(base_url="https://api.telegram.org") as mock:
        route = mock.post("/botTEST_TOKEN/sendMessage").mock(
            side_effect=[
                httpx.Response(200, json={"ok": True, "result": {"message_id": 201}}),
                httpx.Response(200, json={"ok": True, "result": {"message_id": 202}}),
            ]
        )
        msg_ids = await telegram.send_digest(text, topic_id="crypto_general", cfg=cfg)
        assert len(msg_ids) >= 2


@pytest.mark.asyncio
async def test_retry_on_5xx_then_success(cfg):
    from aggregator.delivery import telegram

    with respx.mock(base_url="https://api.telegram.org") as mock:
        mock.post("/botTEST_TOKEN/sendMessage").mock(
            side_effect=[
                httpx.Response(500, json={"ok": False}),
                httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
            ]
        )
        msg_ids = await telegram.send_digest("x", topic_id="crypto_general", cfg=cfg)
        assert msg_ids == [1]


@pytest.mark.asyncio
async def test_returns_empty_on_persistent_failure(cfg):
    from aggregator.delivery import telegram

    with respx.mock(base_url="https://api.telegram.org") as mock:
        mock.post("/botTEST_TOKEN/sendMessage").mock(
            return_value=httpx.Response(500, json={"ok": False})
        )
        msg_ids = await telegram.send_digest("x", topic_id="crypto_general", cfg=cfg)
        assert msg_ids == []
