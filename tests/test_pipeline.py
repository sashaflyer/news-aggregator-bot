from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from aggregator.config import load_config
from aggregator.sources.base import Item
from aggregator.storage import Storage


def make_item(source: str, idx: int) -> Item:
    return Item(
        id=f"{source}:{idx}",
        source=source,
        title=f"{source} item {idx}",
        url=f"https://example.com/{source}/{idx}",
        text="body",
        created_at=datetime.now(timezone.utc),
        engagement_raw={"upvotes": 100 - idx},
        metadata={},
    )


@pytest.fixture
def cfg(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(open("config.example.toml", encoding="utf-8").read())
    return load_config(cfg_path)


@pytest.fixture
def storage(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics(
        general_subreddits=["CryptoCurrency"],
        general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL", "SUI", "AVAX"],
        watchlist_schedule="0 8 * * *",
    )
    return s


@pytest.mark.asyncio
async def test_run_digest_happy_path(cfg, storage):
    from aggregator import pipeline

    reddit_items = [make_item("reddit", i) for i in range(5)]
    poly_items = [make_item("polymarket", i) for i in range(3)]

    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": reddit_items, "polymarket": poly_items}
    )), patch.object(pipeline, "_score_and_dedup",
                     side_effect=lambda items, **kw: items[: cfg.crypto_general.top_n]
    ), patch.object(pipeline, "synthesize", return_value="DIGEST TEXT"
    ), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[101])):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "ok"
    assert result.items_fetched == 8
    assert result.items_delivered >= 1
    last = storage.last_run("crypto_general")
    assert last["status"] == "ok"


@pytest.mark.asyncio
async def test_run_digest_one_source_fails(cfg, storage):
    from aggregator import pipeline

    async def fake_fetch_all(*a, **kw):
        return {"reddit": [make_item("reddit", 0)],
                "polymarket": RuntimeError("polymarket down")}

    with patch.object(pipeline, "_fetch_all", side_effect=fake_fetch_all
    ), patch.object(pipeline, "_score_and_dedup",
                     side_effect=lambda items, **kw: items
    ), patch.object(pipeline, "synthesize", return_value="DIGEST"
    ), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "partial"
    poly_health = storage.get_source_health("polymarket")
    assert poly_health is not None
    assert poly_health["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_run_digest_all_sources_fail(cfg, storage):
    from aggregator import pipeline

    async def fake_fetch_all(*a, **kw):
        return {"reddit": RuntimeError("boom"),
                "polymarket": RuntimeError("boom2")}

    with patch.object(pipeline, "_fetch_all", side_effect=fake_fetch_all
    ), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[])):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "error"
    assert storage.get_source_health("reddit")["consecutive_failures"] == 1
    assert storage.get_source_health("polymarket")["consecutive_failures"] == 1
