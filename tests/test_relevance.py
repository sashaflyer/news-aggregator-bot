"""Tests for the crypto-watchlist relevance filter.

The noise examples are real titles pulled from production run 12, where the
LLM correctly returned 'no notable activity' for every symbol because Reddit's
global symbol search had filled the input with hockey teams, video games, and
perfume brands.
"""
from datetime import datetime, timezone

from aggregator.relevance import filter_crypto_watchlist_items, is_crypto_related
from aggregator.sources.base import Item


def _it(source, title, text=""):
    return Item(id=f"{source}:{title}", source=source, title=title, url="u", text=text,
                created_at=datetime(2026, 5, 28, tzinfo=timezone.utc))


def _hn(title: str, *, text: str = "") -> Item:
    return _it("hackernews", title, text)


def _polymarket(title: str) -> Item:
    return _it("polymarket", title)


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

def test_rss_and_polymarket_pass_without_keyword():
    from aggregator.relevance import filter_crypto_watchlist_items
    rss = _it("rss", "Sui Foundation announces grants program")
    poly = _it("polymarket", "Will X happen by 2026?")
    out = filter_crypto_watchlist_items([rss, poly])
    assert rss in out and poly in out


def test_hn_item_still_gated_by_keyword():
    from aggregator.relevance import filter_crypto_watchlist_items
    on = _it("hackernews", "New DeFi protocol launches on Solana")  # has 'defi'
    off = _it("hackernews", "Avalanche ski resort opens early")     # no crypto context
    out = filter_crypto_watchlist_items([on, off])
    assert on in out and off not in out
