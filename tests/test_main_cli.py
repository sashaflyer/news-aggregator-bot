from unittest.mock import AsyncMock, patch

import pytest


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
