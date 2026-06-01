import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from aggregator.sources.rss import RssSource, _to_item, _fetch_feed

FIXTURE = Path(__file__).parent / "fixtures" / "rss_sample.xml"


def _entries():
    return [
        {"id": "https://example.com/news/btc-etf", "title": "Bitcoin ETF inflows hit record high",
         "url": "https://example.com/news/btc-etf", "summary": "Spot bitcoin ETFs saw record inflows.",
         "published_parsed": time.struct_time((2026, 5, 28, 18, 0, 0, 0, 0, 0))},
        {"id": "https://example.com/news/sol-upgrade", "title": "Solana network upgrade ships",
         "url": "https://example.com/news/sol-upgrade", "summary": "The Solana upgrade improves throughput.",
         "published_parsed": time.struct_time((2026, 5, 27, 9, 0, 0, 0, 0, 0))},
        {"id": "https://example.com/news/undated", "title": "Undated draft item",
         "url": "https://example.com/news/undated", "summary": "No pubDate.",
         "published_parsed": None},
    ]


@pytest.mark.asyncio
async def test_untagged_feed_maps_and_drops_undated():
    with patch("aggregator.sources.rss._fetch_feed", return_value=_entries()):
        items = await RssSource().fetch({"rss_feeds": ["https://x/feed"]})
    assert len(items) == 2  # undated dropped
    assert all(it.source == "rss" and it.id.startswith("rss:") for it in items)
    assert all("watchlist_symbol" not in it.metadata for it in items)


@pytest.mark.asyncio
async def test_recency_score_newer_first():
    with patch("aggregator.sources.rss._fetch_feed", return_value=_entries()):
        items = await RssSource().fetch({"rss_feeds": ["https://x/feed"]})
    btc = next(it for it in items if "ETF" in it.title)
    sol = next(it for it in items if "Solana" in it.title)
    assert btc.engagement_raw["score"] > sol.engagement_raw["score"]


@pytest.mark.asyncio
async def test_untagged_symbol_filter():
    with patch("aggregator.sources.rss._fetch_feed", return_value=_entries()):
        items = await RssSource().fetch({"rss_feeds": ["https://x/feed"], "symbols": ["SOL", "Solana"]})
    assert len(items) == 1 and "Solana" in items[0].title


@pytest.mark.asyncio
async def test_symbol_feeds_tag_items_and_skip_filter():
    # Symbol-feed items are tagged and NOT subject to symbol filtering.
    with patch("aggregator.sources.rss._fetch_feed", return_value=_entries()):
        items = await RssSource().fetch({
            "rss_symbol_feeds": {"SOL": ["https://x/tag/solana"]},
        })
    assert len(items) == 2  # both dated items kept (no filtering), undated dropped
    assert all(it.metadata["watchlist_symbol"] == "SOL" for it in items)


@pytest.mark.asyncio
async def test_search_feeds_alias_filter_and_tag():
    # Search-feed items are tagged with the symbol AND alias-filtered: only
    # entries mentioning the symbol's terms survive (unlike trusted symbol feeds).
    with patch("aggregator.sources.rss._fetch_feed", return_value=_entries()):
        items = await RssSource().fetch({
            "rss_search_feeds": [
                {"symbol": "SOL", "terms": ["SOL", "Solana"],
                 "url": "https://news.google.com/rss/search?q=Solana"},
            ],
        })
    assert len(items) == 1  # BTC item dropped by alias filter, undated dropped
    assert "Solana" in items[0].title
    assert items[0].metadata["watchlist_symbol"] == "SOL"


@pytest.mark.asyncio
async def test_search_feed_fetch_failure_is_skipped():
    with patch("aggregator.sources.rss._fetch_feed", side_effect=RuntimeError("boom")):
        items = await RssSource().fetch({
            "rss_search_feeds": [
                {"symbol": "SOL", "terms": ["SOL", "Solana"], "url": "https://x/gnews"},
            ],
        })
    assert items == []


@pytest.mark.asyncio
async def test_no_feeds_returns_empty():
    assert await RssSource().fetch({}) == []


def test_to_item_drops_unparseable_date():
    raw = {"id": "x", "title": "t", "url": "u", "summary": "s", "published_parsed": None}
    assert _to_item(raw, now=datetime(2026, 5, 28, tzinfo=timezone.utc)) is None


def test_fetch_feed_parses_real_xml():
    xml = FIXTURE.read_bytes()

    class _Resp:
        content = xml
        def raise_for_status(self): pass

    with patch("aggregator.sources.rss.httpx.get", return_value=_Resp()):
        entries = _fetch_feed("https://x/feed")
    assert len(entries) == 3
    assert entries[0]["title"] == "Bitcoin ETF inflows hit record high"
    assert entries[0]["url"] == "https://example.com/news/btc-etf"
    assert entries[2]["published_parsed"] is None
