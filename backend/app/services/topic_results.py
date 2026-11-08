from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.db import db


@dataclass
class TopicStudyResultRecord:
    """A generated topic study output persisted for history and export.

    The full response payload is stored as JSON so reopening a result never
    re-runs matching or AI generation. Listing columns are duplicated for
    cheap history queries.
    """

    result_id: str
    source_id: str
    owner_id: str
    document_id: str | None
    document_filename: str | None
    document_format: str | None
    mode: str
    ai_scope: str
    status: str
    topic_count: int
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float = 0.0


class TopicResultStore:
    """Owner-scoped persistence for topic study output (SQLite + memory fallback)."""

    def __init__(self) -> None:
        self._ttl_seconds = 3600.0
        self._memory: dict[str, TopicStudyResultRecord] = {}
        self._lock = threading.RLock()

    def configure(self, ttl_minutes: int) -> None:
        self._ttl_seconds = float(ttl_minutes) * 60.0

    def new_result_id(self) -> str:
        return uuid.uuid4().hex

    def save(
        self,
        owner_id: str,
        *,
        source_id: str,
        document_id: str | None,
        document_filename: str | None,
        document_format: str | None,
        mode: str,
        ai_scope: str,
        status: str,
        topic_count: int,
        payload: dict[str, Any],
    ) -> TopicStudyResultRecord:
        result_id = self.new_result_id()
        now = time.time()
        created_at = _iso(now)
        expires_at = now + self._ttl_seconds
        stored = dict(payload)
        stored["result_id"] = result_id
        stored["created_at"] = created_at
        stored["expires_at"] = _iso(expires_at)
        record = TopicStudyResultRecord(
            result_id=result_id,
            source_id=source_id,
            owner_id=owner_id,
            document_id=document_id,
            document_filename=document_filename,
            document_format=document_format,
            mode=mode,
            ai_scope=ai_scope,
            status=status,
            topic_count=topic_count,
            payload=stored,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        if db.configured:
            db.execute(
                """
                INSERT INTO topic_study_results (
                    result_id, source_id, owner_id, document_id,
                    document_filename, document_format, mode, ai_scope,
                    status, topic_count, payload_json, created_at,
                    updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.result_id,
                    record.source_id,
                    record.owner_id,
                    record.document_id,
                    record.document_filename,
                    record.document_format,
                    record.mode,
                    record.ai_scope,
                    record.status,
                    record.topic_count,
                    json.dumps(record.payload),
                    record.created_at,
                    record.updated_at,
                    record.expires_at,
                ),
            )
        else:
            with self._lock:
                self._memory[result_id] = record
        return record

    def get_for_owner(
        self, result_id: str, owner_id: str | None
    ) -> TopicStudyResultRecord | None:
        if not owner_id or not result_id:
            return None
        if db.configured:
            rows = db.query(
                "SELECT * FROM topic_study_results WHERE result_id = ?",
                (result_id,),
            )
            record = _from_row(rows[0]) if rows else None
        else:
            with self._lock:
                record = self._memory.get(result_id)
        if record is None or record.owner_id != owner_id:
            return None
        if record.expires_at < time.time():
            self.delete(result_id)
            return None
        return record

    def list_for_owner(
        self, owner_id: str | None, limit: int = 20, offset: int = 0
    ) -> list[TopicStudyResultRecord]:
        if not owner_id:
            return []
        now = time.time()
        if db.configured:
            rows = db.query(
                """
                SELECT * FROM topic_study_results
                 WHERE owner_id = ? AND expires_at > ?
                 ORDER BY created_at DESC, result_id DESC
                 LIMIT ? OFFSET ?
                """,
                (owner_id, now, limit, offset),
            )
            return [_from_row(row) for row in rows]
        with self._lock:
            records = [
                record
                for record in self._memory.values()
                if record.owner_id == owner_id and record.expires_at > now
            ]
        records.sort(key=lambda item: (item.created_at, item.result_id), reverse=True)
        return records[offset : offset + limit]

    def list_for_source(
        self, source_id: str, owner_id: str | None, limit: int = 20
    ) -> list[TopicStudyResultRecord]:
        if not owner_id or not source_id:
            return []
        now = time.time()
        if db.configured:
            rows = db.query(
                """
                SELECT * FROM topic_study_results
                 WHERE source_id = ? AND owner_id = ? AND expires_at > ?
                 ORDER BY created_at DESC, result_id DESC
                 LIMIT ?
                """,
                (source_id, owner_id, now, limit),
            )
            return [_from_row(row) for row in rows]
        with self._lock:
            records = [
                record
                for record in self._memory.values()
                if record.source_id == source_id
                and record.owner_id == owner_id
                and record.expires_at > now
            ]
        records.sort(key=lambda item: (item.created_at, item.result_id), reverse=True)
        return records[:limit]

    def delete_for_owner(self, result_id: str, owner_id: str | None) -> bool:
        record = self.get_for_owner(result_id, owner_id)
        if record is None:
            return False
        return self.delete(result_id)

    def delete(self, result_id: str) -> bool:
        if not result_id:
            return False
        removed = False
        if db.configured:
            with db.transaction() as conn:
                cursor = conn.execute(
                    "DELETE FROM topic_study_results WHERE result_id = ?",
                    (result_id,),
                )
                removed = cursor.rowcount > 0
        with self._lock:
            if self._memory.pop(result_id, None) is not None:
                removed = True
        return removed

    def sweep(self) -> int:
        now = time.time()
        removed = 0
        if db.configured:
            rows = db.query(
                "SELECT result_id FROM topic_study_results WHERE expires_at < ?",
                (now,),
            )
            for row in rows:
                if self.delete(row["result_id"]):
                    removed += 1
            return removed
        with self._lock:
            expired = [
                result_id
                for result_id, record in self._memory.items()
                if record.expires_at < now
            ]
        for result_id in expired:
            if self.delete(result_id):
                removed += 1
        return removed

    def clear(self) -> None:
        if db.configured:
            db.execute("DELETE FROM topic_study_results")
        with self._lock:
            self._memory.clear()


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone

    return (
        datetime.fromtimestamp(epoch, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _from_row(row: Any) -> TopicStudyResultRecord:
    return TopicStudyResultRecord(
        result_id=row["result_id"],
        source_id=row["source_id"],
        owner_id=row["owner_id"],
        document_id=row["document_id"],
        document_filename=row["document_filename"],
        document_format=row["document_format"],
        mode=row["mode"],
        ai_scope=row["ai_scope"],
        status=row["status"],
        topic_count=int(row["topic_count"]),
        payload=json.loads(row["payload_json"] or "{}"),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        expires_at=float(row["expires_at"]),
    )


topic_results = TopicResultStore()
