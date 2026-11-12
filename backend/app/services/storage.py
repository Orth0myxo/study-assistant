from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.errors import ApiError
from app.services.db import db


@dataclass
class StoredDocument:
    document_id: str
    owner_id: str | None
    filename: str
    format: str
    size_bytes: int
    path: Path
    created_at: float
    expires_at: float
    page_count: int | None
    extraction_status: str
    text_extractable: bool
    section_count: int
    total_chars: int
    sections: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    ingest_status: str = "ready"
    ingest_progress: dict[str, Any] = field(default_factory=dict)
    ingest_error: dict[str, str] | None = None

    @property
    def available(self) -> bool:
        """Whether the original source file is still on disk."""
        try:
            return self.path.exists()
        except OSError:
            return False


class DocumentStore:
    def __init__(self) -> None:
        self._root: Path | None = None
        self._ttl_seconds = 3600.0
        self._records: dict[str, StoredDocument] = {}
        self._lock = threading.Lock()

    def configure(self, root: str | os.PathLike[str], ttl_minutes: int) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._ttl_seconds = float(ttl_minutes) * 60.0

    @property
    def root(self) -> Path:
        if self._root is None:
            raise RuntimeError("DocumentStore is not configured")
        return self._root

    def new_document_id(self) -> str:
        return uuid.uuid4().hex

    def save(
        self,
        document_id: str,
        filename: str,
        data: bytes,
        *,
        owner_id: str | None,
        format: str,
        page_count: int | None,
        extraction_status: str,
        text_extractable: bool,
        total_chars: int,
        sections: list[dict[str, Any]],
        warnings: list[dict[str, str]],
        pages: list[dict[str, Any]] | None = None,
        ingest_status: str = "ready",
        progress: dict[str, Any] | None = None,
        error: dict[str, str] | None = None,
    ) -> StoredDocument:
        safe_name = Path(filename).name or "upload"
        suffix = Path(safe_name).suffix.lower()
        path = self.root / f"{document_id}{suffix}"
        path.write_bytes(data)
        now = time.time()
        record = StoredDocument(
            document_id=document_id,
            owner_id=owner_id,
            filename=safe_name,
            format=format,
            size_bytes=len(data),
            path=path,
            created_at=now,
            expires_at=now + self._ttl_seconds,
            page_count=page_count,
            extraction_status=extraction_status,
            text_extractable=text_extractable,
            section_count=len(sections),
            total_chars=total_chars,
            sections=sections,
            warnings=warnings,
            ingest_status=ingest_status,
            ingest_progress=progress or {},
            ingest_error=error,
        )
        with self._lock:
            self._records[document_id] = record
        self._persist(record)
        self._replace_pages(document_id, pages or [])
        return record

    def create_pending(
        self,
        document_id: str,
        filename: str,
        data: bytes,
        *,
        owner_id: str | None,
        format: str,
    ) -> StoredDocument:
        """Store the raw file and a processing record before parsing."""
        return self.save(
            document_id,
            filename,
            data,
            owner_id=owner_id,
            format=format,
            page_count=None,
            extraction_status="pending",
            text_extractable=False,
            total_chars=0,
            sections=[],
            warnings=[],
            pages=[],
            ingest_status="processing",
            progress={"processed_units": 0, "total_units": 0},
        )

    def update_progress(
        self, document_id: str, processed: int, total: int, label: str | None = None
    ) -> None:
        with self._lock:
            record = self._records.get(document_id)
            if record is None or record.ingest_status != "processing":
                return
            record.ingest_progress = {
                "processed_units": int(processed),
                "total_units": int(total),
                "label": label,
            }
            self._persist(record)

    def complete_ingest(
        self,
        document_id: str,
        *,
        page_count: int | None,
        extraction_status: str,
        text_extractable: bool,
        total_chars: int,
        sections: list[dict[str, Any]],
        pages: list[dict[str, Any]],
        warnings: list[dict[str, str]],
        progress: dict[str, Any] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> StoredDocument | None:
        with self._lock:
            record = self._records.get(document_id)
            if record is None:
                return None
            record.page_count = page_count
            record.extraction_status = extraction_status
            record.text_extractable = text_extractable
            record.section_count = len(sections)
            record.total_chars = total_chars
            record.sections = sections
            record.warnings = warnings
            record.ingest_status = "ready"
            record.ingest_progress = progress or {
                "processed_units": len(pages),
                "total_units": len(pages),
            }
            record.ingest_error = None
            self._persist(record)
            self._replace_pages(document_id, pages)
            return record

    def fail_ingest(
        self, document_id: str, code: str, message: str
    ) -> StoredDocument | None:
        with self._lock:
            record = self._records.get(document_id)
            if record is None:
                return None
            record.ingest_status = "failed"
            record.ingest_error = {"code": code, "message": message}
            record.extraction_status = "no_text"
            record.text_extractable = False
            self._persist(record)
            return record

    def pages_for(self, document_id: str) -> list[dict[str, Any]]:
        if not db.configured:
            return []
        rows = db.query(
            "SELECT page_index, page_number, kind, text, source, structure_json "
            "FROM document_pages WHERE document_id = ? ORDER BY page_index",
            (document_id,),
        )
        return [
            {
                "index": int(row["page_index"]),
                "page_number": row["page_number"],
                "kind": row["kind"],
                "text": row["text"],
                "source": row["source"],
                "structure": json.loads(row["structure_json"] or "[]"),
            }
            for row in rows
        ]

    def pending_ids(self) -> list[str]:
        if not db.configured:
            return []
        rows = db.query(
            "SELECT document_id FROM documents WHERE ingest_status = 'processing'"
        )
        return [row["document_id"] for row in rows]

    def _replace_pages(
        self, document_id: str, pages: list[dict[str, Any]]
    ) -> None:
        if not db.configured:
            return
        rows = [
            (
                document_id,
                int(page.get("index", 0)),
                page.get("page_number"),
                page.get("kind") or "page",
                page.get("text") or "",
                page.get("source") or "native",
                json.dumps(page.get("structure") or []),
            )
            for page in pages
        ]
        with db.transaction() as conn:
            conn.execute(
                "DELETE FROM document_pages WHERE document_id = ?", (document_id,)
            )
            conn.executemany(
                "INSERT OR REPLACE INTO document_pages "
                "(document_id, page_index, page_number, kind, text, source, "
                "structure_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    def get(self, document_id: str) -> StoredDocument | None:
        with self._lock:
            record = self._records.get(document_id)
        if record is None:
            record = self._load_one(document_id)
        if record is None:
            return None
        if record.expires_at < time.time():
            self.delete(document_id)
            return None
        return record

    def get_for_owner(
        self, document_id: str, owner_id: str | None
    ) -> StoredDocument | None:
        """Owner-scoped lookup used by every API path.

        ``owner_id`` must be a real user id. Legacy rows have no owner and are
        therefore never returned.
        """
        if not owner_id:
            return None
        record = self.get(document_id)
        if record is None or record.owner_id != owner_id:
            return None
        return record

    def delete_for_owner(self, document_id: str, owner_id: str | None) -> bool:
        if not owner_id:
            return False
        record = self.get(document_id)
        if record is None or record.owner_id != owner_id:
            return False
        return self.delete(document_id)

    def delete(self, document_id: str) -> bool:
        with self._lock:
            record = self._records.pop(document_id, None)
        if db.configured:
            db.execute(
                "DELETE FROM document_pages WHERE document_id = ?", (document_id,)
            )
            db.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
        if record is None:
            return False
        try:
            record.path.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def sweep(self) -> int:
        now = time.time()
        with self._lock:
            expired = [
                doc_id
                for doc_id, record in self._records.items()
                if record.expires_at < now
            ]
        for doc_id in expired:
            self.delete(doc_id)

        removed = len(expired)
        with self._lock:
            known = {record.path.name for record in self._records.values()}
        try:
            for candidate in self.root.iterdir():
                if not candidate.is_file() or candidate.name in known:
                    continue
                if now - candidate.stat().st_mtime > self._ttl_seconds:
                    candidate.unlink(missing_ok=True)
                    removed += 1
        except OSError:
            pass
        return removed

    def load(self) -> int:
        """Hydrate records from persistence. Returns the number loaded."""
        if not db.configured:
            return 0
        rows = db.query("SELECT * FROM documents")
        loaded: dict[str, StoredDocument] = {}
        for row in rows:
            record = self._from_row(row)
            loaded[record.document_id] = record
        with self._lock:
            self._records = loaded
        return len(loaded)

    def reconcile(self) -> int:
        """Rewrite paths for persisted rows and drop orphan files with no row.

        Source files belonging to a known document are kept. Files under the
        store root that no persisted record references are unreachable and are
        removed. Documents whose source file is missing are kept with
        ``available == False`` so completed results stay readable while new
        processing is blocked.
        """
        if not db.configured:
            return 0
        rows = db.query("SELECT document_id, path FROM documents")
        removed = 0
        known_names: set[str] = set()
        for row in rows:
            known_names.add(Path(row["path"]).name)
        try:
            for candidate in self.root.iterdir():
                if not candidate.is_file() or candidate.name in known_names:
                    continue
                candidate.unlink(missing_ok=True)
                removed += 1
        except OSError:
            pass
        return removed

    def reload(self) -> int:
        """Simulate a restart: drop in-memory state and hydrate from persistence."""
        with self._lock:
            self._records = {}
        return self.load()

    def clear(self) -> None:
        with self._lock:
            records = list(self._records.values())
            self._records.clear()
        if db.configured:
            db.execute("DELETE FROM documents")
        for record in records:
            try:
                record.path.unlink(missing_ok=True)
            except OSError:
                pass

    def _load_one(self, document_id: str) -> StoredDocument | None:
        if not db.configured:
            return None
        rows = db.query(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        )
        if not rows:
            return None
        record = self._from_row(rows[0])
        with self._lock:
            self._records[document_id] = record
        return record

    def _persist(self, record: StoredDocument) -> None:
        if not db.configured:
            return
        db.execute(
            """
            INSERT OR REPLACE INTO documents (
                document_id, owner_id, filename, format, size_bytes, path,
                created_at, expires_at, page_count, extraction_status,
                text_extractable, section_count, total_chars, sections_json,
                warnings_json, ingest_status, ingest_progress_json,
                ingest_error_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.document_id,
                record.owner_id,
                record.filename,
                record.format,
                record.size_bytes,
                str(record.path),
                record.created_at,
                record.expires_at,
                record.page_count,
                record.extraction_status,
                1 if record.text_extractable else 0,
                record.section_count,
                record.total_chars,
                json.dumps(record.sections),
                json.dumps(record.warnings),
                record.ingest_status,
                json.dumps(record.ingest_progress),
                json.dumps(record.ingest_error) if record.ingest_error else None,
            ),
        )

    @staticmethod
    def _from_row(row: Any) -> StoredDocument:
        return StoredDocument(
            document_id=row["document_id"],
            owner_id=row["owner_id"],
            filename=row["filename"],
            format=row["format"],
            size_bytes=int(row["size_bytes"]),
            path=Path(row["path"]),
            created_at=float(row["created_at"]),
            expires_at=float(row["expires_at"]),
            page_count=row["page_count"],
            extraction_status=row["extraction_status"],
            text_extractable=bool(row["text_extractable"]),
            section_count=int(row["section_count"]),
            total_chars=int(row["total_chars"]),
            sections=json.loads(row["sections_json"] or "[]"),
            warnings=json.loads(row["warnings_json"] or "[]"),
            ingest_status=row["ingest_status"],
            ingest_progress=json.loads(row["ingest_progress_json"] or "{}"),
            ingest_error=(
                json.loads(row["ingest_error_json"])
                if row["ingest_error_json"]
                else None
            ),
        )


def require_ready_document(record: StoredDocument) -> StoredDocument:
    """Reject routes that would otherwise read empty, still-processing text."""
    if record.ingest_status == "processing":
        raise ApiError(
            "document_processing",
            "The document is still being read. Try again in a moment.",
            409,
        )
    if record.ingest_status == "failed":
        message = (
            record.ingest_error.get("message")
            if record.ingest_error
            else "The document could not be processed. Upload it again."
        )
        raise ApiError(
            "document_failed",
            message,
            409,
        )
    return record


storage = DocumentStore()
