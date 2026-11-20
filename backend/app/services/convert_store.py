"""Owner-scoped, short-lived file conversion jobs and output files."""

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
class ConversionJob:
    job_id: str
    owner_id: str
    conversion_id: str
    input_kind: str
    output_kind: str
    status: str = "queued"
    needs_ai: bool = False
    input_path: Path | None = None
    input_name: str = ""
    sheet: str | None = None
    file_name: str | None = None
    file_format: str | None = None
    file_path: Path | None = None
    size_bytes: int = 0
    table: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    error_details: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float = 0.0


class ConversionStore:
    def __init__(self) -> None:
        self._ttl_seconds = 3600.0
        self._root: Path | None = None
        self._memory: dict[str, ConversionJob] = {}
        self._lock = threading.RLock()

    def configure(self, temp_dir: str | Path, ttl_minutes: int) -> None:
        self._ttl_seconds = float(ttl_minutes) * 60.0
        self._root = Path(temp_dir).resolve() / "conversions"
        (self._root / "inputs").mkdir(parents=True, exist_ok=True)
        (self._root / "outputs").mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return uuid.uuid4().hex

    def input_path(self, job_id: str, suffix: str) -> Path:
        if self._root is None:
            raise RuntimeError("conversion storage is not configured")
        safe_suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
        return self._root / "inputs" / f"{job_id}{safe_suffix}"

    def output_path(self, job_id: str, file_format: str) -> Path:
        if self._root is None:
            raise RuntimeError("conversion storage is not configured")
        return self._root / "outputs" / f"{job_id}.{file_format}"

    def create(
        self,
        owner_id: str,
        *,
        job_id: str,
        conversion_id: str,
        input_kind: str,
        output_kind: str,
        needs_ai: bool,
        input_path: Path | None,
        input_name: str = "",
        sheet: str | None = None,
    ) -> ConversionJob:
        now = time.time()
        job = ConversionJob(
            job_id=job_id,
            owner_id=owner_id,
            conversion_id=conversion_id,
            input_kind=input_kind,
            output_kind=output_kind,
            needs_ai=needs_ai,
            input_path=input_path,
            input_name=input_name,
            sheet=sheet,
            created_at=now,
            updated_at=now,
            expires_at=now + self._ttl_seconds,
        )
        self.save(job)
        return job

    def save(self, job: ConversionJob) -> None:
        job.updated_at = time.time()
        if db.configured:
            db.execute(
                "INSERT OR REPLACE INTO conversion_jobs "
                "(job_id, owner_id, conversion_id, input_kind, output_kind, status, "
                "needs_ai, input_path, input_name, sheet, file_name, file_format, "
                "file_path, size_bytes, table_json, warnings_json, error_code, "
                "error_message, error_details_json, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job.job_id,
                    job.owner_id,
                    job.conversion_id,
                    job.input_kind,
                    job.output_kind,
                    job.status,
                    1 if job.needs_ai else 0,
                    str(job.input_path) if job.input_path else None,
                    job.input_name,
                    job.sheet,
                    job.file_name,
                    job.file_format,
                    str(job.file_path) if job.file_path else None,
                    job.size_bytes,
                    json.dumps(job.table, ensure_ascii=False),
                    json.dumps(job.warnings, ensure_ascii=False),
                    job.error_code,
                    job.error_message,
                    json.dumps(job.error_details, ensure_ascii=False),
                    job.created_at,
                    job.updated_at,
                    job.expires_at,
                ),
            )
            return
        with self._lock:
            self._memory[job.job_id] = job

    def get_for_owner(
        self, job_id: str, owner_id: str | None
    ) -> ConversionJob | None:
        if not job_id or not owner_id:
            return None
        if db.configured:
            rows = db.query(
                "SELECT * FROM conversion_jobs "
                "WHERE job_id = ? AND owner_id = ? AND expires_at > ?",
                (job_id, owner_id, time.time()),
            )
            return _row_to_job(rows[0]) if rows else None
        with self._lock:
            job = self._memory.get(job_id)
            if job is None or job.owner_id != owner_id or job.expires_at <= time.time():
                return None
            return job

    def finish(
        self,
        job_id: str,
        owner_id: str,
        *,
        data: bytes,
        file_name: str,
        file_format: str,
        warnings: list[str] | None = None,
    ) -> ConversionJob | None:
        job = self.get_for_owner(job_id, owner_id)
        if job is None:
            return None
        path = self.output_path(job_id, file_format)
        if job.file_path is not None and job.file_path != path:
            _unlink(job.file_path)
        path.write_bytes(data)
        job.status = "completed"
        job.file_name = Path(file_name).name
        job.file_format = file_format
        job.file_path = path
        job.size_bytes = len(data)
        job.warnings = list(warnings or job.warnings)
        job.error_code = None
        job.error_message = None
        job.error_details = {}
        self.save(job)
        self._remove_input(job)
        return job

    def finish_table(
        self,
        job_id: str,
        owner_id: str,
        *,
        table: dict[str, Any],
        warnings: list[str] | None = None,
    ) -> ConversionJob | None:
        job = self.get_for_owner(job_id, owner_id)
        if job is None:
            return None
        job.status = "completed"
        job.table = dict(table)
        job.warnings = list(warnings or job.warnings)
        self.save(job)
        self._remove_input(job)
        return job

    def fail(
        self,
        job_id: str,
        owner_id: str,
        *,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> ConversionJob | None:
        job = self.get_for_owner(job_id, owner_id)
        if job is None:
            return None
        job.status = "failed"
        job.error_code = code
        job.error_message = message
        job.error_details = dict(details or {})
        self.save(job)
        self._remove_input(job)
        return job

    def sweep(self) -> int:
        now = time.time()
        if db.configured:
            rows = db.query(
                "SELECT * FROM conversion_jobs WHERE expires_at <= ?", (now,)
            )
            db.execute("DELETE FROM conversion_jobs WHERE expires_at <= ?", (now,))
            jobs = [_row_to_job(row) for row in rows]
        else:
            with self._lock:
                jobs = [
                    job
                    for job in self._memory.values()
                    if job.expires_at <= now
                ]
                for job in jobs:
                    self._memory.pop(job.job_id, None)
        for job in jobs:
            self._remove_input(job)
            _unlink(job.file_path)
        return len(jobs)

    def clear(self) -> None:
        if db.configured:
            rows = db.query("SELECT * FROM conversion_jobs")
            jobs = [_row_to_job(row) for row in rows]
            db.execute("DELETE FROM conversion_jobs")
        else:
            with self._lock:
                jobs = list(self._memory.values())
                self._memory.clear()
        for job in jobs:
            self._remove_input(job)
            _unlink(job.file_path)

    def _remove_input(self, job: ConversionJob) -> None:
        _unlink(job.input_path)
        job.input_path = None


def _row_to_job(row: Any) -> ConversionJob:
    return ConversionJob(
        job_id=row["job_id"],
        owner_id=row["owner_id"],
        conversion_id=row["conversion_id"],
        input_kind=row["input_kind"],
        output_kind=row["output_kind"],
        status=row["status"],
        needs_ai=bool(row["needs_ai"]),
        input_path=Path(row["input_path"]) if row["input_path"] else None,
        input_name=row["input_name"] or "",
        sheet=row["sheet"],
        file_name=row["file_name"],
        file_format=row["file_format"],
        file_path=Path(row["file_path"]) if row["file_path"] else None,
        size_bytes=int(row["size_bytes"] or 0),
        table=_json_object(row["table_json"]),
        warnings=_json_list(row["warnings_json"]),
        error_code=row["error_code"],
        error_message=row["error_message"],
        error_details=_json_object(row["error_details_json"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        expires_at=float(row["expires_at"]),
    )


def _json_object(value: str) -> dict[str, Any]:
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _json_list(value: str) -> list[str]:
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    return [str(item) for item in data] if isinstance(data, list) else []


def _unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


conversion_jobs = ConversionStore()
