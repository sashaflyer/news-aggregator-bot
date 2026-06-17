from datetime import datetime, timezone

from aggregator.ranking import cap_per_symbol, engagement_score, score_and_dedup
from aggregator.sources.base import Item


def _mk(source: str, engagement_raw: dict | None = None, **kw) -> Item:
    defaults = dict(
        id=f"{source}:1",
        source=source,
        title="title",
        url="https://example.com/1",
        text="body",
        created_at=datetime.now(timezone.utc),
        engagement_raw=engagement_raw or {},
        metadata={},
    )
    defaults.update(kw)
    return Item(**defaults)


# ── engagement_score: hackernews ──────────────────────────────────────────────

def test_engagement_score_hackernews():
    item = _mk("hackernews", {"points": 100, "comments": 50})
    score = engagement_score(item)
    assert score > 0


def test_engagement_score_hackernews_weights():
    hi_pts = _mk("hackernews", {"points": 1000, "comments": 0}, id="hi")
    hi_cmt = _mk("hackernews", {"points": 0, "comments": 1000}, id="cmt")
    s_pts = engagement_score(hi_pts)
    s_cmt = engagement_score(hi_cmt)
    assert s_pts > s_cmt


# ── engagement_score: polymarket ──────────────────────────────────────────────

def test_engagement_score_polymarket():
    item = _mk("polymarket", {"volume": 50000, "liquidity": 10000})
    score = engagement_score(item)
    assert score > 0


def test_engagement_score_polymarket_volume_dominates():
    hi_vol = _mk("polymarket", {"volume": 100000, "liquidity": 0}, id="v")
    hi_liq = _mk("polymarket", {"volume": 0, "liquidity": 100000}, id="l")
    s_vol = engagement_score(hi_vol)
    s_liq = engagement_score(hi_liq)
    assert s_vol > s_liq


# ── engagement_score: github ─────────────────────────────────────────────────

def test_engagement_score_github():
    item = _mk("github", {"reactions": 200, "comments": 30})
    score = engagement_score(item)
    assert score > 0


def test_engagement_score_github_reactions_dominates():
    hi_rxn = _mk("github", {"reactions": 1000, "comments": 0}, id="r")
    hi_cmt = _mk("github", {"reactions": 0, "comments": 1000}, id="c")
    s_rxn = engagement_score(hi_rxn)
    s_cmt = engagement_score(hi_cmt)
    assert s_rxn > s_cmt


# ── engagement_score: rss fallback ────────────────────────────────────────────

def test_engagement_score_rss_fallback():
    item = _mk("rss", {"upvotes": 50, "score": 200, "comments": 10})
    score = engagement_score(item)
    assert score > 0


def test_engagement_score_rss_uses_global_weights():
    from aggregator.config import ScoringConfig
    scoring = ScoringConfig(
        dedup_window_days=7, per_author_cap=3,
        weight_upvotes=1.0, weight_score=1.0,
        weight_comments=0.1, weight_volume=0.001,
    )
    item = _mk("rss", {"upvotes": 100})
    s_default = engagement_score(item)
    s_custom = engagement_score(item, scoring=scoring)
    assert s_default > 0
    assert s_custom > 0


# ── engagement_score: None / missing values ───────────────────────────────────

def test_engagement_score_empty_raw():
    item = _mk("hackernews", {})
    assert engagement_score(item) >= 0


def test_engagement_score_none_values():
    item = _mk("hackernews", {"points": None, "comments": None})
    assert engagement_score(item) >= 0


def test_engagement_score_mixed_none_and_real():
    item = _mk("polymarket", {"volume": 1000, "liquidity": None})
    score = engagement_score(item)
    assert score > 0


# ── cap_per_symbol ────────────────────────────────────────────────────────────

def test_cap_per_symbol_basic():
    now = datetime.now(timezone.utc)
    items = [
        Item(id=f"a{i}", source="rss", title=f"SOL news {i}", url=f"https://x/{i}",
             text="", created_at=now, engagement_raw={}, metadata={})
        for i in range(10)
    ]
    alias_map = {"sol": "SOL"}
    out = cap_per_symbol(items, ["SOL"], alias_map, per_symbol_top_n=3)
    assert len(out) == 3


def test_cap_per_symbol_buckets_by_ticker():
    now = datetime.now(timezone.utc)
    sol = [Item(id=f"s{i}", source="rss", title=f"SOL item {i}", url=f"https://s/{i}",
                text="", created_at=now, engagement_raw={}, metadata={}) for i in range(5)]
    avax = [Item(id=f"a{i}", source="rss", title=f"AVAX item {i}", url=f"https://a/{i}",
                 text="", created_at=now, engagement_raw={}, metadata={}) for i in range(3)]
    alias_map = {"sol": "SOL", "avax": "AVAX"}
    out = cap_per_symbol(sol + avax, ["SOL", "AVAX"], alias_map, per_symbol_top_n=5)
    sol_out = [it for it in out if "SOL" in it.title]
    avax_out = [it for it in out if "AVAX" in it.title]
    assert len(sol_out) == 5
    assert len(avax_out) == 3


def test_cap_per_symbol_drops_unmatched():
    now = datetime.now(timezone.utc)
    matched = Item(id="m", source="rss", title="SOL news", url="https://m",
                   text="", created_at=now, engagement_raw={}, metadata={})
    unmatched = Item(id="u", source="rss", title="Random news", url="https://u",
                     text="", created_at=now, engagement_raw={}, metadata={})
    alias_map = {"sol": "SOL"}
    out = cap_per_symbol([matched, unmatched], ["SOL"], alias_map, per_symbol_top_n=5)
    assert len(out) == 1
    assert out[0].id == "m"


def test_cap_per_symbol_respects_symbol_order():
    now = datetime.now(timezone.utc)
    avax = Item(id="a", source="rss", title="AVAX news", url="https://a",
                text="", created_at=now, engagement_raw={}, metadata={})
    sol = Item(id="s", source="rss", title="SOL news", url="https://s",
               text="", created_at=now, engagement_raw={}, metadata={})
    alias_map = {"sol": "SOL", "avax": "AVAX"}
    out = cap_per_symbol([avax, sol], ["SOL", "AVAX"], alias_map, per_symbol_top_n=5)
    assert out[0].id == "s"
    assert out[1].id == "a"


# ── score_and_dedup ───────────────────────────────────────────────────────────

def test_score_and_dedup_returns_sorted_by_engagement():
    now = datetime.now(timezone.utc)
    lo = Item(id="lo", source="hackernews", title="low",
              url="https://lo", text="x", created_at=now,
              engagement_raw={"points": 1, "comments": 0}, metadata={})
    hi = Item(id="hi", source="hackernews", title="high",
              url="https://hi", text="y", created_at=now,
              engagement_raw={"points": 1000, "comments": 500}, metadata={})
    out = score_and_dedup([lo, hi], top_n=10, per_author_cap=0)
    assert len(out) >= 1
    assert out[0].id == "hi"


def test_score_and_dedup_truncates_to_top_n():
    now = datetime.now(timezone.utc)
    items = [
        Item(id=f"i{i}", source="rss", title=f"item {i} unique topic xyz",
             url=f"https://x/{i}", text=f"body {i} unique content",
             created_at=now, engagement_raw={"score": 100 - i}, metadata={})
        for i in range(20)
    ]
    out = score_and_dedup(items, top_n=5, per_author_cap=0)
    assert len(out) <= 5


def test_score_and_dedup_empty_input():
    assert score_and_dedup([], top_n=10, per_author_cap=0) == []
