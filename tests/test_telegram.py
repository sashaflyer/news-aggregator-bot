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
        assert '"parse_mode":"HTML"' in body


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


@pytest.mark.asyncio
async def test_parse_error_falls_back_to_plain_text(cfg):
    """When Telegram rejects the Markdown, retry the chunk as plain text."""
    from aggregator.delivery import telegram

    parse_error = httpx.Response(
        400, json={"ok": False, "error_code": 400,
                   "description": "Bad Request: can't parse entities: Can't find end of the entity starting at byte offset 1799"}
    )
    success = httpx.Response(200, json={"ok": True, "result": {"message_id": 999}})

    with respx.mock(base_url="https://api.telegram.org") as mock:
        # respx serves responses in order across consecutive calls.
        mock.post("/botTEST_TOKEN/sendMessage").mock(
            side_effect=[parse_error, success]
        )
        msg_ids = await telegram.send_digest(
            "*unclosed bold _italic", topic_id="crypto_general", cfg=cfg
        )
        assert msg_ids == [999]

        # Inspect the two calls: first with parse_mode, second without.
        calls = mock.routes[0].calls
        assert len(calls) == 2
        body0 = calls[0].request.read().decode()
        body1 = calls[1].request.read().decode()
        assert "HTML" in body0  # first attempt used configured parse_mode
        # Second attempt: parse_mode is OMITTED from the payload (Telegram
        # rejects `parse_mode: null` as unsupported).
        assert '"parse_mode"' not in body1


@pytest.mark.asyncio
async def test_parse_error_fallback_also_fails(cfg):
    """If plain-text fallback also fails, return [] without further retries."""
    from aggregator.delivery import telegram

    parse_error = httpx.Response(
        400, json={"ok": False, "description": "Bad Request: can't parse entities: x"}
    )
    server_error = httpx.Response(500, json={"ok": False, "description": "server error"})

    with respx.mock(base_url="https://api.telegram.org") as mock:
        mock.post("/botTEST_TOKEN/sendMessage").mock(
            side_effect=[parse_error, server_error]
        )
        msg_ids = await telegram.send_digest(
            "*bad markdown", topic_id="crypto_general", cfg=cfg
        )
        assert msg_ids == []
        # Should have made exactly 2 attempts: the markdown one and one plain-text fallback.
        # No additional retries after fallback fails.
        assert mock.routes[0].call_count == 2


@pytest.mark.asyncio
async def test_non_retriable_4xx_short_circuits(cfg):
    """401/403/404 must NOT burn three retries with backoff — token revoked,
    bot blocked, or chat gone won't change between attempts."""
    from aggregator.delivery import telegram

    with respx.mock(base_url="https://api.telegram.org") as mock:
        route = mock.post("/botTEST_TOKEN/sendMessage").mock(
            return_value=httpx.Response(
                403, json={"ok": False, "description": "Forbidden: bot was blocked by the user"}
            )
        )
        msg_ids = await telegram.send_digest("hi", topic_id="crypto_general", cfg=cfg)
        assert msg_ids == []
        # Exactly one attempt, no retry loop.
        assert route.call_count == 1


@pytest.mark.asyncio
async def test_chunk_suffix_uses_html_italic_under_html_mode(cfg):
    """Page counter must be valid HTML, not Markdown underscores."""
    from aggregator.delivery import telegram

    text = ("p1\n\n" * 1500) + "end"
    with respx.mock(base_url="https://api.telegram.org") as mock:
        route = mock.post("/botTEST_TOKEN/sendMessage").mock(
            side_effect=[
                httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
                httpx.Response(200, json={"ok": True, "result": {"message_id": 2}}),
            ]
        )
        await telegram.send_digest(text, topic_id="crypto_general", cfg=cfg)
        first = route.calls[0].request.read().decode()
        assert "<i>(1/" in first
        assert "_(1/" not in first


def test_chunk_respects_utf16_budget_with_suffix():
    """Telegram counts UTF-16 code units, not Python chars. Each emoji is
    1 char but 2 code units, so a 2100-char emoji string is 4200 code units
    and must split into chunks that each fit under the 4096 hard cap."""
    from aggregator.delivery.telegram import _chunk

    text = "\U0001F600" * 2100
    chunks = _chunk(text)
    for c in chunks:
        assert len(c.encode("utf-16-le")) // 2 <= 4096


@pytest.mark.asyncio
async def test_normal_5xx_still_retries_after_fix(cfg):
    """Pre-existing 5xx retry behavior must still work alongside the new fallback."""
    from aggregator.delivery import telegram

    with respx.mock(base_url="https://api.telegram.org") as mock:
        mock.post("/botTEST_TOKEN/sendMessage").mock(
            side_effect=[
                httpx.Response(500, json={"ok": False}),
                httpx.Response(200, json={"ok": True, "result": {"message_id": 7}}),
            ]
        )
        msg_ids = await telegram.send_digest("ok", topic_id="crypto_general", cfg=cfg)
        assert msg_ids == [7]
