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
    last_source_id: str | None = None
    last_source_kind: str | None = None
    memory: list[dict[str, str]] = field(default_factory=list)


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


@dataclass
class AgentPendingTurn:
    pending_id: str
    owner_id: str
    conversation_id: str
    messages: list[dict[str, Any]]
    outputs: list[dict[str, Any]]
    warnings: list[str]
    model_calls: int
    tool_calls: int
    estimated_tokens: int
    call: dict[str, Any]
    created_at: float
    expires_at: float
    tool_names: list[str] = field(default_factory=list)
    attachments: list[dict[str, str]] = field(default_factory=list)


@dataclass
class AgentExportJob:
    job_id: str
    owner_id: str
    result_id: str
    format: str
    status: str
    file_id: str | None
    name: str | None
    error_code: str | None
    error_message: str | None
    details: dict[str, Any]
    created_at: float
    updated_at: float
    expires_at: float


class AgentStore:
    """Conversation, result references, and generated files with TTL cleanup."""

    def __init__(self) -> None:
        self._ttl_seconds = 3600.0
        self._conversations: dict[str, AgentConversation] = {}
        self._results: dict[str, AgentResult] = {}
        self._files: dict[str, AgentFile] = {}
        self._export_jobs: dict[str, AgentExportJob] = {}
        self._pending: dict[str, AgentPendingTurn] = {}
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
            return AgentConversation(
                conversation_id=row["conversation_id"],
                owner_id=row["owner_id"],
                last_result_id=row["last_result_id"],
                last_document_id=row["last_document_id"],
                last_topics=_json_list(row["last_topics_json"]),
                updated_at=row["updated_at"],
                last_source_id=row["last_source_id"],
                last_source_kind=row["last_source_kind"],
                memory=_json_memory(row["memory_json"]),
            )
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
                (conversation_id, owner_id, last_result_id, last_document_id, last_topics_json, updated_at, last_source_id, last_source_kind, memory_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(conversation_id) DO UPDATE SET
                last_result_id=excluded.last_result_id, last_document_id=excluded.last_document_id,
                last_topics_json=excluded.last_topics_json, updated_at=excluded.updated_at,
                last_source_id=excluded.last_source_id, last_source_kind=excluded.last_source_kind,
                memory_json=excluded.memory_json
                WHERE agent_conversations.owner_id=excluded.owner_id""",
                (record.conversation_id, record.owner_id, record.last_result_id,
                 record.last_document_id, json.dumps(record.last_topics), record.updated_at,
                 record.last_source_id, record.last_source_kind,
                 json.dumps(record.memory, ensure_ascii=False)))
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

    def save_pending(
        self,
        owner_id: str,
        conversation_id: str,
        *,
        messages: list[dict[str, Any]],
        outputs: list[dict[str, Any]],
        warnings: list[str],
        model_calls: int,
        tool_calls: int,
        estimated_tokens: int,
        call: dict[str, Any],
        ttl_seconds: float,
        tool_names: list[str] | None = None,
        attachments: list[dict[str, str]] | None = None,
    ) -> AgentPendingTurn:
        now = time.time()
        record = AgentPendingTurn(
            self.new_id(),
            owner_id,
            conversation_id,
            [dict(message) for message in messages],
            list(outputs),
            list(warnings),
            int(model_calls),
            int(tool_calls),
            int(estimated_tokens),
            dict(call),
            now,
            now + max(float(ttl_seconds), 0.0),
            list(tool_names or []),
            [dict(item) for item in attachments or []],
        )
        with self._lock:
            self._pending[record.pending_id] = record
        return record

    def get_pending(self, pending_id: str, owner_id: str) -> AgentPendingTurn | None:
        if not pending_id or not owner_id:
            return None
        with self._lock:
            record = self._pending.get(pending_id)
            return (
                record
                if record
                and record.owner_id == owner_id
                and record.expires_at > time.time()
                else None
            )

    def delete_pending(self, pending_id: str) -> None:
        with self._lock:
            self._pending.pop(pending_id, None)

    def save_export_job(
        self, owner_id: str, result_id: str, file_format: str
    ) -> AgentExportJob:
        now = time.time()
        record = AgentExportJob(
            self.new_id(), owner_id, result_id, file_format, "queued",
            None, None, None, None, {}, now, now, now + self._ttl_seconds,
        )
        if db.configured:
            db.execute(
                "INSERT INTO agent_export_jobs "
                "(job_id, owner_id, result_id, format, status, file_id, name, "
                "error_code, error_message, details_json, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (record.job_id, owner_id, result_id, file_format, record.status,
                 None, None, None, None, "{}", now, now, record.expires_at),
            )
        else:
            with self._lock:
                self._export_jobs[record.job_id] = record
        return record

    def get_export_job(
        self, job_id: str, owner_id: str
    ) -> AgentExportJob | None:
        if not job_id or not owner_id:
            return None
        if db.configured:
            rows = db.query(
                "SELECT * FROM agent_export_jobs "
                "WHERE job_id = ? AND owner_id = ? AND expires_at > ?",
                (job_id, owner_id, time.time()),
            )
            if not rows:
                return None
            return _export_job_from_row(rows[0])
        with self._lock:
            record = self._export_jobs.get(job_id)
            return (
                record
                if record and record.owner_id == owner_id
                and record.expires_at > time.time()
                else None
            )

    def update_export_job(self, record: AgentExportJob) -> None:
        record.updated_at = time.time()
        if db.configured:
            db.execute(
                "UPDATE agent_export_jobs SET status = ?, file_id = ?, name = ?, "
                "error_code = ?, error_message = ?, details_json = ?, updated_at = ? "
                "WHERE job_id = ? AND owner_id = ?",
                (record.status, record.file_id, record.name, record.error_code,
                 record.error_message, json.dumps(record.details), record.updated_at,
                 record.job_id, record.owner_id),
            )
        else:
            with self._lock:
                self._export_jobs[record.job_id] = record

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
            db.execute("DELETE FROM agent_export_jobs WHERE expires_at <= ?", (now,))
            db.execute("DELETE FROM agent_conversations WHERE updated_at <= ?", (now - self._ttl_seconds,))
            paths = [Path(row["path"]) for row in rows]
        else:
            with self._lock:
                expired_files = [k for k, v in self._files.items() if v.expires_at <= now]
                expired_results = [k for k, v in self._results.items() if v.expires_at <= now]
                expired_export_jobs = [
                    k for k, v in self._export_jobs.items() if v.expires_at <= now
                ]
                expired_conversations = [
                    k for k, v in self._conversations.items()
                    if v.updated_at <= now - self._ttl_seconds
                ]
                paths = [self._files[k].path for k in expired_files]
                for key in expired_files:
                    self._files.pop(key, None)
                for key in expired_results:
                    self._results.pop(key, None)
                for key in expired_export_jobs:
                    self._export_jobs.pop(key, None)
                for key in expired_conversations:
                    self._conversations.pop(key, None)
                expired_pending = [
                    k for k, v in self._pending.items() if v.expires_at <= now
                ]
                for key in expired_pending:
                    self._pending.pop(key, None)
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
            db.execute("DELETE FROM agent_export_jobs")
            db.execute("DELETE FROM agent_conversations")
        with self._lock:
            self._conversations.clear()
            self._results.clear()
            self._files.clear()
            self._export_jobs.clear()
            self._pending.clear()


def _export_job_from_row(row: dict[str, Any]) -> AgentExportJob:
    try:
        details = json.loads(row["details_json"])
    except (TypeError, ValueError):
        details = {}
    return AgentExportJob(
        row["job_id"], row["owner_id"], row["result_id"], row["format"],
        row["status"], row["file_id"], row["name"], row["error_code"],
        row["error_message"], details if isinstance(details, dict) else {},
        row["created_at"], row["updated_at"], row["expires_at"],
    )


def _json_list(value: str) -> list[str]:
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [item for item in data if isinstance(item, str)] if isinstance(data, list) else []


def _json_memory(value: str) -> list[dict[str, str]]:
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    memory: list[dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role in {"user", "assistant"} and isinstance(content, str) and content:
            memory.append({"role": role, "content": content})
    return memory


agent_store = AgentStore()
