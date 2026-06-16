"""RSS feed health scanner.

Probes every RSS URL referenced from ``[topics.*.rss_feeds]`` in config.toml,
classifies each as ``ok`` / ``slow`` / ``dead``, and (with ``--prune``) rewrites
the config to remove dead URLs after writing a ``.bak`` backup.

Lives at ``aggregator/scripts/`` so it can be invoked as a module:
    python -m aggregator.scripts.scan_rss_feeds --config config.toml
    python -m aggregator.scripts.scan_rss_feeds --config config.toml --prune

Network/UA/timeout mirror ``aggregator/sources/rss.py`` so the measurement
matches what the pipeline will actually do.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import feedparser
import httpx

from aggregator.config import load_config


_UA = "Mozilla/5.0 (compatible; news-aggregator/0.1; +rss)"
_TIMEOUT_S = 20.0
_SLOW_THRESHOLD_S = 10.0
_CONCURRENCY = 8


@dataclass
class FeedResult:
    url: str
    topics: list[str]
    status: str            # "ok" | "slow" | "dead"
    http_status: int | None
    elapsed_s: float
    entries: int
    error: str | None


def _probe(url: str, client: httpx.Client) -> FeedResult:
    """Synchronous probe; runs inside ``asyncio.to_thread`` per URL."""
    t0 = time.perf_counter()
    try:
        resp = client.get(url, headers={"User-Agent": _UA}, follow_redirects=True)
    except httpx.HTTPError as e:
        return FeedResult(url, [], "dead", None, time.perf_counter() - t0, 0, str(e))
    elapsed = time.perf_counter() - t0
    if resp.status_code >= 400:
        return FeedResult(
            url, [], "dead", resp.status_code, elapsed, 0,
            f"HTTP {resp.status_code}",
        )
    parsed = feedparser.parse(resp.content)
    entries = len(parsed.entries or [])
    if entries == 0:
        return FeedResult(
            url, [], "dead", resp.status_code, elapsed, 0, "no entries parsed"
        )
    return _classify(url, resp.status_code, elapsed, entries)


def _classify(url: str, http_status: int, elapsed: float,
              entries: int) -> FeedResult:
    status = "slow" if elapsed > _SLOW_THRESHOLD_S else "ok"
    return FeedResult(url, [], status, http_status, elapsed, entries, None)


async def _scan_all(urls: list[tuple[str, str]]) -> list[FeedResult]:
    """Probe (url, topic) pairs with bounded concurrency.

    The tuple preserves the topic each URL came from; we attach it after
    probing so a single ``httpx.Client`` can be shared across all workers.
    """
    sem = asyncio.Semaphore(_CONCURRENCY)
    results_by_url: dict[str, FeedResult] = {}

    def attach_topic(r: FeedResult, topic: str) -> FeedResult:
        r.topics.append(topic)
        return r

    async def run(url: str, topic: str, client: httpx.Client) -> None:
        async with sem:
            r = await asyncio.to_thread(_probe, url, client)
            existing = results_by_url.get(url)
            if existing is None:
                results_by_url[url] = attach_topic(r, topic)
            else:
                existing.topics.append(topic)

    with httpx.Client(timeout=_TIMEOUT_S) as client:
        await asyncio.gather(*(run(u, t, client) for u, t in urls))
    return list(results_by_url.values())


def _collect_feed_topics(cfg) -> list[tuple[str, str]]:
    """Walk cfg.topics and return [(url, topic_id), ...] in declaration order."""
    out: list[tuple[str, str]] = []
    for topic_id, topic in cfg.topics.items():
        for url in topic.rss_feeds:
            out.append((url, topic_id))
    return out


def _print_report(results: list[FeedResult]) -> None:
    by_status: dict[str, list[FeedResult]] = defaultdict(list)
    for r in results:
        by_status[r.status].append(r)

    def fmt_row(r: FeedResult) -> str:
        topics = ",".join(sorted(r.topics))
        code = "" if r.http_status is None else f" {r.http_status}"
        return f"  {r.elapsed_s:5.2f}s{code:<5} entries={r.entries:<4} [{topics}] {r.url}"

    for status in ("ok", "slow", "dead"):
        bucket = by_status.get(status, [])
        if not bucket:
            continue
        print(f"\n{status.upper()} ({len(bucket)})")
        for r in sorted(bucket, key=lambda x: x.url):
            line = fmt_row(r)
            if r.error:
                line += f"   -- {r.error}"
            print(line)


def _prune_config(path: Path, dead_urls: set[str]) -> int:
    """Rewrite config.toml removing ``dead_urls`` from every topic's rss_feeds.

    Backup written to ``<path>.bak``. Uses regex on the raw text so we don't
    depend on the TOML parser round-tripping identical formatting. Only touches
    the ``rss_feeds = [ ... ]`` blocks; everything else is left alone.
    Returns the total number of URLs removed.
    """
    backup = path.with_suffix(path.suffix + ".bak")
    shutil.copy2(path, backup)
    text = path.read_text(encoding="utf-8")

    counter = {"removed": 0}

    def sub(m: re.Match) -> str:
        return _filter_rss_block(m, dead_urls, counter)

    new_text = re.sub(
        r"(?ms)(rss_feeds\s*=\s*\[)([^\]]*?)(\])",
        sub,
        text,
    )
    path.write_text(new_text, encoding="utf-8")
    print(f"\nWrote backup to {backup}")
    print(f"Removed {counter['removed']} URL(s) from rss_feeds blocks across topics.")
    return counter["removed"]


def _filter_rss_block(match: re.Match, dead_urls: set[str],
                      counter: dict[str, int]) -> str:
    head, body, tail = match.group(1), match.group(2), match.group(3)
    new_body = _filter_body(body, dead_urls, counter)
    return f"{head}{new_body}{tail}"


def _filter_body(body: str, dead_urls: set[str],
                 counter: dict[str, int]) -> str:
    """Strip dead URLs from a raw ``rss_feeds = [...]`` body, preserving layout.

    Handles both multi-line blocks (one URL per line) and single-line blocks
    (comma-separated URLs on one line). For single-line blocks, only the dead
    URL strings are removed; surrounding text and other URLs are kept.
    """
    out_lines: list[str] = []
    for line in body.splitlines(keepends=True):
        urls = _urls_from_line(line)
        if not urls:
            out_lines.append(line)
            continue
        if all(u in dead_urls for u in urls):
            counter["removed"] += len(urls)
            continue
        new_line, removed = _drop_dead_from_line(line, dead_urls)
        counter["removed"] += removed
        out_lines.append(new_line)
    return "".join(out_lines)


def _drop_dead_from_line(line: str, dead_urls: set[str]) -> tuple[str, int]:
    """Return ``(line_with_dead_urls_removed, count_removed)``."""
    parts: list[str] = []
    removed = 0
    last = 0
    for m in _QUOTED_RE.finditer(line):
        if m.group(2) in dead_urls:
            parts.append(line[last:m.start()])
            last = m.end()
            removed += 1
    parts.append(line[last:])
    return "".join(parts), removed


_QUOTED_RE = re.compile(r"""(['"])([^'"]+?)\1""")


def _urls_from_line(line: str) -> list[str]:
    """Return all quoted strings on ``line``. Comments ignored."""
    hash_pos = line.find("#")
    code = line if hash_pos < 0 else line[:hash_pos]
    return [m.group(2) for m in _QUOTED_RE.finditer(code)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_rss_feeds",
        description="Probe every RSS URL referenced in config.toml and report health.",
    )
    parser.add_argument("--config", default="config.toml",
                        help="path to config.toml (default: ./config.toml)")
    parser.add_argument("--prune", action="store_true",
                        help="rewrite config.toml to remove dead feeds (writes .bak)")
    args = parser.parse_args(argv)

    cfg = load_config(args.config)
    feed_topics = _collect_feed_topics(cfg)
    if not feed_topics:
        print("No rss_feeds configured.", file=sys.stderr)
        return 1

    unique_urls = sorted({u for u, _ in feed_topics})
    print(f"Scanning {len(unique_urls)} unique feed(s) across {len(cfg.topics)} topic(s)...")

    results = asyncio.run(_scan_all(feed_topics))
    _print_report(results)

    dead = sorted({r.url for r in results if r.status == "dead"})
    slow = sorted({r.url for r in results if r.status == "slow"})
    if dead:
        print(f"\n{len(dead)} dead feed(s) found.", end="")
        if args.prune:
            print(" Pruning config.toml...")
            _prune_config(Path(args.config), set(dead))
        else:
            print(" Re-run with --prune to remove them.")
    if slow:
        print(f"{len(slow)} slow feed(s) found (>{_SLOW_THRESHOLD_S:.0f}s). "
              "Consider replacing or removing.")
    if not dead and not slow:
        print("\nAll feeds healthy.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
