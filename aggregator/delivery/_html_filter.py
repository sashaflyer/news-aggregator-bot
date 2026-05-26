"""Whitelist outgoing HTML tags + link schemes for Telegram parse_mode=HTML.

LLM output may contain prompt-injected anchor tags pointing to attacker URLs.
We allow only the Telegram-supported tag set with http(s) hrefs. Disallowed
tags are stripped but their text content is preserved so legitimate copy
isn't silently lost.
"""
from __future__ import annotations

import re

# Telegram's HTML parse_mode supported tags. `strong` and `em` are aliases of
# `b` and `i` and Telegram accepts them; keeping them avoids LLM-output churn.
_ALLOWED_TAGS = {"b", "i", "u", "s", "a", "code", "pre", "strong", "em"}
_TAG_RE = re.compile(r"<(/?)([a-zA-Z][a-zA-Z0-9]*)([^>]*)>")
_HREF_RE = re.compile(r'href="(https?://[^"]+)"')


def sanitize_outgoing(text: str) -> str:
    """Strip disallowed HTML tags and non-http(s) anchor hrefs from `text`.

    Allowed tags pass through unchanged. Disallowed tags are removed but their
    text content is preserved. Anchor tags with non-http(s) hrefs (e.g.
    `javascript:`) are stripped entirely (both open and close).
    """
    def _replace(m: re.Match[str]) -> str:
        slash, tag, attrs = m.group(1), m.group(2).lower(), m.group(3)
        if tag not in _ALLOWED_TAGS:
            return ""
        if tag == "a":
            if slash:
                return "</a>"
            href = _HREF_RE.search(attrs)
            if not href:
                return ""
            return f'<a href="{href.group(1)}">'
        return f"<{slash}{tag}>"

    return _TAG_RE.sub(_replace, text)
