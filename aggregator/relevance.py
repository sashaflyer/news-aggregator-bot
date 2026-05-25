"""Topic-relevance filter to drop off-domain items that slipped past source-side search.

The watchlist Reddit/HN keyword search submits bare tickers (e.g. "SOL", "AVAX")
to global search endpoints. Those terms collide with NHL teams (Colorado
Avalanche), video games (Nine Sols, Warframe's Sol system), perfume brands
(Sol de Janeiro), and assorted noise. This module drops items that look
off-topic for cryptocurrency before they reach the ranker and the LLM.

Conservative by design: better to drop a borderline real crypto post than
to ship Stanley Cup recaps to the watchlist.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from aggregator.sources.base import Item

log = logging.getLogger(__name__)

# Single-word context terms. An item passes the keyword check if any of these
# words appear in title or body. Bigrams handled separately below.
_CRYPTO_CONTEXT_WORDS = frozenset({
    "crypto", "cryptocurrency", "blockchain", "blockchains",
    "defi", "stablecoin", "stablecoins", "tokenomics",
    "token", "tokens", "altcoin", "altcoins", "memecoin", "shitcoin",
    "bitcoin", "btc", "ethereum", "eth", "satoshi", "satoshis",
    "wallet", "wallets", "ledger",
    "dex", "cex", "amm",
    "staking", "staker", "validator", "validators", "delegator",
    "airdrop", "airdrops",
    "nft", "nfts",
    "tvl", "apy", "apr",
    "rugpull", "rugpulled",
    "binance", "coinbase", "kraken", "uniswap", "polymarket",
    "metamask", "phantom",
    "onchain", "offchain",
    "perp", "perps",
})

# Bigrams (substring match on lowercased text). Cheap fallback for terms that
# tokenize into common words individually.
_CRYPTO_CONTEXT_BIGRAMS = (
    "smart contract", "prediction market", "layer 2", "layer 1",
    "gas fee", "gas fees", "yield farm", "yield farming",
    "spot etf", "spot etfs",
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def is_crypto_related(*texts: str) -> bool:
    """Return True if any of the given texts contains a crypto-context term."""
    for raw in texts:
        if not raw:
            continue
        lower = raw.lower()
        words = set(_WORD_RE.findall(lower))
        if words & _CRYPTO_CONTEXT_WORDS:
            return True
        if any(bg in lower for bg in _CRYPTO_CONTEXT_BIGRAMS):
            return True
    return False


def _normalize_subreddit(name: str) -> str:
    """Lowercase and strip an optional ``r/`` prefix."""
    s = (name or "").strip().lower()
    if s.startswith("r/"):
        s = s[2:]
    return s


def filter_crypto_watchlist_items(
    items: Iterable[Item],
    trusted_subreddits: Iterable[str],
) -> list[Item]:
    """Drop off-topic items from a crypto-watchlist fetch.

    Pass-through rules:
    - Polymarket items always pass (they match via curated tags, not keyword search).
    - Reddit items from a user-trusted subreddit (listed in the topic config) pass.
    - Reddit/HN items pass only if title or body contains a crypto-context term.
    """
    trusted = {_normalize_subreddit(s) for s in trusted_subreddits}
    out: list[Item] = []
    for it in items:
        if it.source == "polymarket":
            out.append(it)
            continue
        if it.source == "reddit":
            sub = _normalize_subreddit(it.metadata.get("subreddit") or "")
            if sub and sub in trusted:
                out.append(it)
                continue
        if is_crypto_related(it.title, it.text):
            out.append(it)
    return out
