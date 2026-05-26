import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aggregator.sources.reddit import RedditSource

FIXTURE = Path(__file__).parent / "fixtures" / "reddit_subreddit_hot.json"


@pytest.mark.asyncio
async def test_fetch_returns_items_from_subreddits():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    with patch("aggregator.sources.reddit._fetch_subreddit", return_value=fixture):
        src = RedditSource()
        items = await src.fetch({
            "subreddits": ["CryptoCurrency"],
            "polymarket_tags": [],
        })

    assert len(items) > 0
    assert all(it.source == "reddit" for it in items)
    assert all(it.id.startswith("reddit:") for it in items)
    assert all(it.url.startswith("http") for it in items)
    assert all("upvotes" in it.engagement_raw or "score" in it.engagement_raw
               for it in items)


@pytest.mark.asyncio
async def test_fetch_handles_empty_subreddit_list():
    src = RedditSource()
    items = await src.fetch({"subreddits": []})
    assert items == []


@pytest.mark.asyncio
async def test_fetch_with_symbol_queries():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with patch("aggregator.sources.reddit._search_reddit", return_value=fixture):
        src = RedditSource()
        items = await src.fetch({"symbols": ["SOL"]})
    assert all(it.source == "reddit" for it in items)


def test_fetch_subreddit_hits_hot_json_and_transforms():
    """Verify the new direct-hit /r/<sub>/hot.json fetcher shapes output correctly."""
    import io
    import json as _json
    from unittest.mock import patch

    from aggregator.sources.reddit import _fetch_subreddit

    fake_reddit_json = {
        "data": {
            "children": [
                {"data": {
                    "id": "abc123",
                    "title": "Bitcoin hit $200k today",
                    "permalink": "/r/CryptoCurrency/comments/abc123/title/",
                    "url": "https://external.example.com/something",
                    "selftext": "body text here",
                    "created_utc": 1716700000.0,
                    "score": 1234,
                    "num_comments": 56,
                    "subreddit": "CryptoCurrency",
                    "author": "satoshi_jr",
                    "upvote_ratio": 0.95,
                }},
                {"data": {
                    "id": "def456",
                    "title": "Daily discussion",
                    "permalink": "/r/CryptoCurrency/comments/def456/title/",
                    "url": "",
                    "selftext": "",
                    "created_utc": 1716800000.0,
                    "score": 50,
                    "num_comments": 200,
                    "subreddit": "CryptoCurrency",
                    "author": "AutoModerator",
                    "upvote_ratio": 1.0,
                }},
            ]
        }
    }

    class FakeResp:
        def __init__(self, data):
            self._data = data
        def read(self):
            return _json.dumps(self._data).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=FakeResp(fake_reddit_json)) as mock_url:
        posts = _fetch_subreddit("CryptoCurrency", limit=25)

    # URL hit was /hot.json with the right sub.
    req = mock_url.call_args.args[0]
    assert "/r/CryptoCurrency/hot.json" in req.full_url
    assert "limit=25" in req.full_url
    # User-Agent header set.
    assert "news-aggregator" in req.get_header("User-agent", "")

    # Shape: 2 posts, each with reddit_id and full reddit.com URL constructed from permalink.
    assert len(posts) == 2
    assert posts[0]["reddit_id"] == "abc123"
    assert posts[0]["url"] == "https://www.reddit.com/r/CryptoCurrency/comments/abc123/title/"
    assert posts[0]["engagement"]["score"] == 1234
    assert posts[0]["engagement"]["upvote_ratio"] == 0.95
    assert posts[1]["author"] == "AutoModerator"


def test_fetch_subreddit_returns_empty_on_network_error():
    import urllib.error
    from unittest.mock import patch
    from aggregator.sources.reddit import _fetch_subreddit

    with patch("urllib.request.urlopen",
               side_effect=urllib.error.URLError("connection refused")):
        posts = _fetch_subreddit("CryptoCurrency", limit=25)
    assert posts == []


def test_to_item_falls_back_to_url_when_id_is_unstable_search_counter():
    """``_search_reddit`` returns ids like ``R1``/``R2`` per call; using them
    directly causes cross-symbol collisions inside a single watchlist topic.
    """
    from aggregator.sources.reddit import _to_item

    raw_a = {"id": "R1", "title": "BTC post",
             "url": "https://reddit.com/r/Bitcoin/comments/aaa/title/",
             "subreddit": "Bitcoin", "score": 100,
             "engagement": {"score": 100, "num_comments": 10}}
    raw_b = {"id": "R1", "title": "ETH post",
             "url": "https://reddit.com/r/Ethereum/comments/bbb/title/",
             "subreddit": "Ethereum", "score": 50,
             "engagement": {"score": 50, "num_comments": 5}}

    a = _to_item(raw_a)
    b = _to_item(raw_b)
    assert a.id != b.id, "unstable R1 ids must not collide across searches"
    assert a.id.endswith("aaa/title/")


def test_to_item_uses_reddit_id_when_present():
    """Subreddit hot fetcher always sets reddit_id — prefer it as the stable key."""
    from aggregator.sources.reddit import _to_item
    raw = {"reddit_id": "abc123", "id": "R1",
           "url": "https://reddit.com/r/X/comments/abc123/title/",
           "title": "t", "subreddit": "X", "score": 1,
           "engagement": {"score": 1, "num_comments": 0}}
    item = _to_item(raw)
    assert item.id == "reddit:abc123"


def test_fetch_subreddit_drops_stickied_and_removed():
    import json as _json
    from unittest.mock import patch

    from aggregator.sources.reddit import _fetch_subreddit

    fake = {"data": {"children": [
        {"data": {"id": "a", "title": "real", "stickied": False,
                  "removed_by_category": None, "selftext": "",
                  "subreddit": "x", "permalink": "/r/x/comments/a/",
                  "url": "https://x/", "score": 1, "num_comments": 0,
                  "author": "u", "created_utc": 0}},
        {"data": {"id": "b", "title": "sticky", "stickied": True,
                  "removed_by_category": None, "selftext": "",
                  "subreddit": "x", "permalink": "/r/x/comments/b/",
                  "url": "https://x/", "score": 1, "num_comments": 0,
                  "author": "u", "created_utc": 0}},
        {"data": {"id": "c", "title": "removed", "stickied": False,
                  "removed_by_category": "moderator",
                  "selftext": "[removed]",
                  "subreddit": "x", "permalink": "/r/x/comments/c/",
                  "url": "https://x/", "score": 1, "num_comments": 0,
                  "author": "u", "created_utc": 0}},
    ]}}

    class _R:
        def read(self):
            return _json.dumps(fake).encode()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    with patch("urllib.request.urlopen", return_value=_R()):
        posts = _fetch_subreddit("x", limit=10)
    ids = [p["reddit_id"] for p in posts]
    assert ids == ["a"]


def test_to_item_unescapes_html_entities():
    from aggregator.sources.reddit import _to_item
    raw = {
        "reddit_id": "abc",
        "title": "Tether&#39;s Q1 attestation",
        "subreddit": "CryptoCurrency",
        "url": "https://x.example/",
        "score": 1,
        "num_comments": 0,
        "author": "u",
        "created_utc": 0,
        "selftext": "Don&amp;t panic",
        "engagement": {"score": 1, "num_comments": 0},
    }
    item = _to_item(raw)
    assert "'" in item.title
    assert "&#39;" not in item.title
    assert "&amp;" not in item.text


def test_fetch_subreddit_429_logs_retry_after_and_returns_empty(caplog):
    import io
    import urllib.error
    from unittest.mock import patch

    from aggregator.sources.reddit import _fetch_subreddit

    err = urllib.error.HTTPError(
        url="http://x",
        code=429,
        msg="x",
        hdrs={"Retry-After": "12"},
        fp=io.BytesIO(b""),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with caplog.at_level("WARNING"):
            items = _fetch_subreddit("test", limit=5)
    assert items == []
    assert any(
        "429" in rec.message and "retry_after" in rec.message.lower()
        for rec in caplog.records
    )


def test_fetch_subreddit_5xx_logs_status(caplog):
    import io
    import urllib.error
    from unittest.mock import patch

    from aggregator.sources.reddit import _fetch_subreddit

    err = urllib.error.HTTPError(
        url="http://x", code=503, msg="x", hdrs={}, fp=io.BytesIO(b"")
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with caplog.at_level("WARNING"):
            items = _fetch_subreddit("test", limit=5)
    assert items == []
    assert any("503" in rec.message for rec in caplog.records)


def test_reddit_user_agent_requires_handle(monkeypatch):
    """Without REDDIT_USER_AGENT or REDDIT_OWNER_HANDLE, module import must fail."""
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    monkeypatch.delenv("REDDIT_OWNER_HANDLE", raising=False)
    import importlib
    import aggregator.sources._ua as ua
    try:
        with pytest.raises(RuntimeError, match="REDDIT_USER_AGENT"):
            importlib.reload(ua)
    finally:
        monkeypatch.setenv("REDDIT_OWNER_HANDLE", "test-handle")
        importlib.reload(ua)


def test_reddit_user_agent_from_handle(monkeypatch):
    """REDDIT_OWNER_HANDLE composes a contact-bearing UA without REDDIT_USER_AGENT."""
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    monkeypatch.setenv("REDDIT_OWNER_HANDLE", "alice")
    import importlib
    import aggregator.sources._ua as ua
    try:
        importlib.reload(ua)
        assert "/u/alice" in ua.USER_AGENT
    finally:
        monkeypatch.setenv("REDDIT_OWNER_HANDLE", "test-handle")
        importlib.reload(ua)
