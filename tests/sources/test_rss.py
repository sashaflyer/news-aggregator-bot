import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import respx

from aggregator.sources import rss as rss_module
from aggregator.sources.rss import (
    RssSource, _to_item, _parse_entries, _gather_urls, _UA, _RECENCY_BASE,
)

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


def _route_all(urls: list[str]) -> respx.MockRouter:
    """Mock every URL in ``urls`` to return a 200 with the RSS fixture body.

    Tests that don't care about the response body just need the HTTP path
    to succeed so the parser is invoked; we then patch ``_parse_entries``
    to return canned entries. This keeps the seam at the pure parser
    function — the network is exercised only enough to prove the URL was
    actually requested.
    """
    router = respx.mock(assert_all_called=False)
    for url in urls:
        router.get(url).mock(return_value=httpx.Response(200, content=FIXTURE.read_bytes()))
    return router


@pytest.mark.asyncio
async def test_untagged_feed_maps_and_drops_undated():
    url = "https://x/feed"
    with _route_all([url]), patch("aggregator.sources.rss._parse_entries",
                                   return_value=_entries()):
        items = await RssSource().fetch({"rss_feeds": [url]})
    assert len(items) == 2  # undated dropped
    assert all(it.source == "rss" and it.id.startswith("rss:") for it in items)
    assert all("watchlist_symbol" not in it.metadata for it in items)


@pytest.mark.asyncio
async def test_recency_score_newer_first():
    fixed_now = datetime(2026, 5, 28, 20, 0, 0, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    url = "https://x/feed"
    with _route_all([url]), \
         patch("aggregator.sources.rss._parse_entries", return_value=_entries()), \
         patch.object(rss_module, "datetime", FixedDateTime):
        items = await RssSource().fetch({"rss_feeds": [url]})
    btc = next(it for it in items if "ETF" in it.title)
    sol = next(it for it in items if "Solana" in it.title)
    assert btc.engagement_raw["score"] > sol.engagement_raw["score"]
    assert btc.engagement_raw["score"] == pytest.approx(_RECENCY_BASE - 2.0)
    assert sol.engagement_raw["score"] == pytest.approx(_RECENCY_BASE - 35.0)


@pytest.mark.asyncio
async def test_untagged_symbol_filter():
    url = "https://x/feed"
    with _route_all([url]), patch("aggregator.sources.rss._parse_entries",
                                   return_value=_entries()):
        items = await RssSource().fetch({"rss_feeds": [url], "symbols": ["SOL", "Solana"]})
    assert len(items) == 1 and "Solana" in items[0].title


@pytest.mark.asyncio
async def test_symbol_feeds_tag_items_and_skip_filter():
    # Symbol-feed items are tagged and NOT subject to symbol filtering.
    url = "https://x/tag/solana"
    with _route_all([url]), patch("aggregator.sources.rss._parse_entries",
                                   return_value=_entries()):
        items = await RssSource().fetch({
            "rss_symbol_feeds": {"SOL": [url]},
        })
    assert len(items) == 2  # both dated items kept (no filtering), undated dropped
    assert all(it.metadata["watchlist_symbol"] == "SOL" for it in items)


@pytest.mark.asyncio
async def test_search_feeds_alias_filter_and_tag():
    # Search-feed items are tagged with the symbol AND alias-filtered: only
    # entries mentioning the symbol's terms survive (unlike trusted symbol feeds).
    url = "https://news.google.com/rss/search?q=Solana"
    with _route_all([url]), patch("aggregator.sources.rss._parse_entries",
                                   return_value=_entries()):
        items = await RssSource().fetch({
            "rss_search_feeds": [
                {"symbol": "SOL", "terms": ["SOL", "Solana"], "url": url},
            ],
        })
    assert len(items) == 1  # BTC item dropped by alias filter, undated dropped
    assert "Solana" in items[0].title
    assert items[0].metadata["watchlist_symbol"] == "SOL"


@pytest.mark.asyncio
async def test_search_feed_fetch_failure_is_skipped():
    # A 500 on a single feed must not poison the batch — the source
    # contract is "one failure, the rest delivered".
    url = "https://x/gnews"
    with respx.mock(assert_all_called=False) as router:
        router.get(url).mock(return_value=httpx.Response(500, text="server error"))
        items = await RssSource().fetch({
            "rss_search_feeds": [
                {"symbol": "SOL", "terms": ["SOL", "Solana"], "url": url},
            ],
        })
    assert items == []


@pytest.mark.asyncio
async def test_no_feeds_returns_empty():
    assert await RssSource().fetch({}) == []


def test_to_item_drops_unparseable_date():
    raw = {"id": "x", "title": "t", "url": "u", "summary": "s", "published_parsed": None}
    assert _to_item(raw, now=datetime(2026, 5, 28, tzinfo=timezone.utc)) is None


def test_parse_entries_against_real_xml():
    """Smoke test: the parser handles a real feed fixture end-to-end.

    Confirms `_parse_entries` is a real feedparser call (not bypassed by
    the test's HTTP layer) by feeding it bytes that exercise the 3-entry
    shape — including the 3rd entry's missing `published_parsed`.
    """
    entries = _parse_entries(FIXTURE.read_bytes())
    assert len(entries) == 3
    assert entries[0]["title"] == "Bitcoin ETF inflows hit record high"
    assert entries[0]["url"] == "https://example.com/news/btc-etf"
    assert entries[2]["published_parsed"] is None


@pytest.mark.asyncio
async def test_gather_urls_uses_one_client():
    """All URLs in a single ``_gather_urls`` call share one AsyncClient.

    This is the keepalive / connection-pool win: N feeds to the same host
    reuse a TCP/TLS connection instead of paying handshake cost per fetch.
    We assert it indirectly by patching `httpx.AsyncClient` and counting
    instantiations.
    """
    from contextlib import asynccontextmanager

    urls = ["https://x/feed-1", "https://x/feed-2", "https://x/feed-3"]
    instances: list[httpx.AsyncClient] = []
    real_init = httpx.AsyncClient.__init__

    def counting_init(self, *a, **kw):
        real_init(self, *a, **kw)
        instances.append(self)

    with respx.mock(assert_all_called=False) as router:
        for u in urls:
            router.get(u).mock(return_value=httpx.Response(200, content=b"<rss/>"))
        with patch.object(httpx.AsyncClient, "__init__", counting_init), \
             patch("aggregator.sources.rss._parse_entries", return_value=[]):
            await _gather_urls(urls)

    assert len(instances) == 1, (
        f"_gather_urls should open exactly one AsyncClient per call, got {len(instances)}"
    )


@pytest.mark.asyncio
async def test_fetch_sends_user_agent_header():
    """The User-Agent is part of the polite-bot contract: some RSS
    endpoints reject requests with the python-httpx default UA."""
    url = "https://x/feed"
    with respx.mock(assert_all_called=False) as router:
        route = router.get(url).mock(return_value=httpx.Response(200, content=b"<rss/>"))
        with patch("aggregator.sources.rss._parse_entries", return_value=[]):
            await RssSource().fetch({"rss_feeds": [url]})
    assert route.calls[0].request.headers["User-Agent"] == _UA
