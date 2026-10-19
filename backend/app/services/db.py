from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_SCHEMA = """
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
            self._conn.executescript(_SCHEMA)
            self._conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self._conn.commit()

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
