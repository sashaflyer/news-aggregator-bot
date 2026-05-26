"""Operator-configured User-Agent resolution.

Centralized so the Reddit source (``aggregator.sources.reddit``) and the
vendored public-search path (``aggregator.vendor.last30days.reddit_public``)
both attach the same contact-bearing UA. Without this, rate-limit budgets
attribute requests to two different identities and UA-based throttling
becomes incoherent.

Reddit's API policy rejects/throttles generic UAs. We refuse to start
unless REDDIT_USER_AGENT is set explicitly or REDDIT_OWNER_HANDLE
identifies a contact handle we can compose into a UA.
"""
from __future__ import annotations

import os


_REDDIT_OWNER_HANDLE = os.environ.get("REDDIT_OWNER_HANDLE", "").strip()


def _default_user_agent() -> str:
    if not _REDDIT_OWNER_HANDLE:
        raise RuntimeError(
            "Reddit requires a contact-bearing User-Agent. "
            "Set REDDIT_USER_AGENT to a fully-formed UA string, or set "
            "REDDIT_OWNER_HANDLE to your reddit username (no leading 'u/') "
            "and we'll compose one."
        )
    return (
        f"news-aggregator/0.1 by /u/{_REDDIT_OWNER_HANDLE} "
        f"(contact via reddit dm)"
    )


USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "").strip() or _default_user_agent()
