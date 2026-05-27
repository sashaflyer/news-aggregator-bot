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

    reddit_items = [make_item("reddit", i) for i in range(5)]
    poly_items = [make_item("polymarket", i) for i in range(3)]

    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": reddit_items, "polymarket": poly_items}
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
        return {"reddit": [make_item("reddit", 0)],
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
        return {"reddit": RuntimeError("boom"),
                "polymarket": RuntimeError("boom2")}

    with patch.object(pipeline, "_fetch_all", side_effect=fake_fetch_all
    ), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[])):
        result = await pipeline.run_digest("crypto_general", cfg, storage,
                                           trigger="scheduled")

    assert result.status == "error"
    assert storage.get_source_health("reddit")["consecutive_failures"] == 1
    assert storage.get_source_health("polymarket")["consecutive_failures"] == 1


@pytest.mark.asyncio
async def test_all_sources_fail_sends_failure_heartbeat(cfg, storage):
    from aggregator import pipeline

    async def fake_fetch_all(*a, **kw):
        return {"reddit": RuntimeError("boom"),
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
        Item(id="reddit:1", source="reddit",
             title="Bitcoin hits new all-time high above $150,000 amid ETF inflows",
             url="https://reddit.com/1",
             text="Bitcoin reached a new all-time high above one hundred fifty thousand dollars today, driven by spot ETF inflows.",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"upvotes": 1000}, metadata={"subreddit": "CryptoCurrency"}),
        Item(id="reddit:2", source="reddit",
             title="Bitcoin hits new all-time high above $150,000 driven by ETF flows",
             url="https://reddit.com/2",
             text="Bitcoin reached a new all-time high above one hundred fifty thousand dollars today, driven by ETF inflows from institutional buyers.",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"upvotes": 500}, metadata={"subreddit": "Bitcoin"}),
        Item(id="reddit:3", source="reddit",
             title="Polymarket trader profile published in WSJ",
             url="https://reddit.com/3",
             text="A wealthy crypto trader on Polymarket was profiled in the Wall Street Journal yesterday.",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"upvotes": 300}, metadata={"subreddit": "CryptoCurrency"}),
    ]
    out = pipeline._score_and_dedup(items, top_n=10, per_author_cap=3)
    # The two near-duplicate Bitcoin items should collapse; only one survives.
    bitcoin_ids = [it.id for it in out if "bitcoin" in it.title.lower()]
    assert len(bitcoin_ids) == 1
    # The unrelated polymarket item should still be present.
    assert any(it.id == "reddit:3" for it in out)


def test_score_and_dedup_handles_empty():
    from aggregator import pipeline
    assert pipeline._score_and_dedup([], top_n=10, per_author_cap=3) == []


def test_cap_per_symbol_enforces_per_ticker_limit():
    from aggregator import pipeline
    now = datetime.now(timezone.utc)
    def _mk(title, id):
        return Item(id=id, source="reddit", title=title, url=f"https://x/{id}",
                    text="", created_at=now, engagement_raw={}, metadata={})
    items = [_mk(f"SOL news {i}", f"a{i}") for i in range(30)] + \
            [_mk("AVAX rises", "b1")]
    out = pipeline._cap_per_symbol(items, symbols=["SOL", "SUI", "AVAX"], per_symbol_top_n=5)
    sol = [it for it in out if "SOL" in it.title]
    avax = [it for it in out if "AVAX" in it.title]
    assert len(sol) == 5
    assert len(avax) == 1


def test_dedupe_keeps_higher_engagement_variant():
    """When two near-duplicates exist, the higher-engagement one wins the slot."""
    from aggregator import pipeline
    items = [
        Item(id="a", source="reddit", title="Bitcoin closed above 200k",
             url="https://a", text="", created_at=datetime.now(timezone.utc),
             engagement_raw={"score": 5, "upvotes": 5, "comments": 0},
             metadata={}),
        Item(id="b", source="reddit", title="Bitcoin closed above 200k!",
             url="https://b", text="", created_at=datetime.now(timezone.utc),
             engagement_raw={"score": 5000, "upvotes": 5000, "comments": 0},
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
        metadata={"subreddit": "CryptoCurrency"},
    )


@pytest.mark.asyncio
async def test_run_digest_filters_previously_delivered(cfg, storage):
    """After one digest delivers item X, the next digest must not include X."""
    from unittest.mock import AsyncMock, patch
    from aggregator import pipeline

    first_items = [make_distinct_item("reddit", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": first_items, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="DIGEST1"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        result1 = await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")
    assert result1.status == "ok"
    assert result1.items_delivered == 3

    # Second run: same 3 reddit items plus 2 truly fresh ones (indices 3, 4).
    repeat = [make_distinct_item("reddit", i) for i in range(3)]
    fresh = [make_distinct_item("polymarket", i) for i in range(3, 5)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": repeat, "polymarket": fresh}
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

    items = [make_distinct_item("reddit", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": items, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="DIGEST"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[])):
        await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")

    items2 = [make_distinct_item("reddit", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": items2, "polymarket": []}
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

    items = [make_distinct_item("reddit", i) for i in range(3)]

    # First run delivers all 3.
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": items, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="D1"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        r1 = await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")
    assert r1.items_delivered == 3

    # Second run with same items: should hit the empty-result path.
    same_items = [make_distinct_item("reddit", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": same_items, "polymarket": []}
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
    items = [make_distinct_item("reddit", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": items, "polymarket": []}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="D1"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        await pipeline.run_digest("crypto_general", cfg, storage, trigger="scheduled")

    # Second run: same items get filtered to empty, send_digest fails.
    same_items = [make_distinct_item("reddit", i) for i in range(3)]
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": same_items, "polymarket": []}
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
        sources=["reddit"],
        subreddits=["test"],
        polymarket_tags=[],
        prompt_template="general_crypto.md",
        top_n=5,
        schedule="0 8 * * *",
    )

    # First run delivers an item so the second run sees it as "previously delivered".
    item = make_distinct_item("reddit", 0)
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": [item]}
    )), patch.object(pipeline, "synthesize_async", new=AsyncMock(return_value="D1"
    )), patch.object(pipeline, "send_digest", new=AsyncMock(return_value=[1])):
        await pipeline.run_digest(weird_id, cfg, storage, trigger="scheduled")

    # Second run with same item -> empty after filter -> heartbeat path.
    fake_send = AsyncMock(return_value=[42])
    with patch.object(pipeline, "_fetch_all", new=AsyncMock(
        return_value={"reddit": [make_distinct_item("reddit", 0)]}
    )), patch.object(pipeline, "send_digest", new=fake_send):
        await pipeline.run_digest(weird_id, cfg, storage, trigger="scheduled")

    fake_send.assert_awaited_once()
    sent = fake_send.await_args.args[0]
    assert "&lt;weird&gt;" in sent
    assert "<weird>" not in sent


@pytest.mark.asyncio
async def test_enrich_reddit_items_adds_comments_to_metadata():
    """Verify Reddit items get top_comments+comment_insights in metadata."""
    from unittest.mock import patch
    from aggregator import pipeline

    fake_enriched = {
        "url": "https://reddit.com/r/x/comments/abc/title/",
        "top_comments": [
            {"score": 500, "author": "alice", "excerpt": "actually this is misleading because..."},
            {"score": 200, "author": "bob", "excerpt": "agreed, source links here"},
            {"score": 50, "author": "cara", "excerpt": "fourth comment, should be dropped due to limit"},
            {"score": 10, "author": "dan", "excerpt": "fifth"},
        ],
        "comment_insights": ["sentiment: skeptical", "consensus: pump-and-dump"],
    }
    items = [
        make_distinct_item("reddit", 0),
        make_distinct_item("reddit", 1),
        make_distinct_item("polymarket", 2),  # should NOT be enriched
    ]
    with patch.object(pipeline._reddit_enrich, "enrich_reddit_item",
                      return_value=fake_enriched):
        out = await pipeline._enrich_reddit_items(items)

    # Reddit items: metadata populated, limited to top 3 comments.
    assert "top_comments" in out[0].metadata
    assert len(out[0].metadata["top_comments"]) == 3  # capped
    assert out[0].metadata["top_comments"][0]["author"] == "alice"
    assert "comment_insights" in out[0].metadata
    assert out[1].metadata.get("top_comments")
    # Polymarket item: untouched.
    assert "top_comments" not in out[2].metadata


@pytest.mark.asyncio
async def test_enrich_reddit_items_continues_on_failure():
    """A single enrichment failure shouldn't take down the run."""
    from unittest.mock import patch
    from aggregator import pipeline

    calls = []
    def flaky(item):
        calls.append(item["url"])
        if "reddit/0" in item["url"]:
            raise RuntimeError("network blip")
        return {"url": item["url"],
                "top_comments": [{"score": 1, "author": "x", "excerpt": "hi"}],
                "comment_insights": []}

    items = [make_distinct_item("reddit", 0), make_distinct_item("reddit", 1)]
    with patch.object(pipeline._reddit_enrich, "enrich_reddit_item", side_effect=flaky):
        out = await pipeline._enrich_reddit_items(items)

    # Both items were attempted; only the second got enriched.
    assert len(calls) == 2
    assert "top_comments" not in out[0].metadata
    assert out[1].metadata.get("top_comments")


@pytest.mark.asyncio
async def test_enrich_reddit_items_aborts_on_rate_limit():
    """If upstream raises a 429-bearing HTTP error, stop enriching the rest."""
    from unittest.mock import MagicMock, patch
    from aggregator import pipeline

    # Upstream reddit_enrich uses httpx; mimic the .response.status_code shape
    # without taking a hard test dep on httpx internals.
    class FakeHTTPError(Exception):
        def __init__(self, status):
            super().__init__(f"HTTP {status}")
            self.response = MagicMock(status_code=status)

    calls = []
    def fake(item):
        calls.append(item["url"])
        raise FakeHTTPError(429)

    items = [make_distinct_item("reddit", i) for i in range(5)]
    with patch.object(pipeline._reddit_enrich, "enrich_reddit_item", side_effect=fake):
        await pipeline._enrich_reddit_items(items)

    # Should bail after the first rate-limit signal.
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_enrich_reddit_items_continues_on_non_429():
    """Non-rate-limit failures should NOT abort the loop early."""
    from unittest.mock import MagicMock, patch
    from aggregator import pipeline

    class FakeHTTPError(Exception):
        def __init__(self, status):
            super().__init__(f"HTTP {status}")
            self.response = MagicMock(status_code=status)

    calls = []
    def fake(item):
        calls.append(item["url"])
        raise FakeHTTPError(500)

    items = [make_distinct_item("reddit", i) for i in range(3)]
    with patch.object(pipeline._reddit_enrich, "enrich_reddit_item", side_effect=fake):
        await pipeline._enrich_reddit_items(items)

    # Each item attempted — 500 is transient, not a rate-limit abort signal.
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_enrich_reddit_items_runs_with_bounded_concurrency():
    """Enrichment runs in parallel but caps in-flight calls at the configured
    concurrency. Verifies behavior via a thread-safe in-flight counter."""
    import threading
    import time as _time
    from unittest.mock import patch
    from aggregator import pipeline

    lock = threading.Lock()
    state = {"in_flight": 0, "peak": 0}

    def slow_enrich(item):
        with lock:
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
        _time.sleep(0.05)
        with lock:
            state["in_flight"] -= 1
        return {"url": item["url"], "top_comments": [], "comment_insights": []}

    items = [make_distinct_item("reddit", i) for i in range(6)]
    with patch.object(pipeline._reddit_enrich, "enrich_reddit_item",
                      side_effect=slow_enrich):
        await pipeline._enrich_reddit_items(items)

    assert state["peak"] >= 2  # ran some calls concurrently
    assert state["peak"] <= pipeline._REDDIT_ENRICH_CONCURRENCY


@pytest.mark.asyncio
async def test_enrich_reddit_items_respects_cap():
    """No more than _REDDIT_ENRICH_CAP items get enriched per run."""
    from unittest.mock import patch
    from aggregator import pipeline

    fake = {"url": "x", "top_comments": [], "comment_insights": []}
    items = [make_distinct_item("reddit", i) for i in range(pipeline._REDDIT_ENRICH_CAP + 5)]
    call_count = 0
    def counter(item):
        nonlocal call_count
        call_count += 1
        return fake

    with patch.object(pipeline._reddit_enrich, "enrich_reddit_item", side_effect=counter):
        await pipeline._enrich_reddit_items(items)

    assert call_count == pipeline._REDDIT_ENRICH_CAP


def test_per_author_cap_limits_per_author():
    from aggregator import pipeline
    # 5 items from same author, 2 from another, 1 with no author.
    items = []
    for i in range(5):
        items.append(Item(
            id=f"r:hot_{i}", source="reddit",
            title=f"Hot story {i}", url=f"https://reddit.com/hot_{i}",
            text="x", created_at=datetime.now(timezone.utc),
            engagement_raw={"upvotes": 1000 - i}, metadata={"author": "alice"},
        ))
    for i in range(2):
        items.append(Item(
            id=f"r:bob_{i}", source="reddit",
            title=f"Bob story {i}", url=f"https://reddit.com/bob_{i}",
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
        Item(id=f"r:{i}", source="reddit", title=f"t{i}",
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
        Item(id="r:hi", source="reddit", title="hi", url="u/hi", text="x",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"upvotes": 999}, metadata={"author": "alice"}),
        Item(id="r:mid", source="reddit", title="mid", url="u/mid", text="x",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"upvotes": 500}, metadata={"author": "alice"}),
        Item(id="r:lo", source="reddit", title="lo", url="u/lo", text="x",
             created_at=datetime.now(timezone.utc),
             engagement_raw={"upvotes": 100}, metadata={"author": "alice"}),
    ]
    out = pipeline._apply_per_author_cap(items, cap=2)
    kept_ids = [it.id for it in out]
    assert kept_ids == ["r:hi", "r:mid"]  # lowest dropped
