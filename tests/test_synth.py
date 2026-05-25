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
    for sym in cfg.topics["crypto_watchlist"].canonical_symbols:
        assert sym in prompt
    # Aliases must also reach the prompt so the LLM groups them under the ticker.
    for entry in cfg.topics["crypto_watchlist"].watch:
        for alias in entry.aliases:
            assert alias in prompt
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
    # Items JSON is the LAST fenced block in the prompt (after "ITEMS (JSON):").
    items_block = prompt.split("ITEMS (JSON):\n```\n", 1)[1].rsplit("\n```", 1)[0]
    payload = json.loads(items_block)
    assert len(payload) == cfg.synth.max_input_items


def test_short_body_passes_through_unchanged(cfg):
    from aggregator import synth

    item = {"id": "r:1", "source": "reddit", "title": "t", "url": "u",
            "text": "short body", "created_at": "2026-05-24T00:00:00+00:00",
            "engagement_raw": {}, "metadata": {}}
    out = synth._shorten_body(item, "crypto")
    assert out is item or out["text"] == "short body"


def test_long_body_gets_trimmed(cfg):
    from aggregator import synth

    long_body = "word " * 300  # 300 words >> 120
    item = {"id": "r:1", "source": "reddit", "title": "Bitcoin hit ATH", "url": "u",
            "text": long_body, "created_at": "2026-05-24T00:00:00+00:00",
            "engagement_raw": {}, "metadata": {}}
    out = synth._shorten_body(item, "crypto")
    assert len(out["text"].split()) <= synth._MAX_BODY_WORDS + 1  # allow ellipsis token
    assert out["id"] == "r:1"  # rest of fields preserved


def test_synthesize_escapes_html_in_titles_and_text(cfg):
    """Defense-in-depth: titles/bodies are HTML-escaped before the LLM sees
    them so a prompt-injected title can't surface attacker-chosen tags in the
    HTML-rendered Telegram digest.
    """
    from aggregator import synth

    nasty = [{
        "id": "reddit:1", "source": "reddit",
        "title": "<script>alert(1)</script> AT&T earnings",
        "url": "https://reddit.com/1",
        "text": '<a href="https://evil.example">click</a>',
        "created_at": "2026-05-24T00:00:00+00:00",
        "engagement_raw": {"upvotes": 1}, "metadata": {},
    }]
    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="OK"))]
    fake_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        synth.synthesize("crypto_general", nasty, cfg=cfg)

    prompt = fake_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "<script>" not in prompt
    assert "&lt;script&gt;" in prompt
    assert "AT&amp;T" in prompt
    assert '<a href="https://evil.example">' not in prompt


def test_synthesize_raises_on_empty_content(cfg, items):
    """Empty LLM output must NOT be silently delivered; raise instead so the
    pipeline doesn't mark every ranked item as delivered for a blank message.
    """
    from aggregator import synth

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content=""))]
    fake_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=0, total_tokens=1)
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        with pytest.raises(RuntimeError, match="empty"):
            synth.synthesize("crypto_general", items, cfg=cfg)


def test_synthesize_handles_none_usage(cfg, items):
    """If the OpenAI response has usage=None, don't crash after a good call."""
    from aggregator import synth

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="DIGEST"))]
    fake_resp.usage = None
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        out = synth.synthesize("crypto_general", items, cfg=cfg)
    assert out == "DIGEST"


@pytest.mark.asyncio
async def test_synthesize_async_runs_blocking_call_off_loop(cfg, items):
    """``synthesize_async`` must dispatch to a worker thread, not block the loop."""
    import threading

    from aggregator import synth

    loop_thread = threading.current_thread()
    seen_thread: list[threading.Thread] = []

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="OK"))]
    fake_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)

    def fake_create(**_kwargs):
        seen_thread.append(threading.current_thread())
        return fake_resp

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = fake_create

    with patch.object(synth, "_get_client", return_value=fake_client):
        out = await synth.synthesize_async("crypto_general", items, cfg=cfg)

    assert out == "OK"
    assert seen_thread and seen_thread[0] is not loop_thread


def test_synthesize_trims_long_items_in_prompt(cfg):
    from unittest.mock import MagicMock, patch
    from aggregator import synth

    items = [
        {"id": f"r:{i}", "source": "reddit", "title": f"t{i}", "url": "u",
         "text": "word " * 400, "created_at": "2026-05-24T00:00:00+00:00",
         "engagement_raw": {}, "metadata": {}}
        for i in range(3)
    ]

    fake_resp = MagicMock()
    fake_resp.choices = [MagicMock(message=MagicMock(content="ok"))]
    fake_resp.usage = MagicMock(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_resp

    with patch.object(synth, "_get_client", return_value=fake_client):
        synth.synthesize("crypto_general", items, cfg=cfg)

    prompt = fake_client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    # Each item's text in the JSON payload should be short, not 400 words.
    # Naive check: the prompt should not contain "word " repeated more than
    # ~_MAX_BODY_WORDS times per item (3 items * 120 = 360 instances tops).
    assert prompt.count("word") < 3 * synth._MAX_BODY_WORDS + 50
