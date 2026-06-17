import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from aggregator.sources.github import GithubSource, _to_item

FIXTURE = Path(__file__).parent / "fixtures" / "github_search.json"


@pytest.mark.asyncio
async def test_fetch_with_github_keywords():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls = []

    async def capture(client, query, token, days=1):
        calls.append(query)
        return fixture["items"]

    with patch("aggregator.sources.github._resolve_token", return_value="fake"):
        with patch("aggregator.sources.github._search_github", side_effect=capture):
            src = GithubSource()
            items = await src.fetch({
                "github_keywords": ["bitcoin", "ethereum"],
            })

    assert calls == ["bitcoin", "ethereum"]
    assert len(items) == 2
    assert all(it.source == "github" for it in items)
    assert all(it.id.startswith("github:") for it in items)


@pytest.mark.asyncio
async def test_fetch_falls_back_to_hn_keywords():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls = []

    async def capture(client, query, token, days=1):
        calls.append(query)
        return fixture["items"]

    with patch("aggregator.sources.github._resolve_token", return_value="fake"):
        with patch("aggregator.sources.github._search_github", side_effect=capture):
            src = GithubSource()
            items = await src.fetch({
                "hn_keywords": ["bitcoin"],
                # no github_keywords
            })

    assert calls == ["bitcoin"]
    assert len(items) == 2


@pytest.mark.asyncio
async def test_fetch_with_symbols():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    calls = []

    async def capture(client, query, token, days=1):
        calls.append(query)
        return fixture["items"]

    with patch("aggregator.sources.github._resolve_token", return_value="fake"):
        with patch("aggregator.sources.github._search_github", side_effect=capture):
            src = GithubSource()
            await src.fetch({"symbols": ["SOL", "SUI"]})

    assert calls == ["SOL", "SUI"]


@pytest.mark.asyncio
async def test_fetch_skips_when_no_token():
    with patch("aggregator.sources.github._resolve_token", return_value=None):
        src = GithubSource()
        items = await src.fetch({"github_keywords": ["bitcoin"]})
    assert items == []


@pytest.mark.asyncio
async def test_fetch_with_empty_queries_returns_empty():
    src = GithubSource()
    assert await src.fetch({}) == []
    assert await src.fetch({"github_keywords": [], "symbols": []}) == []


@pytest.mark.asyncio
async def test_fetch_deduplicates_across_queries():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    async def capture(client, query, token, days=1):
        return fixture["items"]

    with patch("aggregator.sources.github._resolve_token", return_value="fake"):
        with patch("aggregator.sources.github._search_github", side_effect=capture):
            src = GithubSource()
            items = await src.fetch({
                "github_keywords": ["bitcoin", "crypto"],
            })

    # Same items returned for both queries; dedup should collapse to 2.
    assert len(items) == 2


def test_to_item_maps_engagement():
    raw = {
        "id": 999,
        "html_url": "https://github.com/test/repo/issues/1",
        "title": "Test issue",
        "body": "Hello world",
        "state": "open",
        "created_at": "2026-06-16T12:00:00Z",
        "user": {"login": "testuser"},
        "labels": [{"name": "bug"}],
        "reactions": {"total_count": 15},
        "comments": 8,
    }
    item = _to_item(raw, rank=0)
    assert item is not None
    assert item.engagement_raw["reactions"] == 15
    assert item.engagement_raw["score"] == 15
    assert item.engagement_raw["comments"] == 8
    assert item.metadata["author"] == "testuser"
    assert item.metadata["repo"] == "test/repo"
    assert item.metadata["is_pr"] is False
    assert item.metadata["labels"] == ["bug"]


def test_to_item_detects_pull_request():
    raw = {
        "id": 1000,
        "html_url": "https://github.com/test/repo/pull/42",
        "title": "Fix something",
        "body": "",
        "state": "open",
        "created_at": "2026-06-16T12:00:00Z",
        "user": {"login": "dev"},
        "labels": [],
        "reactions": {"total_count": 3},
        "comments": 1,
        "pull_request": {"url": "https://api.github.com/repos/test/repo/pulls/42"},
    }
    item = _to_item(raw, rank=0)
    assert item is not None
    assert item.metadata["is_pr"] is True


def test_to_item_returns_none_without_url():
    raw = {"id": 1, "title": "No URL", "created_at": "2026-06-16T12:00:00Z"}
    assert _to_item(raw, rank=0) is None


def test_to_item_returns_none_without_date():
    raw = {"id": 1, "html_url": "https://github.com/x/y/issues/1", "title": "No date"}
    assert _to_item(raw, rank=0) is None


def test_to_item_parses_created_at_as_aware_datetime():
    raw = {
        "id": 1,
        "html_url": "https://github.com/x/y/issues/1",
        "title": "T",
        "created_at": "2026-06-16T12:00:00Z",
        "user": {"login": "u"},
        "reactions": {"total_count": 0},
        "comments": 0,
    }
    item = _to_item(raw, rank=0)
    assert item is not None
    assert item.created_at.year == 2026
    assert item.created_at.tzinfo is not None


@pytest.mark.asyncio
async def test_fetch_subquery_failure_does_not_kill_batch():
    async def side_effect(client, query, token, days=1):
        if query == "fail":
            raise RuntimeError("boom")
        return []

    with patch("aggregator.sources.github._resolve_token", return_value="fake"):
        with patch("aggregator.sources.github._search_github", side_effect=side_effect):
            src = GithubSource()
            items = await src.fetch({
                "github_keywords": ["fail", "bitcoin"],
            })
    # Should not raise; "fail" is logged as a warning, "bitcoin" returns empty.
    assert items == []
