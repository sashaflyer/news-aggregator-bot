import json
from unittest.mock import MagicMock, patch

import pytest

from aggregator.config import load_config


@pytest.fixture
def cfg(tmp_path):
    return load_config("config.example.toml")


@pytest.fixture
def items():
    return [
        {"id": "reddit:1", "source": "reddit", "title": "SOL up 20%",
         "url": "https://reddit.com/1", "text": "...", "created_at": "2026-05-24T00:00:00+00:00",
         "engagement_raw": {"upvotes": 500}, "metadata": {"subreddit": "solana"}},
        {"id": "polymarket:1", "source": "polymarket", "title": "Will SUI reach $5 by July?",
         "url": "https://polymarket.com/x", "text": "...", "created_at": "2026-05-24T00:00:00+00:00",
         "engagement_raw": {"volume": 50000}, "metadata": {}},
    ]


def test_synthesize_general_crypto_calls_openai(cfg, items):
    from aggregator import synth

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="DIGEST OUTPUT"))]
    fake_resp.usage = MagicMock(prompt_tokens=100, completion_tokens=50, total_tokens=150)

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        out = synth.synthesize("crypto_general", items, cfg=cfg)

    assert out == "DIGEST OUTPUT"
    call_kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == cfg.synth.model
    assert call_kwargs["max_completion_tokens"] == cfg.synth.max_output_tokens
    prompt = call_kwargs["messages"][0]["content"]
    assert "SOL up 20%" in prompt


def test_synthesize_watchlist_includes_symbols(cfg, items):
    from aggregator import synth

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="*SOL*\n- foo"))]
    fake_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        out = synth.synthesize("crypto_watchlist", items, cfg=cfg)

    prompt = fake_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    for sym in cfg.crypto_watchlist.symbols:
        assert sym in prompt
    assert "SOL" in out


def test_synthesize_truncates_to_max_input_items(cfg):
    from aggregator import synth

    many = [
        {"id": f"r:{i}", "source": "reddit", "title": f"t{i}", "url": "u",
         "text": "", "created_at": "2026-05-24T00:00:00+00:00",
         "engagement_raw": {}, "metadata": {}}
        for i in range(200)
    ]
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="x"))]
    fake_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        synth.synthesize("crypto_general", many, cfg=cfg)

    prompt = fake_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    payload = json.loads(prompt.split("```\n")[1].split("\n```")[0])
    assert len(payload) == cfg.synth.max_input_items
