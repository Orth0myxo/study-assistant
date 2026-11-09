"""Content-addressed cache for AI summaries (SQLite, via the existing ``db``).

The table is created lazily with ``CREATE TABLE IF NOT EXISTS`` so the existing
schema migrations do not change. The key is a SHA-256 of the exact prompt input
(purpose, source label, source text, prompt version), so a cached summary can
only be read by a caller that already holds the identical source text. Failures
are never cached, and any cache error degrades to a miss: the cache must never
break a request.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from typing import Callable

from app.services.db import Database, db as default_db

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS summary_cache (
    cache_key TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_summary_cache_expires
    ON summary_cache (expires_at);
"""

_SWEEP_EVERY_N_WRITES = 50


def make_cache_key(
    *,
    purpose: str,
    source_label: str,
    source_text: str,
    prompt_version: str,
) -> str:
    digest = hashlib.sha256()
    for part in (prompt_version, purpose, source_label, source_text):
        encoded = part.encode("utf-8")
        digest.update(str(len(encoded)).encode("ascii"))
        digest.update(b":")
        digest.update(encoded)
    return digest.hexdigest()


class SummaryCache:
    def __init__(
        self,
        database: Database = default_db,
        *,
        retention_minutes: int = 10080,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = database
        self._retention_s = max(int(retention_minutes), 1) * 60
        self._clock = clock
        self._ready_for: object | None = None
        self._writes = 0

    def _ensure(self) -> bool:
        if not self._db.configured:
            return False
        marker = (self._db.path, id(self._db))
        if self._ready_for != marker:
            with self._db.transaction() as conn:
                conn.executescript(_SCHEMA)
            self._ready_for = marker
        return True

    def get(self, key: str) -> str | None:
        try:
            if not self._ensure():
                return None
            rows = self._db.query(
                "SELECT summary FROM summary_cache "
                "WHERE cache_key = ? AND expires_at >= ?",
                (key, self._clock()),
            )
        except (sqlite3.Error, AssertionError) as exc:
            logger.warning("summary cache read failed error_type=%s", type(exc).__name__)
            return None
        return str(rows[0]["summary"]) if rows else None

    def put(self, key: str, summary: str) -> None:
        if not summary or not summary.strip():
            return
        try:
            if not self._ensure():
                return
            now = self._clock()
            self._db.execute(
                "INSERT OR REPLACE INTO summary_cache "
                "(cache_key, summary, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (key, summary, now, now + self._retention_s),
            )
            self._writes += 1
            if self._writes % _SWEEP_EVERY_N_WRITES == 0:
                self.sweep()
        except (sqlite3.Error, AssertionError) as exc:
            logger.warning("summary cache write failed error_type=%s", type(exc).__name__)

    def sweep(self) -> None:
        try:
            if not self._ensure():
                return
            self._db.execute(
                "DELETE FROM summary_cache WHERE expires_at < ?", (self._clock(),)
            )
        except (sqlite3.Error, AssertionError) as exc:
            logger.warning("summary cache sweep failed error_type=%s", type(exc).__name__)
