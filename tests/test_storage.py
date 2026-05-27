import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from aggregator.config import TopicConfig, WatchEntry
from aggregator.storage import Storage


def test_iso_rejects_naive_datetime():
    from aggregator.storage import _iso
    with pytest.raises(AssertionError):
        _iso(datetime(2025, 1, 1))


def test_project_schema_version_recorded(tmp_path):
    from aggregator.storage import Storage, PROJECT_SCHEMA_VERSION
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    with sqlite3.connect(s.path) as conn:
        v = conn.execute("SELECT version FROM project_schema_version").fetchone()[0]
    assert v == PROJECT_SCHEMA_VERSION


def test_record_delivered_items_is_idempotent(tmp_path):
    s = Storage(str(tmp_path / "test.db"))
    s.init_schema()
    items = [{"url": "https://x/a", "title": "t", "id": "x:1"}]
    now = datetime.now(timezone.utc)
    s.record_delivered_items(topic_id="t1", items=items, at=now)
    s.record_delivered_items(topic_id="t1", items=items, at=now)
    urls = s.recently_delivered_urls(topic_id="t1", since=now - timedelta(seconds=1))
    assert urls == {"https://x/a"}
    with sqlite3.connect(s.path) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM delivered_findings WHERE topic_id=?", ("t1",)
        ).fetchone()[0]
    assert n == 1


def test_migration_from_v1_to_v2_adds_unique_index(tmp_path):
    db = tmp_path / "t.db"
    # Seed a v1 DB: project_schema_version=1, no unique index
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE project_schema_version (version INTEGER);
        INSERT INTO project_schema_version VALUES (1);
        CREATE TABLE delivered_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id TEXT NOT NULL, item_id TEXT NOT NULL,
            url TEXT NOT NULL, title TEXT,
            delivered_at TIMESTAMP NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    s = Storage(str(db))
    s.init_schema()
    with sqlite3.connect(s.path) as conn:
        idx = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='uq_delivered_findings_topic_url'"
        ).fetchone()
    assert idx is not None


def test_record_delivered_items_uses_id_when_url_empty(tmp_path):
    s = Storage(str(tmp_path / "t.db"))
    s.init_schema()
    items = [{"url": "", "id": "polymarket:xyz", "title": "t"}]
    now = datetime.now(timezone.utc)
    s.record_delivered_items(topic_id="t", items=items, at=now)
    urls = s.recently_delivered_urls(topic_id="t", since=now - timedelta(seconds=1))
    assert "id:polymarket:xyz" in urls


def test_migration_v3_canonicalizes_urls(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE project_schema_version (version INTEGER);
        INSERT INTO project_schema_version VALUES (2);
        CREATE TABLE delivered_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id TEXT NOT NULL, item_id TEXT NOT NULL,
            url TEXT NOT NULL, title TEXT,
            delivered_at TIMESTAMP NOT NULL
        );
        CREATE UNIQUE INDEX uq_delivered_findings_topic_url
            ON delivered_findings(topic_id, url);
        INSERT INTO delivered_findings
            (topic_id, item_id, url, title, delivered_at)
            VALUES ('t', 'x', 'https://X.example/a/?utm_source=z', 't',
                    '2025-01-01T00:00:00+00:00');
    """)
    conn.commit()
    conn.close()
    s = Storage(str(db))
    s.init_schema()
    with sqlite3.connect(s.path) as conn:
        url = conn.execute("SELECT url FROM delivered_findings").fetchone()[0]
    assert url == "https://x.example/a"


@pytest.fixture
def storage(tmp_path):
    db = tmp_path / "test.db"
    s = Storage(str(db))
    s.init_schema()
    return s


def _sample_topics() -> dict[str, TopicConfig]:
    return {
        "crypto_general": TopicConfig(
            kind="general",
            sources=["reddit", "polymarket"],
            subreddits=["CryptoCurrency"],
            polymarket_tags=["crypto"],
            prompt_template="general_crypto.md",
            top_n=10,
            schedule="0 8 * * *",
        ),
        "crypto_watchlist": TopicConfig(
            kind="watchlist",
            sources=["reddit"],
            watch=[
                WatchEntry(ticker="SOL", aliases=["Solana"]),
                WatchEntry(ticker="SUI"),
            ],
            prompt_template="watchlist.md",
            per_symbol_top_n=5,
            schedule="0 8 * * *",
        ),
    }


def test_added_tables_exist(storage):
    with sqlite3.connect(storage.path) as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "digest_log" in names
    assert "source_health" in names


def test_seed_topics_idempotent(storage):
    topics = _sample_topics()
    storage.seed_topics(topics)
    storage.seed_topics(topics)  # second call must not duplicate
    rows = storage.list_topics()
    names = sorted(t["name"] for t in rows)
    assert names == ["crypto_general", "crypto_watchlist"]


def test_seed_topics_persists_query_payload(storage):
    storage.seed_topics(_sample_topics())
    rows = {r["name"]: r for r in storage.list_topics()}
    g = json.loads(rows["crypto_general"]["search_queries"])
    assert g["kind"] == "general"
    assert g["sources"] == ["reddit", "polymarket"]
    assert g["subreddits"] == ["CryptoCurrency"]
    assert g["polymarket_tags"] == ["crypto"]
    assert g["prompt_template"] == "general_crypto.md"
    w = json.loads(rows["crypto_watchlist"]["search_queries"])
    assert w["kind"] == "watchlist"
    assert w["watch"] == [
        {"ticker": "SOL", "aliases": ["Solana"]},
        {"ticker": "SUI", "aliases": []},
    ]


def test_record_source_health_failure_then_success(storage):
    now = datetime.now(timezone.utc)
    storage.record_source_failure("reddit", "boom", at=now)
    storage.record_source_failure("reddit", "boom again", at=now)
    h = storage.get_source_health("reddit")
    assert h["consecutive_failures"] == 2
    assert h["last_error_message"] == "boom again"

    storage.record_source_success("reddit", at=now)
    h = storage.get_source_health("reddit")
    assert h["consecutive_failures"] == 0
    assert h["last_success_at"] is not None


def test_record_run_and_digest_log(storage):
    now = datetime.now(timezone.utc)
    run_id = storage.start_run("crypto_general", trigger="scheduled", at=now)
    storage.finish_run(run_id, status="ok", items_fetched=10, items_delivered=5, at=now)
    storage.log_digest(run_id=run_id, topic_id="crypto_general",
                       message_text="hello", telegram_message_ids=[1, 2], at=now)
    last = storage.last_run("crypto_general")
    assert last["status"] == "ok"
    assert last["items_delivered"] == 5


def test_record_and_recall_delivered_items(tmp_path):
    from datetime import datetime, timedelta, timezone
    from aggregator.storage import Storage

    s = Storage(str(tmp_path / "t.db"))
    s.init_schema()
    now = datetime.now(timezone.utc)

    n = s.record_delivered_items(
        topic_id="crypto_general",
        items=[
            {"id": "r:1", "url": "https://reddit.com/1", "title": "A"},
            {"id": "r:2", "url": "https://reddit.com/2", "title": "B"},
            {"id": "", "url": "", "title": "no key"},  # no url + no id -> skipped
        ],
        at=now,
    )
    assert n == 2

    urls = s.recently_delivered_urls(
        topic_id="crypto_general",
        since=now - timedelta(hours=1),
    )
    # reddit.com is canonicalized to www.reddit.com.
    assert urls == {"https://www.reddit.com/1", "https://www.reddit.com/2"}


def test_recently_delivered_urls_filters_by_topic(tmp_path):
    from datetime import datetime, timedelta, timezone
    from aggregator.storage import Storage

    s = Storage(str(tmp_path / "t.db"))
    s.init_schema()
    now = datetime.now(timezone.utc)
    s.record_delivered_items(
        topic_id="crypto_general",
        items=[{"id": "1", "url": "https://a.com", "title": "A"}],
        at=now,
    )
    s.record_delivered_items(
        topic_id="crypto_watchlist",
        items=[{"id": "2", "url": "https://b.com", "title": "B"}],
        at=now,
    )

    # URLs come back canonicalized (path '' -> '/').
    assert s.recently_delivered_urls(
        topic_id="crypto_general", since=now - timedelta(hours=1)
    ) == {"https://a.com/"}
    assert s.recently_delivered_urls(
        topic_id="crypto_watchlist", since=now - timedelta(hours=1)
    ) == {"https://b.com/"}


def test_recently_delivered_urls_filters_by_time(tmp_path):
    from datetime import datetime, timedelta, timezone
    from aggregator.storage import Storage

    s = Storage(str(tmp_path / "t.db"))
    s.init_schema()
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    s.record_delivered_items(
        topic_id="crypto_general",
        items=[{"id": "old", "url": "https://old.com", "title": "old"}],
        at=old,
    )
    s.record_delivered_items(
        topic_id="crypto_general",
        items=[{"id": "new", "url": "https://new.com", "title": "new"}],
        at=now,
    )
    recent = s.recently_delivered_urls(
        topic_id="crypto_general", since=now - timedelta(days=7)
    )
    assert recent == {"https://new.com/"}


def test_prune_delivered_findings_deletes_old_rows(tmp_path):
    from datetime import datetime, timedelta, timezone
    from aggregator.storage import Storage

    s = Storage(str(tmp_path / "t.db"))
    s.init_schema()
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=30)
    s.record_delivered_items(
        topic_id="t1",
        items=[{"id": "old", "url": "https://old.com", "title": "o"}],
        at=old,
    )
    s.record_delivered_items(
        topic_id="t1",
        items=[{"id": "new", "url": "https://new.com", "title": "n"}],
        at=now,
    )
    deleted = s.prune_delivered_findings(older_than=now - timedelta(days=7))
    assert deleted == 1
    # The "new" row survives (path '' canonicalizes to '/').
    remaining = s.recently_delivered_urls(
        topic_id="t1", since=now - timedelta(days=365)
    )
    assert remaining == {"https://new.com/"}
