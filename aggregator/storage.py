"""Storage layer wrapping the vendored last30days SQLite store.

Adds three project-specific tables (digest_log, source_health, run_history)
and exposes a thin Storage class used by the pipeline, scheduler, and /status
command. Topic seeding is idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generator

from aggregator.url_norm import canonicalize, dedup_key
from aggregator.vendor.last30days import store as upstream_store

if TYPE_CHECKING:
    from aggregator.config import AnyTopicConfig


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
CREATE TABLE IF NOT EXISTS delivered_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT,
    delivered_at TIMESTAMP NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_delivered_findings_topic_url
    ON delivered_findings(topic_id, url);
CREATE INDEX IF NOT EXISTS idx_delivered_findings_topic_delivered_at
    ON delivered_findings(topic_id, delivered_at);
"""


def _iso(at: datetime) -> str:
    assert at.tzinfo is not None, "datetime passed to _iso must be tz-aware"
    return at.isoformat()


PROJECT_SCHEMA_VERSION = 3

_MIGRATIONS: dict[int, list[str]] = {
    # v1 is implicit: the contents of _ADDED_SCHEMA. We just record version=1
    # after init_schema runs for existing or fresh DBs. Migrations 2+ add new
    # statements applied in version order.
    2: [
        # Dedupe before adding UNIQUE: keep the earliest row per (topic_id, url).
        "DELETE FROM delivered_findings WHERE id NOT IN ("
        " SELECT MIN(id) FROM delivered_findings GROUP BY topic_id, url"
        ")",
        "DROP INDEX IF EXISTS idx_delivered_findings_topic_url",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_delivered_findings_topic_url "
        "ON delivered_findings(topic_id, url)",
    ],
    # v3 is handled in Python by _migrate() (canonicalize existing urls).
    3: [],
}


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations to bring the DB up to PROJECT_SCHEMA_VERSION."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS project_schema_version (version INTEGER)"
    )
    row = conn.execute("SELECT version FROM project_schema_version").fetchone()
    current = row[0] if row else 0
    if current == 0:
        conn.execute("INSERT INTO project_schema_version VALUES (?)", (1,))
        current = 1
    for v in sorted(_MIGRATIONS):
        if v > current:
            for stmt in _MIGRATIONS[v]:
                conn.execute(stmt)
            if v == 3:
                # Backfill: canonicalize existing URLs in place. UNIQUE index
                # may collide if two rows canonicalize to the same value;
                # INSERT OR IGNORE pattern doesn't apply to UPDATE, so we
                # collapse duplicates manually.
                rows = conn.execute(
                    "SELECT id, topic_id, url FROM delivered_findings"
                ).fetchall()
                seen: dict[tuple[str, str], int] = {}
                for row_id, topic_id, url in rows:
                    new = canonicalize(url)
                    key = (topic_id, new)
                    if key in seen:
                        # Another row already holds the canonical form here;
                        # drop this duplicate to keep the UNIQUE index happy.
                        conn.execute(
                            "DELETE FROM delivered_findings WHERE id=?",
                            (row_id,),
                        )
                        continue
                    seen[key] = row_id
                    if new != url:
                        conn.execute(
                            "UPDATE delivered_findings SET url=? WHERE id=?",
                            (new, row_id),
                        )
            conn.execute("UPDATE project_schema_version SET version = ?", (v,))
            current = v
    conn.commit()


class Storage:
    """Thin SQLite access layer for the aggregator."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> str:
        return str(self._path)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Initialize vendored schema then add project-specific tables."""
        upstream_store.init_db(self._path)
        with self._connect() as conn:
            conn.executescript(_ADDED_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL")
            _migrate(conn)

    # --- Topics ---

    def seed_topics(self, topics: dict[str, "AnyTopicConfig"]) -> None:
        """Idempotently seed each topic in `topics` (dict keyed by topic id).

        For each TopicConfig, the full set of per-source query inputs plus
        `kind`, `sources`, and `prompt_template` is serialized into
        `topics.search_queries` as a JSON object with the shape:

            {
                "kind": "general" | "watchlist",
                "sources": ["rss", ...],
                "prompt_template": "general_crypto.md",
                "polymarket_tags": [...], "hn_keywords": [...],
                "rss_feeds": [...],
                "watch": [{"ticker": "SOL", "aliases": ["Solana"], "feeds": [...], "search_feeds": [...]}, ...]
            }

        Consumers (pipeline, synth) decode this back from the row when needed.
        """
        for topic_id, topic in topics.items():
            payload: dict[str, Any] = {
                "kind": topic.kind,
                "sources": list(topic.sources),
                "prompt_template": topic.prompt_template,
                "polymarket_tags": list(topic.polymarket_tags),
                "hn_keywords": list(topic.hn_keywords),
                "rss_feeds": list(topic.rss_feeds),
                "watch": [],
            }
            if topic.kind == "watchlist":
                payload["watch"] = [
                    {"ticker": w.ticker, "aliases": list(w.aliases),
                     "feeds": list(w.feeds), "search_feeds": list(w.search_feeds)}
                    for w in topic.watch
                ]
            self._upsert_topic(topic_id, json.dumps(payload), topic.schedule)

    def _upsert_topic(self, name: str, search_queries: str, schedule: str) -> None:
        with self._connect() as conn:
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

    def list_topics(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM topics ORDER BY name"
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Source health ---

    def record_source_failure(self, source: str, message: str, *, at: datetime) -> None:
        ts = _iso(at)
        with self._connect() as conn:
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

    def record_source_success(self, source: str, *, at: datetime) -> None:
        ts = _iso(at)
        with self._connect() as conn:
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

    def get_source_health(self, source: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM source_health WHERE source = ?", (source,)
            ).fetchone()
            return dict(row) if row else None

    def all_source_health(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM source_health ORDER BY source"
            ).fetchall()
            return [dict(r) for r in rows]

    # --- Runs ---

    def start_run(self, topic_id: str, *, trigger: str, at: datetime) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO run_history (topic_id, started_at, trigger)
                   VALUES (?, ?, ?)""",
                (topic_id, _iso(at), trigger),
            )
            row_id = cur.lastrowid
            if row_id is None:
                raise RuntimeError("INSERT into run_history returned no row id")
            return row_id

    def finish_run(
        self,
        run_id: int,
        *,
        status: str,
        items_fetched: int,
        items_delivered: int,
        error_message: str | None = None,
        at: datetime,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE run_history
                   SET finished_at = ?, status = ?, items_fetched = ?,
                       items_delivered = ?, error_message = ?
                   WHERE id = ?""",
                (_iso(at), status, items_fetched, items_delivered, error_message, run_id),
            )

    def last_run(self, topic_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM run_history
                   WHERE topic_id = ? AND finished_at IS NOT NULL
                   ORDER BY finished_at DESC, id DESC
                   LIMIT 1""",
                (topic_id,),
            ).fetchone()
            return dict(row) if row else None

    # --- Digest log ---

    def log_digest(
        self,
        *,
        run_id: int,
        topic_id: str,
        message_text: str,
        telegram_message_ids: list[int],
        at: datetime,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO digest_log
                       (run_id, topic_id, sent_at, message_text, telegram_message_ids)
                   VALUES (?, ?, ?, ?, ?)""",
                (run_id, topic_id, _iso(at), message_text, json.dumps(telegram_message_ids)),
            )

    # --- Delivered findings (cross-run memory so we don't repeat items) ---

    def record_delivered_items(
        self,
        *,
        topic_id: str,
        items: list[dict[str, Any]],
        at: datetime,
    ) -> int:
        """Record the items just delivered for `topic_id` so future digests can
        filter them out. Items with no URL fall back to ``id:<source-id>`` as a
        dedup key; items with neither are skipped. Returns rows actually
        inserted (UNIQUE index collisions via INSERT OR IGNORE return 0 from
        Cursor.rowcount, so re-deliveries don't inflate the count).
        """
        ts = _iso(at)
        with self._connect() as conn:
            n = 0
            for it in items:
                key = dedup_key(it)
                if not key:
                    continue
                cur = conn.execute(
                    """INSERT OR IGNORE INTO delivered_findings
                           (topic_id, item_id, url, title, delivered_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (topic_id, str(it.get("id", "")), key, it.get("title") or "", ts),
                )
                n += cur.rowcount or 0
            return n

    def recently_delivered_urls(
        self,
        *,
        topic_id: str,
        since: datetime,
    ) -> set[str]:
        """Return the set of URLs delivered for `topic_id` at or after `since`."""
        with self._connect() as conn:
            cur = conn.execute(
                """SELECT DISTINCT url FROM delivered_findings
                       WHERE topic_id = ? AND delivered_at >= ?""",
                (topic_id, _iso(since)),
            )
            return {row["url"] for row in cur.fetchall() if row["url"]}

    def prune_delivered_findings(self, *, older_than: datetime) -> int:
        """Delete delivered_findings rows older than ``older_than``.

        The cross-run dedup window is ``cfg.scoring.dedup_window_days``; rows
        beyond that window are no longer consulted by ``recently_delivered_urls``
        and would otherwise grow unbounded over time, slowing the
        ``SELECT DISTINCT url`` scan on every run.
        """
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM delivered_findings WHERE delivered_at < ?",
                (_iso(older_than),),
            )
            return cur.rowcount or 0
