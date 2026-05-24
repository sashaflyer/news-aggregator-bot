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
