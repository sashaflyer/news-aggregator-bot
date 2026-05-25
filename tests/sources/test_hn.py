import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aggregator.sources.hn import HnSource

FIXTURE = Path(__file__).parent / "fixtures" / "hn_search.json"


@pytest.mark.asyncio
async def test_fetch_with_polymarket_tags_uses_them_as_keywords():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with patch("aggregator.sources.hn._fetch_hn", return_value=fixture) as mock:
        src = HnSource()
        items = await src.fetch({"polymarket_tags": ["crypto"]})

    mock.assert_called_once_with("crypto", 15)
    assert len(items) == 3
    assert all(it.source == "hackernews" for it in items)
    assert all(it.id.startswith("hackernews:") for it in items)
    # Engagement carries points and aliases as score (so engagement-sum sort works).
    assert items[0].engagement_raw["points"] == 250
    assert items[0].engagement_raw["score"] == 250
    assert items[0].engagement_raw["comments"] == 45


@pytest.mark.asyncio
async def test_fetch_with_explicit_hn_keywords_takes_precedence():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls = []
    def capture(q, limit):
        calls.append(q)
        return fixture
    with patch("aggregator.sources.hn._fetch_hn", side_effect=capture):
        src = HnSource()
        await src.fetch({
            "polymarket_tags": ["crypto"],     # should be ignored
            "hn_keywords": ["bitcoin", "ai"],  # used instead
        })
    assert calls == ["bitcoin", "ai"]


@pytest.mark.asyncio
async def test_fetch_with_symbols_searches_per_symbol():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls = []
    def capture(q, limit):
        calls.append(q)
        return fixture
    with patch("aggregator.sources.hn._fetch_hn", side_effect=capture):
        src = HnSource()
        await src.fetch({"symbols": ["SOL", "SUI", "AVAX"]})
    assert calls == ["SOL", "SUI", "AVAX"]


@pytest.mark.asyncio
async def test_fetch_with_empty_queries_returns_empty():
    src = HnSource()
    assert await src.fetch({}) == []
    assert await src.fetch({"polymarket_tags": [], "symbols": []}) == []


@pytest.mark.asyncio
async def test_to_item_preserves_hn_link_when_no_external_url():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with patch("aggregator.sources.hn._fetch_hn", return_value=fixture):
        src = HnSource()
        items = await src.fetch({"polymarket_tags": ["crypto"]})

    # Third fixture item is an Ask HN with the HN URL itself as `url`.
    ask_hn = next(it for it in items if "Ask HN" in it.title)
    assert "news.ycombinator.com" in ask_hn.url
    assert ask_hn.text  # has story_text body
