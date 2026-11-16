"""Owner-scoped persistence for the optional study agent."""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.db import db


@dataclass
class AgentConversation:
    conversation_id: str
    owner_id: str
    last_result_id: str | None = None
    last_document_id: str | None = None
    last_topics: list[str] = field(default_factory=list)
    updated_at: float = 0.0


@dataclass
class AgentResult:
    result_id: str
    owner_id: str
    document_id: str | None
    kind: str
    payload: dict[str, Any]
    created_at: float
    expires_at: float


@dataclass
class AgentFile:
    file_id: str
    owner_id: str
    path: Path
    name: str
    format: str
    created_at: float
    expires_at: float


class AgentStore:
    """Conversation, result references, and generated files with TTL cleanup."""

    def __init__(self) -> None:
        self._ttl_seconds = 3600.0
        self._conversations: dict[str, AgentConversation] = {}
        self._results: dict[str, AgentResult] = {}
        self._files: dict[str, AgentFile] = {}
        self._lock = threading.RLock()

    def configure(self, ttl_minutes: int, root: str | Path) -> None:
        self._ttl_seconds = max(float(ttl_minutes) * 60.0, 60.0)
        self._root = Path(root).resolve() / "agent-files"
        self._root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex

    def get_conversation(self, conversation_id: str, owner_id: str) -> AgentConversation | None:
        if not conversation_id or not owner_id:
            return None
        if db.configured:
            rows = db.query(
                "SELECT * FROM agent_conversations WHERE conversation_id = ? AND owner_id = ? AND updated_at > ?",
                (conversation_id, owner_id, time.time() - self._ttl_seconds),
            )
            if not rows:
                return None
            row = rows[0]
            return AgentConversation(row["conversation_id"], row["owner_id"], row["last_result_id"], row["last_document_id"], _json_list(row["last_topics_json"]), row["updated_at"])
        with self._lock:
            record = self._conversations.get(conversation_id)
            return (
                record
                if record and record.owner_id == owner_id
                and record.updated_at > time.time() - self._ttl_seconds
                else None
            )

    def save_conversation(self, record: AgentConversation) -> None:
        record.updated_at = time.time()
        if db.configured:
            db.execute("""INSERT INTO agent_conversations
                (conversation_id, owner_id, last_result_id, last_document_id, last_topics_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(conversation_id) DO UPDATE SET
                last_result_id=excluded.last_result_id, last_document_id=excluded.last_document_id,
                last_topics_json=excluded.last_topics_json, updated_at=excluded.updated_at
                WHERE agent_conversations.owner_id=excluded.owner_id""",
                (record.conversation_id, record.owner_id, record.last_result_id,
                 record.last_document_id, json.dumps(record.last_topics), record.updated_at))
        else:
            with self._lock:
                self._conversations[record.conversation_id] = record

    def save_result(self, owner_id: str, document_id: str | None, kind: str, payload: dict[str, Any]) -> AgentResult:
        now = time.time()
        record = AgentResult(
            self.new_id(), owner_id, document_id, kind, dict(payload), now,
            now + self._ttl_seconds,
        )
        if db.configured:
            db.execute(
                "INSERT INTO agent_results (result_id, owner_id, document_id, kind, payload_json, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (record.result_id, owner_id, document_id, kind,
                 json.dumps(record.payload), now, record.expires_at),
            )
        else:
            with self._lock:
                self._results[record.result_id] = record
        return record

    def get_result(self, result_id: str, owner_id: str) -> AgentResult | None:
        if not result_id or not owner_id:
            return None
        if db.configured:
            rows = db.query(
                "SELECT * FROM agent_results WHERE result_id = ? AND owner_id = ? AND expires_at > ?",
                (result_id, owner_id, time.time()),
            )
            if not rows:
                return None
            row = rows[0]
            try:
                payload = json.loads(row["payload_json"])
            except (TypeError, ValueError):
                payload = {}
            return AgentResult(
                row["result_id"], row["owner_id"], row["document_id"],
                row["kind"], payload if isinstance(payload, dict) else {},
                row["created_at"], row["expires_at"],
            )
        with self._lock:
            value = self._results.get(result_id)
            return value if value and value.owner_id == owner_id and value.expires_at > time.time() else None

    def save_file(self, owner_id: str, content: bytes, name: str, file_format: str) -> AgentFile:
        now = time.time()
        file_id = self.new_id()
        safe_name = Path(name).name or f"export.{file_format}"
        path = self._root / f"{file_id}.{file_format}"
        path.write_bytes(content)
        record = AgentFile(
            file_id, owner_id, path, safe_name, file_format, now,
            now + self._ttl_seconds,
        )
        if db.configured:
            db.execute(
                "INSERT INTO agent_files (file_id, owner_id, path, name, format, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (file_id, owner_id, str(path), safe_name, file_format, now,
                 record.expires_at),
            )
        else:
            with self._lock:
                self._files[file_id] = record
        return record

    def get_file(self, file_id: str, owner_id: str) -> AgentFile | None:
        if not file_id or not owner_id:
            return None
        if db.configured:
            rows = db.query(
                "SELECT * FROM agent_files WHERE file_id = ? AND owner_id = ? AND expires_at > ?",
                (file_id, owner_id, time.time()),
            )
            if not rows:
                return None
            row = rows[0]
            return AgentFile(
                row["file_id"], row["owner_id"], Path(row["path"]),
                row["name"], row["format"], row["created_at"],
                row["expires_at"],
            )
        with self._lock:
            value = self._files.get(file_id)
            return value if value and value.owner_id == owner_id and value.expires_at > time.time() else None

    def sweep(self) -> int:
        now = time.time()
        if db.configured:
            rows = db.query("SELECT path FROM agent_files WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM agent_files WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM agent_results WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM agent_conversations WHERE updated_at <= ?", (now - self._ttl_seconds,))
            paths = [Path(row["path"]) for row in rows]
        else:
            with self._lock:
                expired_files = [k for k, v in self._files.items() if v.expires_at <= now]
                expired_results = [k for k, v in self._results.items() if v.expires_at <= now]
                expired_conversations = [
                    k for k, v in self._conversations.items()
                    if v.updated_at <= now - self._ttl_seconds
                ]
                paths = [self._files[k].path for k in expired_files]
                for key in expired_files:
                    self._files.pop(key, None)
                for key in expired_results:
                    self._results.pop(key, None)
                for key in expired_conversations:
                    self._conversations.pop(key, None)
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        return len(paths)

    def clear(self) -> None:
        if db.configured:
            db.execute("DELETE FROM agent_files")
            db.execute("DELETE FROM agent_results")
            db.execute("DELETE FROM agent_conversations")
        with self._lock:
            self._conversations.clear()
            self._results.clear()
            self._files.clear()


def _json_list(value: str) -> list[str]:
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [item for item in data if isinstance(item, str)] if isinstance(data, list) else []


agent_store = AgentStore()
