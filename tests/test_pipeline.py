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
def storage(tmp_path, cfg):
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    s.seed_topics(cfg.topics)
    return s


@pytest.mark.asyncio
async def test_run_digest_happy_path(cfg, storage):
    from aggregator import pipeline

    rss_items = [make_item("rss", i) for i in range(5)]
    poly_items = [make_item("polymarket", i) for i in range(3)]

    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": rss_items, "polymarket": poly_items}
    )), patch.object(pipeline, "_score_and_dedup",
                     side_effect=lambda items, **kw: items[: cfg.topics["crypto_general"].top_n]
    ), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="DIGEST TEXT"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[101])):
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
        return {"rss": [make_item("rss", 0)],
                "polymarket": RuntimeError("polymarket down")}

    with patch.object(pipeline, "_fetch_all", side_effect=fake_fetch_all
    ), patch.object(pipeline, "_score_and_dedup",
                     side_effect=lambda items, **kw: items
    ), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="DIGEST"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
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
        return {"rss": RuntimeError("boom"),
                "polymarket": RuntimeError("boom2")}

    with patch.object(pipeline, "_fetch_all", side_effect=fake_fetch_all
    ), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[])):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "error"
    assert storage.get_source_health("rss")["consecutive_failures"] == 1
    assert storage.get_source_health("polymarket")["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_all_sources_fail_sends_failure_heartbeat(cfg, storage):
    from aggregator import pipeline

    async def fake_fetch_all(*a, **kw):
        return {"rss": RuntimeError("boom"),
                "polymarket": RuntimeError("boom2")}

    fake_send = AsyncMock(return_value=[1])
    with patch.object(pipeline, "_fetch_all", side_effect=fake_fetch_all
    ), patch.object(pipeline, "send_digest", new=fake_send):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "error"
    fake_send.assert_awaited_once()
    sent_text = fake_send.await_args.args[0]
    assert "all sources failed" in sent_text.lower()
    assert "crypto_general" in sent_text


def test_score_and_dedup_removes_near_duplicates():
    from aggregator import pipeline
    from datetime import datetime, timezone

    items = [
        Item(id="rss:1", source="rss",
             title="Bitcoin hits new all-time high above $150,000 amid ETF inflows",
             url="https://cointelegraph.com/1",
             text="Bitcoin reached a new all-time high above one hundred fifty thousand dollars today, driven by spot ETF inflows.",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"score": 1000}, metadata={}),
        Item(id="rss:2", source="rss",
             title="Bitcoin hits new all-time high above $150,000 driven by ETF flows",
             url="https://cointelegraph.com/2",
             text="Bitcoin reached a new all-time high above one hundred fifty thousand dollars today, driven by ETF inflows from institutional buyers.",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"score": 500}, metadata={}),
        Item(id="rss:3", source="rss",
             title="Polymarket trader profile published in WSJ",
             url="https://cointelegraph.com/3",
             text="A wealthy crypto trader on Polymarket was profiled in the Wall Street Journal yesterday.",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"score": 300}, metadata={}),
    ]
    out = pipeline._score_and_dedup(items, top_n=10, per_author_cap=3)
    # The two near-duplicate Bitcoin items should collapse; only one survives.
    bitcoin_ids = [it.id for it in out if "bitcoin" in it.title.lower()]
    assert len(bitcoin_ids) == 1
    # The unrelated polymarket item should still be present.
    assert any(it.id == "rss:3" for it in out)


def test_score_and_dedup_handles_empty():
    from aggregator import pipeline
    assert pipeline._score_and_dedup([], top_n=10, per_author_cap=3) == []


def test_cap_per_symbol_enforces_per_ticker_limit():
    from aggregator import pipeline
    now = datetime.now(timezone.utc)
    def _mk(title, id):
        return Item(id=id, source="rss", title=title, url=f"https://x/{id}",
                    text="", created_at=now, engagement_raw={}, metadata={})
    items = [_mk(f"SOL news {i}", f"a{i}") for i in range(30)] + \
            [_mk("AVAX rises", "b1")]
    alias_map = {"sol": "SOL", "sui": "SUI", "avax": "AVAX"}
    out = pipeline._cap_per_symbol(items, ["SOL", "SUI", "AVAX"], alias_map, 5)
    sol = [it for it in out if "SOL" in it.title]
    avax = [it for it in out if "AVAX" in it.title]
    assert len(sol) == 5
    assert len(avax) == 1


def test_dedupe_keeps_higher_engagement_variant():
    """When two near-duplicates exist, the higher-engagement one wins the slot."""
    from aggregator import pipeline
    items = [
        Item(id="a", source="rss", title="Bitcoin closed above 200k",
             url="https://a", text="", created_at=datetime.now(timezone.utc),
             engagement_raw={"score": 5},
             metadata={}),
        Item(id="b", source="rss", title="Bitcoin closed above 200k!",
             url="https://b", text="", created_at=datetime.now(timezone.utc),
             engagement_raw={"score": 5000},
             metadata={}),
    ]
    out = pipeline._score_and_dedup(items, top_n=10, per_author_cap=0)
    assert len(out) == 1
    assert out[0].id == "b"


# Truly different headlines/bodies (no overlap) to bypass Jaccard near-dup
# collapsing in pipeline._score_and_dedup. Indexed by an integer key 0..N-1.
_DISTINCT_FIXTURES = [
    ("Bitcoin breaks $200K barrier amid massive institutional ETF inflows",
     "Spot bitcoin ETFs absorbed record demand from pension funds this morning"),
    ("Solana network suffers eight-hour outage disrupting DeFi protocols",
     "Validators report consensus failure starting at 03:00 UTC affecting Jupiter"),
    ("Vitalik proposes EIP-7777 for verkle tree gas optimization rollups",
     "Long-term scaling roadmap update from Ethereum cofounder published today"),
    ("Cardano launches Hydra layer-2 mainnet beta with throughput claims",
     "IOG releases the production candidate after eighteen months of testnet trials"),
    ("Polygon rebrands MATIC to POL and finalizes 1-to-1 token migration",
     "Holders have ninety days to swap before deprecation of legacy contracts"),
]


def make_distinct_item(source: str, idx: int) -> Item:
    """Item with content distinct enough to survive Jaccard dedup."""
    title, body = _DISTINCT_FIXTURES[idx % len(_DISTINCT_FIXTURES)]
    return Item(
        id=f"{source}:{idx}",
        source=source,
        title=title,
        url=f"https://example.com/{source}/{idx}",
        text=body,
        created_at=datetime.now(timezone.utc),
        engagement_raw={"upvotes": 1000 - idx * 10},
        metadata={},
    )


@pytest.mark.asyncio
async def test_run_digest_filters_previously_delivered(cfg, storage):
    """After one digest delivers item X, the next digest must not include X."""
    from unittest.mock import AsyncMock, patch
    from aggregator import pipeline

    first_items = [make_distinct_item("rss", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": first_items, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="DIGEST1"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        result1 = await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")
    assert result1.status == "ok"
    assert result1.items_delivered == 3

    # Second run: same 3 rss items plus 2 truly fresh ones (indices 3, 4).
    repeat = [make_distinct_item("rss", i) for i in range(3)]
    fresh = [make_distinct_item("polymarket", i) for i in range(3, 5)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": repeat, "polymarket": fresh}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="DIGEST2"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[2])):
        result2 = await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")

    assert result2.status == "ok"
    assert result2.items_delivered == 2


@pytest.mark.asyncio
async def test_run_digest_does_not_record_when_telegram_fails(cfg, storage):
    """If Telegram returns no message_ids, items should NOT be recorded as delivered."""
    from unittest.mock import AsyncMock, patch
    from aggregator import pipeline

    items = [make_distinct_item("rss", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": items, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="DIGEST"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[])):
        await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")

    items2 = [make_distinct_item("rss", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": items2, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="D2"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        result = await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")
    assert result.items_delivered == 3


@pytest.mark.asyncio
async def test_run_digest_empty_after_filter_sends_heartbeat(cfg, storage):
    """When all items get filtered out, we send a brief heartbeat instead of
    calling the LLM with zero items."""
    from unittest.mock import AsyncMock, patch
    from aggregator import pipeline

    items = [make_distinct_item("rss", i) for i in range(3)]

    # First run delivers all 3.
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": items, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="D1"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        r1 = await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")
    assert r1.items_delivered == 3

    # Second run with same items: should hit the empty-result path.
    same_items = [make_distinct_item("rss", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": same_items, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="SHOULD NOT BE CALLED"
    )) as fake_synth, patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[42])) as fake_send:
        r2 = await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")

    # Synth must NOT be called (saves a costly LLM round-trip).
    fake_synth.assert_not_called()
    # A heartbeat message must be sent.
    fake_send.assert_awaited_once()
    sent_text = fake_send.await_args.args[0]
    assert "no new items" in sent_text.lower()
    assert "crypto_general" in sent_text
    # Run is marked ok with 0 delivered.
    assert r2.status == "ok"
    assert r2.items_delivered == 0


@pytest.mark.asyncio
async def test_empty_digest_with_failed_send_records_error(cfg, storage):
    """A Telegram outage during an otherwise empty digest reports error."""
    from aggregator import pipeline

    # First, deliver some items so they end up in delivered_findings.
    items = [make_distinct_item("rss", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": items, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="D1"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")

    # Second run: same items get filtered to empty, send_digest fails.
    same_items = [make_distinct_item("rss", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": same_items, "polymarket": []}
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[])):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "error"


@pytest.mark.asyncio
async def test_heartbeat_html_escapes_topic_id(cfg, storage):
    """The heartbeat path html-escapes topic_id so '<weird>' becomes safe."""
    from aggregator import pipeline
    from aggregator.config import TopicConfig

    weird_id = "<weird>"
    cfg.topics[weird_id] = TopicConfig(
        kind="general",
        sources=["rss"],
        rss_feeds=["https://example.com/rss"],
        polymarket_tags=[],
        prompt_template="general_crypto.md",
        top_n=5,
        schedule="0 8 * * *",
    )

    # First run delivers an item so the second run sees it as "previously delivered".
    item = make_distinct_item("rss", 0)
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": [item]}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="D1"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        await pipeline.run_digest(weird_id, cfg, storage, trigger="scheduled")

    # Second run with same item -> empty after filter -> heartbeat path.
    fake_send = AsyncMock(return_value=[42])
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"rss": [make_distinct_item("rss", 0)]}
    )), patch.object(pipeline, "send_digest", new=fake_send):
        await pipeline.run_digest(weird_id, cfg, storage, trigger="scheduled")

    fake_send.assert_awaited_once()
    sent = fake_send.await_args.args[0]
    assert "&lt;weird&gt;" in sent
    assert "<weird>" not in sent


def test_per_author_cap_limits_per_author():
    from aggregator import pipeline
    # 5 items from same author, 2 from another, 1 with no author.
    items = []
    for i in range(5):
        items.append(Item(
            id=f"r:hot_{i}", source="rss",
            title=f"Hot story {i}", url=f"https://cointelegraph.com/hot_{i}",
            text="x", created_at=datetime.now(timezone.utc),
            engagement_raw={"upvotes": 1000 - i}, metadata={"author": "alice"},
        ))
    for i in range(2):
        items.append(Item(
            id=f"r:bob_{i}", source="rss",
            title=f"Bob story {i}", url=f"https://cointelegraph.com/bob_{i}",
            text="x", created_at=datetime.now(timezone.utc),
            engagement_raw={"upvotes": 500}, metadata={"author": "bob"},
        ))
    items.append(Item(
        id="pm:1", source="polymarket",
        title="Market", url="https://polymarket.com/event/x",
        text="x", created_at=datetime.now(timezone.utc),
        engagement_raw={"volume": 10000}, metadata={},
    ))

    out = pipeline._apply_per_author_cap(items, cap=2)
    by_author = {}
    for it in out:
        a = it.metadata.get("author") or "_no_author"
        by_author[a] = by_author.get(a, 0) + 1
    assert by_author["alice"] == 2  # capped from 5
    assert by_author["bob"] == 2    # under cap, kept both
    assert by_author["_no_author"] == 1  # polymarket uncapped


def test_per_author_cap_disabled_when_zero():
    from aggregator import pipeline
    items = [
        Item(id=f"r:{i}", source="rss", title=f"t{i}",
             url=f"u/{i}", text="x",
             created_at=datetime.now(timezone.utc),
             engagement_raw={}, metadata={"author": "alice"})
        for i in range(10)
    ]
    assert len(pipeline._apply_per_author_cap(items, cap=0)) == 10
    assert len(pipeline._apply_per_author_cap(items, cap=-1)) == 10


def test_per_author_cap_preserves_engagement_order():
    """Higher-engagement items within an author's quota win."""
    from aggregator import pipeline
    # Already sorted by engagement (highest first) — that's the contract.
    items = [
        Item(id="r:hi", source="rss", title="hi", url="u/hi", text="x",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"upvotes": 999}, metadata={"author": "alice"}),
        Item(id="r:mid", source="rss", title="mid", url="u/mid", text="x",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"upvotes": 500}, metadata={"author": "alice"}),
        Item(id="r:lo", source="rss", title="lo", url="u/lo", text="x",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"upvotes": 100}, metadata={"author": "alice"}),
    ]
    out = pipeline._apply_per_author_cap(items, cap=2)
    kept_ids = [it.id for it in out]
    assert kept_ids == ["r:hi", "r:mid"]  # lowest dropped


def _wl_item(source, title, *, tag=None):
    from datetime import datetime, timezone
    from aggregator.sources.base import Item
    return Item(id=f"{source}:{title}", source=source, title=title, url="u", text="",
                created_at=datetime(2026, 5, 28, tzinfo=timezone.utc),
                engagement_raw={"score": 5},
                metadata=({"watchlist_symbol": tag} if tag else {}))


def test_cap_per_symbol_metadata_tag_overrides_conflicting_alias():
    from aggregator.pipeline import _cap_per_symbol
    # `tagged`: title text matches the AVAX alias, but metadata tags it SOL.
    # `untagged`: title text matches the AVAX alias, no metadata tag.
    tagged = _wl_item("rss", "Avalanche announces partnership", tag="SOL")
    untagged = _wl_item("hackernews", "Avalanche subnet launches")
    alias_map = {"sol": "SOL", "solana": "SOL", "avax": "AVAX", "avalanche": "AVAX"}
    # Input order: untagged first. If metadata wins, tagged -> SOL bucket, untagged -> AVAX
    # bucket, and output (ordered by canonical [SOL, AVAX]) is [tagged, untagged].
    # If metadata were IGNORED, both -> AVAX bucket and output would be input order
    # [untagged, tagged]. So this assertion distinguishes the two.
    out = _cap_per_symbol([untagged, tagged], ["SOL", "AVAX"], alias_map, per_symbol_top_n=5)
    # Item is frozen + dataclasses.replace produces new instances on stamp;
    # compare by id (the contract) plus the stamped bucket on the unmatched
    # one (the assignment under test).
    assert [it.id for it in out] == [tagged.id, untagged.id]
    assert out[1].metadata["watchlist_symbol"] == "AVAX"


def test_cap_per_symbol_matches_alias_text():
    from aggregator.pipeline import _cap_per_symbol
    it = _wl_item("hackernews", "Avalanche subnet launches")  # alias, no AVAX ticker
    alias_map = {"avax": "AVAX", "avalanche": "AVAX"}
    out = _cap_per_symbol([it], ["AVAX"], alias_map, 5)
    assert [it_out.id for it_out in out] == [it.id]
    # Alias-match path stamps the canonical bucket.
    assert out[0].metadata["watchlist_symbol"] == "AVAX"


def test_cap_per_symbol_stamps_canonical_symbol():
    # Items bucketed by alias text-match (no incoming watchlist_symbol) must be
    # stamped with their canonical bucket so the synth prompt can trust it.
    from aggregator.pipeline import _cap_per_symbol
    untagged = _wl_item("rss", "Avalanche subnet launches")  # AVAX via alias
    tagged = _wl_item("rss", "Opaque title", tag="SOL")      # SOL via feed tag
    alias_map = {"sol": "SOL", "avax": "AVAX", "avalanche": "AVAX"}
    out = _cap_per_symbol([untagged, tagged], ["SOL", "AVAX"], alias_map, 5)
    stamped = {it.id: it.metadata["watchlist_symbol"] for it in out}
    assert stamped[untagged.id] == "AVAX"
    assert stamped[tagged.id] == "SOL"


# ---------------------------------------------------------------------------
# End-to-end: run_digest for crypto_watchlist exercises rss_symbol_feeds wiring
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_digest_watchlist_rss_symbol_feeds(cfg, storage):
    """run_digest for crypto_watchlist must request the configured
    per-coin tag-feed URLs (rss_symbol_feeds) AND per-symbol search-feed URLs
    (rss_search_feeds) over HTTP, and deliver tagged items.

    Failure mode caught: if either key were misspelled in pipeline.run_digest,
    RssSource.fetch would see empty inputs, no HTTP request would be made for
    those URLs, and this test would fail on the requested_urls checks AND on
    items_delivered == 0.
    """
    import time
    from unittest.mock import AsyncMock, MagicMock, patch
    import httpx
    import respx
    from aggregator import pipeline

    # The SOL search feed (URL taken from config so the assertion below
    # proves the rss_search_feeds wiring, not a hardcode).
    _sol_search = cfg.topics["crypto_watchlist"].watch[0].search_feeds[0]

    # Real RSS XML for each tag feed. feedparser requires valid XML; the
    # exact titles/links/ids are arbitrary — what matters is the URL
    # routing and that the entries are tagged correctly downstream.
    def _rss_xml(item_id: str, title: str, link: str) -> bytes:
        # Build a tiny RSS 2.0 document with a single item. feedparser is
        # permissive — minimal valid XML with the right tags is enough.
        return (
            b"<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
            b"<rss version=\"2.0\"><channel>"
            b"<title>Test</title><link>https://t/</link>"
            b"<item>"
            b"<id>" + item_id.encode() + b"</id>"
            b"<title>" + title.encode() + b"</title>"
            b"<link>" + link.encode() + b"</link>"
            b"<description>desc " + item_id.encode() + b"</description>"
            b"<pubDate>Thu, 28 May 2026 18:00:00 +0000</pubDate>"
            b"</item></channel></rss>"
        )

    # URL -> response body for the *canned* feeds. Other URLs (the broad
    # rss_feeds list, plus the SUI/AVAX/ENA search feeds) get empty bodies
    # via the catch-all route.
    _FEED_BODIES: dict[str, bytes] = {
        "https://cointelegraph.com/rss/tag/solana":
            _rss_xml("sol-1", "Solana upgrade ships", "https://ct.com/sol-1"),
        "https://cointelegraph.com/rss/tag/sui":
            _rss_xml("sui-1", "Sui DeFi milestone", "https://ct.com/sui-1"),
        "https://cointelegraph.com/rss/tag/avalanche":
            _rss_xml("avax-1", "Avalanche subnet expansion", "https://ct.com/avax-1"),
        "https://cryptoslate.com/news/ethena/feed/":
            _rss_xml("ena-1", "Ethena USDe supply hits record", "https://cs.com/ena-1"),
        _sol_search:
            _rss_xml("sol-gn-1", "Solana ETF filing reported", "https://gn.com/sol-gn-1"),
    }

    # Stub out polymarket and hackernews so only RssSource runs for real.
    stub_source = MagicMock()
    stub_source.fetch = AsyncMock(return_value=[])

    with respx.mock(assert_all_called=False) as router:
        # Canned feeds return real (small) RSS XML; everything else gets
        # an empty 200 — equivalent to "fetched, no entries".
        for url, body in _FEED_BODIES.items():
            router.get(url).mock(return_value=httpx.Response(200, content=body))
        router.route().mock(return_value=httpx.Response(200, content=b"<rss/>"))
        with patch.dict(pipeline.SOURCES, {"polymarket": stub_source, "hackernews": stub_source}), \
             patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="WATCHLIST DIGEST")), \
             patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[42])):
            result = await pipeline.run_digest("crypto_watchlist", cfg, storage,
                                                trigger="scheduled")
        requested_urls = {str(call.request.url) for call in router.calls}

    # Run must succeed.
    assert result.status == "ok"

    # The configured per-coin tag-feed URLs and the SOL search-feed URL
    # must have been requested. This verifies the rss_symbol_feeds and
    # rss_search_feeds keys are spelled correctly in pipeline.run_digest
    # and reach RssSource.fetch.
    assert "https://cointelegraph.com/rss/tag/solana" in requested_urls, (
        "SOL tag feed was not requested — rss_symbol_feeds wiring likely broken"
    )
    assert "https://cryptoslate.com/news/ethena/feed/" in requested_urls, (
        "ENA tag feed was not requested — rss_symbol_feeds wiring likely broken"
    )
    assert _sol_search in requested_urls, (
        "SOL search feed was not requested — rss_search_feeds wiring likely broken"
    )

    # Items from the per-coin feeds must have flowed through to delivery.
    assert result.items_delivered >= 2, (
        f"Expected at least SOL+ENA items delivered, got {result.items_delivered}"
    )
