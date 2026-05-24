import sqlite3
from datetime import datetime, timezone

import pytest

from aggregator.storage import Storage


@pytest.fixture
def storage(tmp_path):
    db = tmp_path / "test.db"
    s = Storage(str(db))
    s.init_schema()
    return s


def test_added_tables_exist(storage):
    with sqlite3.connect(storage.path) as conn:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    assert "digest_log" in names
    assert "source_health" in names


def test_seed_topics_idempotent(storage):
    storage.seed_topics(
        general_subreddits=["CryptoCurrency"],
        general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL", "SUI"],
        watchlist_schedule="0 8 * * *",
    )
    storage.seed_topics(  # second call must not duplicate
        general_subreddits=["CryptoCurrency"],
        general_polymarket_tags=["crypto"],
        general_schedule="0 8 * * *",
        watchlist_symbols=["SOL", "SUI"],
        watchlist_schedule="0 8 * * *",
    )
    topics = storage.list_topics()
    names = sorted(t["name"] for t in topics)
    assert names == ["crypto_general", "crypto_watchlist"]


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
