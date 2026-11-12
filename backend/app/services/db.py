from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 8

# Version 1 schema, kept verbatim so databases created before per-user
# isolation can be migrated in place without losing rows.
_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    format TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    path TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    page_count INTEGER,
    extraction_status TEXT NOT NULL,
    text_extractable INTEGER NOT NULL DEFAULT 0,
    section_count INTEGER NOT NULL DEFAULT 0,
    total_chars INTEGER NOT NULL DEFAULT 0,
    sections_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS study_jobs (
    job_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    format TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_epoch REAL NOT NULL,
    expires_at REAL NOT NULL,
    page_count INTEGER,
    item_count INTEGER NOT NULL DEFAULT 0,
    items_json TEXT NOT NULL DEFAULT '[]',
    document_summary TEXT,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    error_json TEXT,
    progress_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_study_jobs_created
    ON study_jobs (created_epoch DESC);
"""

_V2_USERS = """
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
"""

_V3_TOPIC_SOURCES = """
CREATE TABLE IF NOT EXISTS topic_sources (
    source_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    document_id TEXT,
    document_filename TEXT,
    document_format TEXT,
    document_page_count INTEGER,
    status TEXT NOT NULL,
    image_count INTEGER NOT NULL DEFAULT 0,
    total_chars INTEGER NOT NULL DEFAULT 0,
    topics_json TEXT NOT NULL DEFAULT '[]',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topic_sources_owner_created
    ON topic_sources (owner_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_topic_sources_expires
    ON topic_sources (expires_at);

CREATE TABLE IF NOT EXISTS topic_source_images (
    source_id TEXT NOT NULL,
    image_index INTEGER NOT NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    path TEXT NOT NULL,
    ocr_status TEXT NOT NULL,
    ocr_text TEXT NOT NULL DEFAULT '',
    warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    PRIMARY KEY (source_id, image_index),
    FOREIGN KEY (source_id) REFERENCES topic_sources (source_id)
        ON DELETE CASCADE
);
"""


_V6_TOPIC_RESULTS = """
CREATE TABLE IF NOT EXISTS topic_study_results (
    result_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    document_id TEXT,
    document_filename TEXT,
    document_format TEXT,
    mode TEXT NOT NULL,
    ai_scope TEXT NOT NULL,
    status TEXT NOT NULL,
    topic_count INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_topic_results_owner_created
    ON topic_study_results (owner_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_topic_results_source
    ON topic_study_results (source_id);

CREATE INDEX IF NOT EXISTS idx_topic_results_expires
    ON topic_study_results (expires_at);
"""

_V8_PAGES = """
CREATE TABLE IF NOT EXISTS document_pages (
    document_id TEXT NOT NULL,
    page_index INTEGER NOT NULL,
    page_number INTEGER,
    kind TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'native',
    structure_json TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (document_id, page_index),
    FOREIGN KEY (document_id) REFERENCES documents (document_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_document_pages_document
    ON document_pages (document_id, page_index);
"""


_V7_SUMMARY_CACHE = """
CREATE TABLE IF NOT EXISTS summary_cache (
    owner_id TEXT NOT NULL,
    key_hash TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (owner_id, key_hash)
);

CREATE INDEX IF NOT EXISTS idx_summary_cache_created
    ON summary_cache (created_at);
"""


class Database:
    """Thin SQLite wrapper (stdlib only). Single connection, serialized access.

    The database is optional: when it is not configured the stores fall back to
    in-memory behaviour so isolated unit tests keep working.
    """

    def __init__(self) -> None:
        self._conn: sqlite3.Connection | None = None
        self._path: Path | None = None
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return self._conn is not None

    @property
    def path(self) -> Path | None:
        return self._path

    def configure(self, path: str | Path) -> None:
        resolved = Path(path).resolve()
        with self._lock:
            if self._conn is not None and self._path == resolved:
                return
            self._close_locked()
            resolved.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(resolved), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._conn = conn
            self._path = resolved
            self._migrate_locked()

    def reset(self) -> None:
        with self._lock:
            self._close_locked()
            self._path = None

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
            except sqlite3.Error:
                pass
            self._conn.close()
        self._conn = None

    def _migrate_locked(self) -> None:
        assert self._conn is not None
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version < 1:
            self._conn.executescript(_SCHEMA_V1)
            version = 1
        if version < 2:
            self._migrate_v2_locked()
            self._set_version_locked(2)
            version = 2
        if version < 3:
            self._migrate_v3_locked()
            self._set_version_locked(3)
            version = 3
        if version < 4:
            self._migrate_v4_locked()
            self._set_version_locked(4)
            version = 4
        if version < 5:
            self._migrate_v5_locked()
            self._set_version_locked(5)
            version = 5
        if version < 6:
            self._migrate_v6_locked()
            self._set_version_locked(6)
            version = 6
        if version < 7:
            self._migrate_v7_locked()
            self._set_version_locked(7)
            version = 7
        if version < 8:
            self._migrate_v8_locked()
            self._set_version_locked(8)

    def _set_version_locked(self, version: int) -> None:
        assert self._conn is not None
        self._conn.execute(f"PRAGMA user_version={version}")
        self._conn.commit()

    def _migrate_v3_locked(self) -> None:
        """Add topic sources and their bounded OCR image records."""
        assert self._conn is not None
        self._conn.executescript(_V3_TOPIC_SOURCES)

    def _migrate_v4_locked(self) -> None:
        """Track transcript provenance and optimistic-concurrency versions."""
        assert self._conn is not None
        if not self._has_column("topic_source_images", "transcript_source"):
            self._conn.execute(
                "ALTER TABLE topic_source_images "
                "ADD COLUMN transcript_source TEXT NOT NULL DEFAULT 'ocr'"
            )
        if not self._has_column("topic_sources", "version"):
            self._conn.execute(
                "ALTER TABLE topic_sources "
                "ADD COLUMN version INTEGER NOT NULL DEFAULT 0"
            )

    def _migrate_v5_locked(self) -> None:
        """Add typed jottings alongside photo transcripts."""
        assert self._conn is not None
        if not self._has_column("topic_sources", "jottings_text"):
            self._conn.execute(
                "ALTER TABLE topic_sources "
                "ADD COLUMN jottings_text TEXT NOT NULL DEFAULT ''"
            )

    def _migrate_v6_locked(self) -> None:
        """Persist generated topic study output so it can be reopened and exported."""
        assert self._conn is not None
        self._conn.executescript(_V6_TOPIC_RESULTS)

    def _migrate_v7_locked(self) -> None:
        """Add owner-scoped summaries with bounded retention."""
        assert self._conn is not None
        self._conn.executescript(_V7_SUMMARY_CACHE)

    def _migrate_v8_locked(self) -> None:
        """Add durable page units and background ingestion state."""
        assert self._conn is not None
        if not self._has_table("documents"):
            return
        if not self._has_column("documents", "ingest_status"):
            self._conn.execute(
                "ALTER TABLE documents ADD COLUMN ingest_status TEXT NOT NULL "
                "DEFAULT 'ready'"
            )
        if not self._has_column("documents", "ingest_progress_json"):
            self._conn.execute(
                "ALTER TABLE documents ADD COLUMN ingest_progress_json TEXT NOT NULL "
                "DEFAULT '{}'"
            )
        if not self._has_column("documents", "ingest_error_json"):
            self._conn.execute(
                "ALTER TABLE documents ADD COLUMN ingest_error_json TEXT"
            )
        self._conn.executescript(_V8_PAGES)

    def _migrate_v2_locked(self) -> None:
        """Add per-user ownership.

        Existing rows keep ``owner_id = NULL``. They remain on disk but are
        unreachable through the API because every read path requires an exact
        owner match. Retention sweeps still remove them when they expire.
        """
        assert self._conn is not None
        if not self._has_column("documents", "owner_id"):
            self._conn.execute("ALTER TABLE documents ADD COLUMN owner_id TEXT")
        if not self._has_column("study_jobs", "owner_id"):
            self._conn.execute("ALTER TABLE study_jobs ADD COLUMN owner_id TEXT")
        self._conn.executescript(_V2_USERS)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_documents_owner "
            "ON documents (owner_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_study_jobs_owner_created "
            "ON study_jobs (owner_id, created_epoch DESC)"
        )

    def _has_column(self, table: str, column: str) -> bool:
        return self._has_table(table) and any(
            row[1] == column
            for row in self._conn.execute(f"PRAGMA table_info({table})").fetchall()
        )

    def _has_table(self, table: str) -> bool:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    @contextmanager
    def transaction(
        self, *, immediate: bool = False
    ) -> Iterator[sqlite3.Connection]:
        """Run several statements atomically while holding the connection lock.

        ``immediate=True`` takes SQLite's write reservation before the first
        statement. Use it for read-then-write invariants such as quotas.
        """
        with self._lock:
            assert self._conn is not None
            if immediate:
                self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(sql, params)
            self._conn.commit()

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            assert self._conn is not None
            return self._conn.execute(sql, params).fetchall()


db = Database()
