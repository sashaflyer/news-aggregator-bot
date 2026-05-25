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
