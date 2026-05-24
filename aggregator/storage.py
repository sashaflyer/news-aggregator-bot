"""Storage layer wrapping the vendored last30days SQLite store.

Adds three project-specific tables (digest_log, source_health, run_history)
and exposes a thin Storage class used by the pipeline, scheduler, and /status
command. Topic seeding is idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from aggregator.vendor.last30days import store as upstream_store


_ADDED_SCHEMA = """
CREATE TABLE IF NOT EXISTS digest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    topic_id TEXT NOT NULL,
    sent_at TIMESTAMP NOT NULL,
    message_text TEXT NOT NULL,
    telegram_message_ids TEXT
);
CREATE TABLE IF NOT EXISTS source_health (
    source TEXT PRIMARY KEY,
    last_attempt_at TIMESTAMP,
    last_success_at TIMESTAMP,
    last_error_at TIMESTAMP,
    last_error_message TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS run_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    finished_at TIMESTAMP,
    status TEXT,
    items_fetched INTEGER,
    items_delivered INTEGER,
    error_message TEXT,
    trigger TEXT NOT NULL
);
"""


def _iso(at: datetime) -> str:
    return at.isoformat()


class Storage:
    """Thin SQLite access layer for the aggregator."""

    def __init__(self, path: str):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> str:
        return str(self._path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        return conn

    def init_schema(self) -> None:
        """Initialize vendored schema then add project-specific tables."""
        upstream_store.init_db(self._path)
        conn = self._connect()
        try:
            conn.executescript(_ADDED_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # --- Topics ---

    def seed_topics(
        self,
        *,
        general_subreddits: List[str],
        general_polymarket_tags: List[str],
        general_schedule: str,
        watchlist_symbols: List[str],
        watchlist_schedule: str,
    ) -> None:
        """Idempotently seed crypto_general and crypto_watchlist topics."""
        general_queries = json.dumps({
            "subreddits": general_subreddits,
            "polymarket_tags": general_polymarket_tags,
        })
        watchlist_queries = json.dumps({"symbols": watchlist_symbols})
        self._upsert_topic("crypto_general", general_queries, general_schedule)
        self._upsert_topic("crypto_watchlist", watchlist_queries, watchlist_schedule)

    def _upsert_topic(self, name: str, search_queries: str, schedule: str) -> None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id FROM topics WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """INSERT INTO topics (name, search_queries, schedule)
                       VALUES (?, ?, ?)""",
                    (name, search_queries, schedule),
                )
            else:
                conn.execute(
                    """UPDATE topics
                       SET search_queries = ?, schedule = ?, updated_at = datetime('now')
                       WHERE name = ?""",
                    (search_queries, schedule, name),
                )
            conn.commit()
        finally:
            conn.close()

    def list_topics(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM topics ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # --- Source health ---

    def record_source_failure(self, source: str, message: str, *, at: datetime) -> None:
        ts = _iso(at)
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO source_health
                       (source, last_attempt_at, last_error_at, last_error_message,
                        consecutive_failures)
                   VALUES (?, ?, ?, ?, 1)
                   ON CONFLICT(source) DO UPDATE SET
                       last_attempt_at = excluded.last_attempt_at,
                       last_error_at = excluded.last_error_at,
                       last_error_message = excluded.last_error_message,
                       consecutive_failures = source_health.consecutive_failures + 1""",
                (source, ts, ts, message),
            )
            conn.commit()
        finally:
            conn.close()

    def record_source_success(self, source: str, *, at: datetime) -> None:
        ts = _iso(at)
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO source_health
                       (source, last_attempt_at, last_success_at, consecutive_failures)
                   VALUES (?, ?, ?, 0)
                   ON CONFLICT(source) DO UPDATE SET
                       last_attempt_at = excluded.last_attempt_at,
                       last_success_at = excluded.last_success_at,
                       consecutive_failures = 0""",
                (source, ts, ts),
            )
            conn.commit()
        finally:
            conn.close()

    def get_source_health(self, source: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM source_health WHERE source = ?", (source,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def all_source_health(self) -> List[Dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM source_health ORDER BY source"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # --- Runs ---

    def start_run(self, topic_id: str, *, trigger: str, at: datetime) -> int:
        conn = self._connect()
        try:
            cur = conn.execute(
                """INSERT INTO run_history (topic_id, started_at, trigger)
                   VALUES (?, ?, ?)""",
                (topic_id, _iso(at), trigger),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        items_fetched: int,
        items_delivered: int,
        error_message: Optional[str] = None,
        at: datetime,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE run_history
                   SET finished_at = ?, status = ?, items_fetched = ?,
                       items_delivered = ?, error_message = ?
                   WHERE id = ?""",
                (_iso(at), status, items_fetched, items_delivered, error_message, run_id),
            )
            conn.commit()
        finally:
            conn.close()

    def last_run(self, topic_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                """SELECT * FROM run_history
                   WHERE topic_id = ? AND finished_at IS NOT NULL
                   ORDER BY finished_at DESC, id DESC
                   LIMIT 1""",
                (topic_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    # --- Digest log ---

    def log_digest(
        self,
        *,
        run_id: int,
        topic_id: str,
        message_text: str,
        telegram_message_ids: List[int],
        at: datetime,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO digest_log
                       (run_id, topic_id, sent_at, message_text, telegram_message_ids)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, topic_id, _iso(at), message_text, json.dumps(telegram_message_ids)),
            )
            conn.commit()
        finally:
            conn.close()
