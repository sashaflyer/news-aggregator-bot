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
        items = await src.fetch({"symbols": ["BTC"]})
    for it in items:
        assert "BTC" in it.title.upper() or "BTC" in it.text.upper()


@pytest.mark.asyncio
async def test_fetch_handles_empty_queries():
    src = PolymarketSource()
    assert await src.fetch({}) == []
