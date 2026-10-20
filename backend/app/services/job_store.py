from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.services.db import db


def _parse_epoch(created_at: str | None) -> float:
    if not created_at:
        return time.time()
    try:
        return datetime.fromisoformat(created_at.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


class JobStore:
    """Study job registry persisted in SQLite (falls back to memory only)."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}
        self._expires_at: dict[str, float] = {}
        self._ttl_seconds = 3600.0
        self._lock = threading.Lock()

    def configure(self, ttl_minutes: int) -> None:
        self._ttl_seconds = float(ttl_minutes) * 60.0

    def new_job_id(self) -> str:
        return uuid.uuid4().hex

    def put(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        expires_at = time.time() + self._ttl_seconds
        with self._lock:
            self._records[job_id] = payload
            self._expires_at[job_id] = expires_at
        self._persist(job_id, payload, expires_at)
        return payload

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            payload = self._records.get(job_id)
            expires_at = self._expires_at.get(job_id)
        if payload is None:
            payload = self._load_one(job_id)
            with self._lock:
                expires_at = self._expires_at.get(job_id)
            if payload is None:
                return None
        if expires_at is not None and expires_at < time.time():
            self.delete(job_id)
            return None
        return payload

    def list_recent(self, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
        if not db.configured:
            payloads = list(self._records.values())
            payloads.sort(key=lambda item: item.get("created_at") or "", reverse=True)
            return payloads[offset : offset + limit]
        rows = db.query(
            """
            SELECT * FROM study_jobs
            ORDER BY created_epoch DESC, job_id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [self._payload_from_row(row) for row in rows]

    def list_for_owner(
        self, owner_id: str | None, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """History for one user only. Legacy ownerless jobs never match."""
        if not owner_id:
            return []
        if not db.configured:
            payloads = [
                payload
                for payload in self._records.values()
                if payload.get("owner_id") == owner_id
            ]
            payloads.sort(key=lambda item: item.get("created_at") or "", reverse=True)
            return payloads[offset : offset + limit]
        rows = db.query(
            """
            SELECT * FROM study_jobs
            WHERE owner_id = ?
            ORDER BY created_epoch DESC, job_id DESC
            LIMIT ? OFFSET ?
            """,
            (owner_id, limit, offset),
        )
        return [self._payload_from_row(row) for row in rows]

    def get_for_owner(
        self, job_id: str, owner_id: str | None
    ) -> dict[str, Any] | None:
        if not owner_id:
            return None
        payload = self.get(job_id)
        if payload is None or payload.get("owner_id") != owner_id:
            return None
        return payload

    def delete(self, job_id: str) -> bool:
        with self._lock:
            self._expires_at.pop(job_id, None)
            removed = self._records.pop(job_id, None) is not None
        if db.configured:
            db.execute("DELETE FROM study_jobs WHERE job_id = ?", (job_id,))
        return removed

    def sweep(self) -> int:
        now = time.time()
        with self._lock:
            expired = [
                job_id
                for job_id, expires_at in self._expires_at.items()
                if expires_at < now
            ]
        for job_id in expired:
            self.delete(job_id)
        return len(expired)

    def load(self) -> int:
        """Hydrate jobs from persistence. Returns the number loaded."""
        if not db.configured:
            return 0
        rows = db.query("SELECT * FROM study_jobs")
        records: dict[str, dict[str, Any]] = {}
        expires: dict[str, float] = {}
        for row in rows:
            records[row["job_id"]] = self._payload_from_row(row)
            expires[row["job_id"]] = float(row["expires_at"])
        with self._lock:
            self._records = records
            self._expires_at = expires
        return len(records)

    def reload(self) -> int:
        """Simulate a restart: drop in-memory state and hydrate from persistence."""
        with self._lock:
            self._records = {}
            self._expires_at = {}
        return self.load()

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._expires_at.clear()
        if db.configured:
            db.execute("DELETE FROM study_jobs")

    def _load_one(self, job_id: str) -> dict[str, Any] | None:
        if not db.configured:
            return None
        rows = db.query("SELECT * FROM study_jobs WHERE job_id = ?", (job_id,))
        if not rows:
            return None
        payload = self._payload_from_row(rows[0])
        with self._lock:
            self._records[job_id] = payload
            self._expires_at[job_id] = float(rows[0]["expires_at"])
        return payload

    def _persist(
        self, job_id: str, payload: dict[str, Any], expires_at: float
    ) -> None:
        if not db.configured:
            return
        created_at = payload.get("created_at") or ""
        db.execute(
            """
            INSERT OR REPLACE INTO study_jobs (
                job_id, owner_id, document_id, filename, format, mode, status,
                created_at, updated_at, created_epoch, expires_at, page_count,
                item_count, items_json, document_summary, warnings_json,
                error_json, progress_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                payload.get("owner_id"),
                payload.get("document_id") or "",
                payload.get("filename") or "",
                payload.get("format") or "",
                payload.get("mode") or "",
                payload.get("status") or "",
                created_at,
                payload.get("updated_at") or created_at,
                _parse_epoch(created_at),
                expires_at,
                payload.get("page_count"),
                int(payload.get("item_count") or 0),
                json.dumps(payload.get("items") or []),
                payload.get("document_summary"),
                json.dumps(payload.get("warnings") or []),
                json.dumps(payload.get("error")) if payload.get("error") else None,
                json.dumps(payload.get("progress") or {}),
            ),
        )

    @staticmethod
    def _payload_from_row(row: Any) -> dict[str, Any]:
        items = json.loads(row["items_json"] or "[]")
        return {
            "job_id": row["job_id"],
            "owner_id": row["owner_id"],
            "document_id": row["document_id"],
            "filename": row["filename"],
            "format": row["format"],
            "mode": row["mode"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "page_count": row["page_count"],
            "item_count": int(row["item_count"]),
            "items": items,
            "document_summary": row["document_summary"],
            "warnings": json.loads(row["warnings_json"] or "[]"),
            "error": json.loads(row["error_json"]) if row["error_json"] else None,
            "progress": json.loads(row["progress_json"] or "{}"),
        }


jobs = JobStore()
