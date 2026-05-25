"""Tests for the crypto-watchlist relevance filter.

The noise examples are real titles pulled from production run 12, where the
LLM correctly returned 'no notable activity' for every symbol because Reddit's
global symbol search had filled the input with hockey teams, video games, and
perfume brands.
"""
from datetime import datetime, timezone

from aggregator.relevance import filter_crypto_watchlist_items, is_crypto_related
from aggregator.sources.base import Item


def _reddit(title: str, *, subreddit: str = "", text: str = "") -> Item:
    return Item(
        id=f"reddit:{title[:30]}",
        source="reddit",
        title=title,
        url=f"https://reddit.com/{title[:30]}",
        text=text,
        created_at=datetime.now(timezone.utc),
        metadata={"subreddit": subreddit},
    )


def _polymarket(title: str) -> Item:
    return Item(
        id=f"polymarket:{title[:30]}",
        source="polymarket",
        title=title,
        url=f"https://polymarket.com/{title[:30]}",
        text="",
        created_at=datetime.now(timezone.utc),
    )


def _hn(title: str, *, text: str = "") -> Item:
    return Item(
        id=f"hn:{title[:30]}",
        source="hackernews",
        title=title,
        url=f"https://news.ycombinator.com/{title[:30]}",
        text=text,
        created_at=datetime.now(timezone.utc),
    )


# ---- is_crypto_related ----

def test_is_crypto_related_finds_single_word():
    assert is_crypto_related("New stablecoin issuer announced")
    assert is_crypto_related("Validator slashing event on Solana")
    assert is_crypto_related("ETH staking yields hit 6%")


def test_is_crypto_related_finds_bigram():
    assert is_crypto_related("Spot ETF inflows turned net positive")
    assert is_crypto_related("Audit found a smart contract bug")


def test_is_crypto_related_case_insensitive():
    assert is_crypto_related("BITCOIN ATH")
    assert is_crypto_related("BlockChain analytics firm")


def test_is_crypto_related_checks_multiple_texts():
    assert is_crypto_related("Generic title", "body mentions defi")
    assert not is_crypto_related("Generic title", "body about hockey")


def test_is_crypto_related_rejects_noise():
    # Real titles from production run 12.
    assert not is_crypto_related("The Minnesota Wild will face the Colorado Avalanche")
    assert not is_crypto_related("POV you get caught in an avalanche in Kyrgyzstan")
    assert not is_crypto_related("Praise Sol and Lua")  # Warframe
    assert not is_crypto_related("66,000ly from sol!")  # Elite Dangerous
    assert not is_crypto_related("Les gens hors sol quand il s'agit d'argent")
    assert not is_crypto_related("Playstation Plus Monthly Games for May")
    assert not is_crypto_related("I redesigned the flags of 14 Bay Area cities!")


def test_btc_token_does_not_mean_blockchain():
    # 'token' is in our keywords; OAuth token should still pass because we
    # don't try to disambiguate. Document the limit explicitly.
    assert is_crypto_related("OAuth token rotation failed")


# ---- filter_crypto_watchlist_items ----

def test_polymarket_items_always_pass():
    items = [_polymarket("Will Trump pardon someone obscure?")]
    out = filter_crypto_watchlist_items(items, trusted_subreddits=["solana"])
    assert out == items


def test_reddit_from_trusted_subreddit_passes_even_without_keyword():
    # Real example: "Buy Sol now or regret later" in r/solana — no crypto
    # keyword in title, but the user trusts r/solana via topic config.
    items = [_reddit("Buy Sol now or regret later", subreddit="solana")]
    out = filter_crypto_watchlist_items(items, trusted_subreddits=["solana", "cryptocurrency"])
    assert out == items


def test_trusted_subreddit_matching_strips_r_prefix():
    items = [_reddit("Anything", subreddit="r/Solana")]
    out = filter_crypto_watchlist_items(items, trusted_subreddits=["solana"])
    assert out == items


def test_reddit_off_topic_subreddit_without_keyword_dropped():
    items = [
        _reddit("Colorado Avalanche win Game 7", subreddit="hockey"),
        _reddit("Nine Sols speedrun world record", subreddit="NineSols"),
        _reddit("Sol de Janeiro perfume review", subreddit="FemFragLab"),
    ]
    out = filter_crypto_watchlist_items(items, trusted_subreddits=["solana", "cryptocurrency"])
    assert out == []


def test_reddit_off_topic_subreddit_with_keyword_passes():
    items = [
        _reddit("Coinbase listed SOL futures today", subreddit="WallStreetBets"),
    ]
    out = filter_crypto_watchlist_items(items, trusted_subreddits=["solana"])
    assert out == items


def test_hn_filtered_by_keyword():
    on = _hn("Solana validator client v2 released", text="blockchain perf upgrade")
    off = _hn("Nine Sols hits 1M sales")
    out = filter_crypto_watchlist_items([on, off], trusted_subreddits=[])
    assert out == [on]


def test_full_production_noise_mix():
    items = [
        # On-topic via keyword
        _reddit("Stablecoin issuer files for IPO", subreddit="WallStreetBets"),
        # On-topic via trusted subreddit (no keyword)
        _reddit("Buy Sol now or regret later", subreddit="solana"),
        # On-topic via Polymarket pass-through
        _polymarket("BTC above 120K by July 1"),
        # Pure noise
        _reddit("Colorado Avalanche advance to WCF", subreddit="hockey"),
        _reddit("Praise Sol and Lua", subreddit="Warframe"),
        _reddit("Playstation Plus Monthly Games for May", subreddit="PS5"),
        _hn("Build your own Sol(ar) system in Python", text=""),
    ]
    out = filter_crypto_watchlist_items(items, trusted_subreddits=["solana", "cryptocurrency"])
    titles = [i.title for i in out]
    assert "Stablecoin issuer files for IPO" in titles
    assert "Buy Sol now or regret later" in titles
    assert "BTC above 120K by July 1" in titles
    assert "Colorado Avalanche advance to WCF" not in titles
    assert "Praise Sol and Lua" not in titles
    assert "Playstation Plus Monthly Games for May" not in titles
    assert len(out) == 3


def test_missing_subreddit_metadata_falls_through_to_keyword_check():
    on = _reddit("DeFi protocol got hacked", subreddit="")
    off = _reddit("Hockey postgame thread", subreddit="")
    out = filter_crypto_watchlist_items([on, off], trusted_subreddits=["solana"])
    assert out == [on]
