"""Whitelist outgoing HTML tags + link schemes for Telegram parse_mode=HTML.

LLM output may contain prompt-injected anchor tags pointing to attacker URLs.
We allow only the Telegram-supported tag set with http(s) hrefs. Disallowed
tags are stripped but their text content is preserved so legitimate copy
isn't silently lost.
"""
from __future__ import annotations

import re

ALLOWED_TAGS = frozenset({"b", "a", "i", "code", "pre"})
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>")
_HREF_RE = re.compile(r'href=(["\'])(https?://[^"\']*)\1')


def sanitize_outgoing(text: str) -> str:
    """Strip disallowed HTML tags and non-http(s) anchor hrefs from `text`.

    Allowed tags pass through unchanged. Disallowed tags are removed but their
    text content is preserved. Anchor tags with non-http(s) hrefs (e.g.
    `javascript:`) are stripped entirely (both open and close). Orphaned
    closing </a> tags (without a preceding valid open) are also removed.
    Nested anchor tags are stripped (Telegram may render them unpredictably).
    """
    a_open_depth = 0

    def _replace(m: re.Match[str]) -> str:
        nonlocal a_open_depth
        slash, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag not in ALLOWED_TAGS:
            return ""
        if tag == "a":
            if slash:
                if a_open_depth > 0:
                    a_open_depth -= 1
                    return "</a>"
                return ""
            href = _HREF_RE.search(attrs)
            if not href:
                return ""
            if a_open_depth > 0:
                return ""
            a_open_depth += 1
            return f'<a href="{href.group(2)}">'
        return f"<{slash}{tag}>"

    return _TAG_RE.sub(_replace, text)


HTML_ENTITY_RE = re.compile(r"&(#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z][a-zA-Z0-9]*);")
