"""Verify the vendored Reddit public-search path uses the unified UA."""
from __future__ import annotations

import importlib


def test_reddit_public_uses_compliant_user_agent(monkeypatch):
    # An earlier test (test_main_cli::test_cli_oneshot_runs_pipeline) calls
    # ``_bootstrap`` which invokes ``load_dotenv()`` and leaks the dev .env's
    # REDDIT_USER_AGENT into os.environ for the rest of the session. We
    # explicitly clear it here so the composed-from-handle path runs.
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    monkeypatch.setenv("REDDIT_OWNER_HANDLE", "alice")
    captured: dict[str, str] = {}

    def fake_urlopen(req, *a, **kw):
        captured["ua"] = req.headers.get("User-agent", "")
        raise RuntimeError("stop")

    from aggregator.sources import _ua
    importlib.reload(_ua)
    from aggregator.vendor.last30days import reddit_public as rp
    importlib.reload(rp)  # re-evaluate USER_AGENT import
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    try:
        try:
            rp.search(query="x", depth="quick")
        except RuntimeError:
            pass
        assert "alice" in captured.get("ua", ""), captured
        assert "Chrome" not in captured.get("ua", "")
    finally:
        # Restore module state with the conftest-injected handle so other
        # tests aren't affected by the alice handle leaking through _ua.
        monkeypatch.setenv("REDDIT_OWNER_HANDLE", "test-handle")
        importlib.reload(_ua)
        importlib.reload(rp)
