"""URL canonicalization for cross-day delivery dedup.

Two URLs that point at the same resource (e.g. trailing slash vs not,
old.reddit.com vs www.reddit.com, with vs without utm_* tracking params)
should compare equal so the same item isn't re-delivered.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DROP_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref_")
_REDDIT_HOSTS = {
    "old.reddit.com",
    "www.reddit.com",
    "reddit.com",
    "i.reddit.com",
    "m.reddit.com",
}


def canonicalize(url: str) -> str:
    """Return a canonical form of `url` suitable for equality comparison.

    Drops fragments, normalizes host case + reddit subdomains, strips tracking
    query params, lowercases scheme, and collapses trailing slashes on the path.
    Path case is preserved.
    """
    if not url:
        return url
    sp = urlsplit(url)
    scheme = sp.scheme.lower()
    host = sp.hostname.lower() if sp.hostname else ""
    if host in _REDDIT_HOSTS:
        host = "www.reddit.com"
    netloc = host + (f":{sp.port}" if sp.port else "")
    path = sp.path.rstrip("/") or "/"
    keep = [
        (k, v)
        for k, v in parse_qsl(sp.query, keep_blank_values=False)
        if not any(k.lower().startswith(p) for p in _DROP_PARAM_PREFIXES)
    ]
    query = urlencode(keep)
    return urlunsplit((scheme, netloc, path, query, ""))


def dedup_key(item: dict) -> str | None:
    """Return a stable dedup key for `item` — canonical URL when available,
    otherwise ``id:<source-id>`` so url-less items (e.g. some Polymarket
    markets) don't bypass cross-run dedup.

    Returns None when the item has neither url nor id.
    """
    url = (item.get("url") or "").strip()
    if url:
        return canonicalize(url)
    item_id = (item.get("id") or "").strip()
    if item_id:
        return f"id:{item_id}"
    return None
