import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aggregator.sources.polymarket import PolymarketSource

FIXTURE = Path(__file__).parent / "fixtures" / "polymarket_crypto.json"


@pytest.mark.asyncio
async def test_fetch_by_tag_returns_items():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with patch("aggregator.sources.polymarket._fetch_by_tag", return_value=fixture):
        src = PolymarketSource()
        items = await src.fetch({"polymarket_tags": ["crypto"]})
    assert len(items) > 0
    assert all(it.source == "polymarket" for it in items)
    assert all(it.id.startswith("polymarket:") for it in items)


@pytest.mark.asyncio
async def test_fetch_with_symbols_filters_by_question():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with patch("aggregator.sources.polymarket._fetch_by_tag", return_value=fixture):
        src = PolymarketSource()
        # Caller must opt into polymarket_tags explicitly; symbols alone no
        # longer back-fills the broad 'crypto' tag (audit M8).
        items = await src.fetch({
            "polymarket_tags": ["crypto"],
            "symbols": ["BTC"],
        })
    for it in items:
        assert "BTC" in it.title.upper() or "BTC" in it.text.upper()


@pytest.mark.asyncio
async def test_polymarket_skips_when_no_tags_configured():
    """Symbols alone must not implicitly back-fill the 'crypto' tag (audit M8)."""
    from aggregator.sources.polymarket import PolymarketSource
    with patch("aggregator.sources.polymarket._fetch_by_tag") as mock_fetch:
        src = PolymarketSource()
        items = await src.fetch({"polymarket_tags": [], "symbols": ["SOL"]})
    assert items == []
    mock_fetch.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_handles_empty_queries():
    src = PolymarketSource()
    assert await src.fetch({}) == []


@pytest.mark.asyncio
async def test_symbol_filter_word_boundary_not_substring():
    """`ETH` must not match `ETHICS`, `ADA` must not match `Canada`, etc."""
    decoys = [
        {
            "event_id": "x1",
            "title": "Will ethics rules pass in Congress",
            "question": "Will ethics rules pass in Congress",
            "url": "https://polymarket.com/event/x1",
            "date": "2026-05-24",
        },
        {
            "event_id": "x2",
            "title": "Trade flows in Canada vs Mexico",
            "question": "Canada trade",
            "url": "https://polymarket.com/event/x2",
            "date": "2026-05-24",
        },
    ]
    with patch("aggregator.sources.polymarket._fetch_by_tag", return_value=decoys):
        src = PolymarketSource()
        items = await src.fetch({"symbols": ["ETH", "ADA"]})
    assert items == []


def test_parse_created_at_bad_returns_none_not_now():
    from aggregator.sources._common import parse_created_at
    assert parse_created_at("not a date") is None


def test_fetch_by_tag_does_not_raise_on_signature():
    """Regression: _fetch_by_tag must satisfy the vendor's required positional
    args (from_date, to_date). All other tests mock _fetch_by_tag directly so
    they wouldn't catch a TypeError at the upstream call site.
    """
    from aggregator.sources.polymarket import _fetch_by_tag
    with patch(
        "aggregator.sources.polymarket._upstream.search_polymarket",
        return_value={"events": []},
    ) as mock:
        result = _fetch_by_tag("crypto")
    assert result == []
    mock.assert_called_once()


def test_to_item_reads_volume_from_volume1mo():
    """Vendor exposes volume under volume1mo / volume24hr, not top-level volume."""
    from aggregator.sources.polymarket import _to_item
    raw = {
        "event_id": "evt1",
        "title": "Will X happen?",
        "url": "https://polymarket.com/event/x",
        "volume24hr": 100.0,
        "volume1mo": 4000.0,
        "date": "2026-05-20",
    }
    item = _to_item(raw)
    assert item.engagement_raw["volume"] == 4000.0
    assert item.created_at.year == 2026
    assert item.created_at.month == 5
    assert item.created_at.day == 20
