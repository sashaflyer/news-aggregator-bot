"""Tests for aggregator.scripts.scan_rss_feeds. All offline; httpx is mocked."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from aggregator.scripts import scan_rss_feeds  # noqa: E402


# A minimal RSS payload with one entry. feedparser needs <entry> or <item>.
_RSS_OK = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>t</title><link>l</link>
<item><title>x</title><link>https://example.com/x</link><pubDate>Mon, 16 Jun 2026 00:00:00 +0000</pubDate></item>
</channel></rss>"""


class _Resp:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


def _patch_probe(behaviors: dict[str, _Resp | Exception]):
    """Replace _probe with a fake that returns based on URL.

    Mirrors the script's actual classification: dead on error / 4xx / no
    entries, slow/ok otherwise.
    """
    from feedparser import parse
    def fake(url: str, _client) -> "scan_rss_feeds.FeedResult":
        b = behaviors.get(url)
        if isinstance(b, Exception):
            return scan_rss_feeds.FeedResult(url, [], "dead", None, 0.0, 0, str(b))
        if isinstance(b, _Resp):
            if b.status_code >= 400:
                return scan_rss_feeds.FeedResult(
                    url, [], "dead", b.status_code, 0.0, 0, f"HTTP {b.status_code}"
                )
            entries = len(parse(b.content).entries or [])
            if entries == 0:
                return scan_rss_feeds.FeedResult(
                    url, [], "dead", b.status_code, 0.0, 0, "no entries parsed"
                )
            return scan_rss_feeds.FeedResult(
                url, [], "ok", b.status_code, 0.5, entries, None
            )
        return scan_rss_feeds.FeedResult(url, [], "dead", None, 0.0, 0, "no behavior")
    return fake


def test_classify_403_as_dead():
    fake = _patch_probe({"https://x": _Resp(403)})
    r = fake("https://x", None)
    assert r.status == "dead"
    assert r.http_status == 403


def test_classify_ok_with_entries():
    fake = _patch_probe({"https://x": _Resp(200, _RSS_OK)})
    r = fake("https://x", None)
    assert r.status == "ok"
    assert r.entries == 1


def test_classify_200_with_no_entries_as_dead():
    fake = _patch_probe({"https://x": _Resp(200, b"")})
    r = fake("https://x", None)
    assert r.status == "dead"


def test_classify_marks_slow_above_threshold():
    from aggregator.scripts.scan_rss_feeds import _classify, _SLOW_THRESHOLD_S
    r = _classify("https://x", 200, _SLOW_THRESHOLD_S + 0.1, 5)
    assert r.status == "slow"
    r2 = _classify("https://x", 200, _SLOW_THRESHOLD_S - 0.1, 5)
    assert r2.status == "ok"


def test_filter_rss_block_removes_dead_keeps_others():
    body = (
        '\n  "https://keep.example/1",\n'
        '  "https://dead.example/1",  # bad\n'
        '  "https://keep.example/2",\n'
    )
    m = scan_rss_feeds.re.search(
        r"(?ms)(rss_feeds\s*=\s*\[)([^\]]*?)(\])",
        f"rss_feeds = [{body}]",
    )
    assert m is not None
    counter = {"removed": 0}
    out = scan_rss_feeds._filter_rss_block(
        m, {"https://dead.example/1"}, counter,
    )
    assert "https://keep.example/1" in out
    assert "https://keep.example/2" in out
    assert "https://dead.example/1" not in out
    assert counter["removed"] == 1


def test_prune_config_writes_backup_and_removes(tmp_path):
    cfg_text = (
        '[topics.x]\nkind = "general"\nsources = ["rss"]\nschedule = "0 5 * * *"\n'
        'top_n = 5\nprompt_template = "general.md"\n'
        'rss_feeds = [\n'
        '  "https://keep.example/1",\n'
        '  "https://dead.example/1",\n'
        '  "https://keep.example/2",   # keep\n'
        ']\n'
    )
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    removed = scan_rss_feeds._prune_config(cfg_path, {"https://dead.example/1"})
    assert removed == 1
    assert (tmp_path / "config.toml.bak").read_text(encoding="utf-8") == cfg_text
    new = cfg_path.read_text(encoding="utf-8")
    assert "https://keep.example/1" in new
    assert "https://keep.example/2" in new
    assert "https://dead.example/1" not in new


def test_prune_config_handles_multiple_topics(tmp_path):
    cfg_text = (
        '[topics.a]\nkind = "general"\nsources = ["rss"]\nschedule = "0 5 * * *"\n'
        'top_n = 5\nprompt_template = "general.md"\n'
        'rss_feeds = ["https://dead.example/x", "https://keep.example/a"]\n\n'
        '[topics.b]\nkind = "general"\nsources = ["rss"]\nschedule = "0 5 * * *"\n'
        'top_n = 5\nprompt_template = "general.md"\n'
        'rss_feeds = ["https://keep.example/b", "https://dead.example/x"]\n'
    )
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(cfg_text, encoding="utf-8")
    removed = scan_rss_feeds._prune_config(cfg_path, {"https://dead.example/x"})
    assert removed == 2
    new = cfg_path.read_text(encoding="utf-8")
    assert "https://dead.example/x" not in new
    assert "https://keep.example/a" in new
    assert "https://keep.example/b" in new


@pytest.mark.asyncio
async def test_scan_all_collects_topics_per_url():
    """A URL referenced by two topics should list both in the result."""
    fake = lambda url, _c: scan_rss_feeds.FeedResult(
        url, [], "ok", 200, 0.1, 1, None,
    )
    with patch.object(scan_rss_feeds, "_probe", side_effect=fake):
        results = await scan_rss_feeds._scan_all(
            [("https://shared.example/feed", "a"),
             ("https://shared.example/feed", "b"),
             ("https://only-a.example/feed", "a")]
        )
    by_url = {r.url: r.topics for r in results}
    assert sorted(by_url["https://shared.example/feed"]) == ["a", "b"]
    assert by_url["https://only-a.example/feed"] == ["a"]
