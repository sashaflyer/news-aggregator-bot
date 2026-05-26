"""Session-wide pytest fixtures.

Reddit's module-level ``USER_AGENT`` (see ``aggregator/sources/reddit.py``) hard-fails
import when neither ``REDDIT_USER_AGENT`` nor ``REDDIT_OWNER_HANDLE`` is set.
The full test suite imports the reddit source transitively at collection time,
so we inject a benign handle at conftest *import* time (before any test module
is imported, before any fixture runs).

Tests that exercise the validation itself (``test_reddit_user_agent_requires_handle``)
explicitly ``monkeypatch.delenv`` these vars and reload the module.
"""
from __future__ import annotations

import os

import pytest

# Must run at import time, not in a fixture — pytest collection imports test
# modules (which transitively import aggregator.sources.reddit) before any
# fixtures execute.
os.environ.setdefault("REDDIT_OWNER_HANDLE", "test-handle")


@pytest.fixture(autouse=True)
def _snapshot_reddit_env():
    """Snapshot/restore Reddit UA env vars per test.

    ``_bootstrap`` (in main_cli) calls ``load_dotenv()`` which leaks the dev
    ``.env``'s REDDIT_USER_AGENT into ``os.environ`` for the rest of the
    session. Tests that toggle these vars should start from a known state
    and not contaminate subsequent tests.

    Restore directly on ``os.environ`` (not via monkeypatch) so we sidestep
    monkeypatch teardown ordering issues with tests that ``importlib.reload``
    modules in a ``finally`` block.
    """
    saved = {
        k: os.environ.get(k)
        for k in ("REDDIT_USER_AGENT", "REDDIT_OWNER_HANDLE")
    }
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
