"""Owner-scoped, short-lived photo-to-table results."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.db import db


@dataclass
class TableResultRecord:
    result_id: str
    owner_id: str
    source_id: str
    image_index: int
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    expires_at: float = 0.0


class TableResultStore:
    def __init__(self) -> None:
        self._ttl_seconds = 3600.0
        self._memory: dict[str, TableResultRecord] = {}
        self._lock = threading.RLock()

    def configure(self, ttl_minutes: int) -> None:
        self._ttl_seconds = float(ttl_minutes) * 60.0

    def save(
        self,
        owner_id: str,
        *,
        source_id: str,
        image_index: int,
        payload: dict[str, Any],
    ) -> TableResultRecord:
        now = time.time()
        record = TableResultRecord(
            uuid.uuid4().hex,
            owner_id,
            source_id,
            image_index,
            dict(payload),
            now,
            now + self._ttl_seconds,
        )
        if db.configured:
            db.execute(
                "INSERT INTO table_results "
                "(result_id, owner_id, source_id, image_index, payload_json, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    record.result_id,
                    record.owner_id,
                    record.source_id,
                    record.image_index,
                    json.dumps(record.payload, ensure_ascii=False),
                    record.created_at,
                    record.expires_at,
                ),
            )
        else:
            with self._lock:
                self._memory[record.result_id] = record
        return record

    def get_for_owner(
        self, result_id: str, owner_id: str | None
    ) -> TableResultRecord | None:
        if not result_id or not owner_id:
            return None
        if db.configured:
            rows = db.query(
                "SELECT * FROM table_results WHERE result_id = ? AND owner_id = ? AND expires_at > ?",
                (result_id, owner_id, time.time()),
            )
            if not rows:
                return None
            row = rows[0]
            return TableResultRecord(
                row["result_id"],
                row["owner_id"],
                row["source_id"],
                int(row["image_index"]),
                _json_object(row["payload_json"]),
                row["created_at"],
                row["expires_at"],
            )
        with self._lock:
            record = self._memory.get(result_id)
            return (
                record
                if record
                and record.owner_id == owner_id
                and record.expires_at > time.time()
                else None
            )

    def delete_for_owner(self, result_id: str, owner_id: str | None) -> bool:
        if not result_id or not owner_id:
            return False
        if self.get_for_owner(result_id, owner_id) is None:
            return False
        if db.configured:
            db.execute(
                "DELETE FROM table_results WHERE result_id = ? AND owner_id = ?",
                (result_id, owner_id),
            )
        else:
            with self._lock:
                self._memory.pop(result_id, None)
        return True

    def sweep(self) -> int:
        now = time.time()
        if db.configured:
            rows = db.query(
                "SELECT result_id FROM table_results WHERE expires_at <= ?", (now,)
            )
            db.execute("DELETE FROM table_results WHERE expires_at <= ?", (now,))
            return len(rows)
        with self._lock:
            expired = [
                key
                for key, value in self._memory.items()
                if value.expires_at <= now
            ]
            for key in expired:
                self._memory.pop(key, None)
        return len(expired)

    def clear(self) -> None:
        if db.configured:
            db.execute("DELETE FROM table_results")
        with self._lock:
            self._memory.clear()


def _json_object(value: str) -> dict[str, Any]:
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


table_results = TableResultStore()
