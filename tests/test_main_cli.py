from unittest.mock import AsyncMock, patch

import pytest

from aggregator.__main__ import _require_env, _require_env_int


def test_require_env_missing_var_message(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit) as exc:
        _require_env("TELEGRAM_BOT_TOKEN")
    assert "TELEGRAM_BOT_TOKEN" in str(exc.value)


def test_require_env_present(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    assert _require_env("TELEGRAM_BOT_TOKEN") == "abc"


def test_require_env_int_invalid(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "not-a-number")
    with pytest.raises(SystemExit) as exc:
        _require_env_int("TELEGRAM_CHAT_ID")
    assert "TELEGRAM_CHAT_ID" in str(exc.value)


@pytest.mark.asyncio
async def test_cli_oneshot_runs_pipeline(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(open("config.example.toml", encoding="utf-8").read())
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    monkeypatch.setenv("NEWS_AGGREGATOR_DATA_DIR", str(tmp_path / "data"))

    from aggregator import __main__ as m

    with patch.object(m, "run_digest", new=AsyncMock(
        return_value=type("R", (), {"run_id": 1, "status": "ok",
                                     "items_fetched": 5, "items_delivered": 3})()
    )) as fake:
        await m.cli_run_once(topic_id="crypto_general", config_path=str(cfg_path))

    fake.assert_awaited_once()
    kwargs = fake.await_args.kwargs
    assert kwargs.get("trigger") == "command" or "command" in fake.await_args.args


@pytest.mark.asyncio
async def test_cli_run_once_exits_nonzero_on_error(monkeypatch):
    from aggregator import __main__ as m

    class _R:
        run_id = 7
        status = "error"
        items_fetched = 0
        items_delivered = 0

    async def fake_run_digest(*a, **kw):
        return _R()

    # Avoid touching disk / loading config: stub _bootstrap to return placeholders.
    monkeypatch.setattr(m, "_bootstrap", lambda config_path: (object(), object()))
    monkeypatch.setattr(m, "run_digest", fake_run_digest)

    with pytest.raises(SystemExit) as exc:
        await m.cli_run_once(topic_id="any", config_path="ignored")
    assert exc.value.code == 1
