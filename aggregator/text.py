"""Text chunking utilities for Telegram message splitting."""
from __future__ import annotations

import re

from aggregator.delivery._html_filter import ALLOWED_TAGS, HTML_ENTITY_RE

_TG_HARD_LIMIT_UTF16 = 4096
_SUFFIX_RESERVE = 32


def utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def find_safe_cut(text: str, hard_cut: int) -> int:
    """Return a cut position in (0, hard_cut] that doesn't split an HTML entity
    or an unclosed tag."""
    if hard_cut >= len(text):
        return len(text)
    if hard_cut <= 0:
        return hard_cut

    for end in range(hard_cut, max(hard_cut - 32, 0), -1):
        if text[end - 1] != ";":
            continue
        amp = text.rfind("&", 0, end)
        if amp == -1:
            continue
        if HTML_ENTITY_RE.fullmatch(text[amp:end]):
            continue
        return end

    best: int | None = None
    for tag in ALLOWED_TAGS:
        close = text.rfind(f"</{tag}>", 0, hard_cut)
        if close == -1:
            continue
        candidate = close + len(f"</{tag}>")
        if best is None or candidate > best:
            best = candidate
    if best is not None and best > 0:
        return best

    for sep in ("\n\n", "\n", ". "):
        idx = text.rfind(sep, 0, hard_cut)
        if idx > 0:
            return idx + len(sep)
    return hard_cut


def chunk_text(text: str, limit: int = _TG_HARD_LIMIT_UTF16 - _SUFFIX_RESERVE) -> list[str]:
    """Split `text` into chunks that each fit `limit` UTF-16 code units."""
    if utf16_len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while utf16_len(remaining) > limit:
        lo, hi = 0, len(remaining)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if utf16_len(remaining[:mid]) <= limit:
                lo = mid
            else:
                hi = mid - 1
        cut = find_safe_cut(remaining, lo)
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
