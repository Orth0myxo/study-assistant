from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.errors import ApiError
from app.services.db import db


@dataclass
class TopicImageRecord:
    index: int
    filename: str
    content_type: str
    size_bytes: int
    width: int
    height: int
    path: Path
    ocr_status: str
    text: str
    warnings: list[dict[str, str]] = field(default_factory=list)
    transcript_source: str = "ocr"
    created_at: float = 0.0


@dataclass
class TopicSourceRecord:
    source_id: str
    owner_id: str
    document_id: str | None
    document_filename: str | None
    document_format: str | None
    document_page_count: int | None
    status: str
    image_count: int
    jottings_text: str
    total_chars: int
    topics: list[str]
    warnings: list[dict[str, str]]
    images: list[TopicImageRecord]
    created_at: float
    updated_at: float
    expires_at: float
    version: int = 0


@dataclass
class TopicImageInput:
    filename: str
    content_type: str
    data: bytes
    width: int
    height: int
    ocr_status: str
    text: str
    warnings: list[dict[str, str]] = field(default_factory=list)
    transcript_source: str = "ocr"


class _StaleWrite(Exception):
    """Raised when an optimistic update lost a race and must be retried."""


_MANUAL_TRANSCRIPT_WARNING = {
    "code": "transcript_manually_edited",
    "message": (
        "This transcript was entered or corrected manually; OCR warnings no "
        "longer apply."
    ),
}


def _manual_warnings() -> list[dict[str, str]]:
    return [dict(_MANUAL_TRANSCRIPT_WARNING)]


def _image_suffix(content_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
    }.get(content_type, ".img")


def _source_status(
    images: list[TopicImageRecord], jottings_text: str
) -> str:
    texts = [image.text.strip() for image in images]
    if jottings_text.strip():
        texts.append(jottings_text.strip())
    with_text = sum(1 for text in texts if text)
    if with_text == 0:
        return "no_text"
    if with_text == len(texts):
        return "ok"
    return "partial"


def _source_warnings(
    images: list[TopicImageRecord],
    jottings_text: str,
    existing: list[dict[str, str]],
) -> list[dict[str, str]]:
    warnings = [
        warning
        for warning in existing
        if warning.get("code")
        not in {"ocr_incomplete", "ocr_handwriting_unreliable", "no_jottings"}
    ]
    if images:
        warnings.append(
            {
                "code": "ocr_handwriting_unreliable",
                "message": (
                    "Handwriting recognition is unreliable. Review and correct "
                    "every transcript before extracting topics."
                ),
            }
        )
    if any(not image.text.strip() for image in images):
        warnings.append(
            {
                "code": "ocr_incomplete",
                "message": (
                    "One or more note images have no transcript yet. Review or "
                    "edit the recognized text before extracting topics."
                ),
            }
        )
    if not jottings_text.strip() and not images:
        warnings.append(
            {
                "code": "no_jottings",
                "message": "Add typed jottings or at least one note photo.",
            }
        )
    return warnings


class TopicSourceStore:
    """Owner-scoped topic sources persisted in SQLite and on the temp disk.

    Image bytes are written below ``TEMP_DIR/topic_sources/<source_id>/`` and
    the OCR transcript is stored in SQLite so it can be reviewed and edited
    without re-running OCR. Nothing in this store is reachable without an exact
    owner match.
    """

    def __init__(self) -> None:
        self._root: Path | None = None
        self._ttl_seconds = 3600.0
        self._max_sources_per_user = 20
        self._memory: dict[str, TopicSourceRecord] = {}
        self._lock = threading.RLock()

    def configure(
        self,
        root: str | Path,
        ttl_minutes: int,
        max_sources_per_user: int = 20,
    ) -> None:
        base = Path(root).resolve() / "topic_sources"
        base.mkdir(parents=True, exist_ok=True)
        self._root = base
        self._ttl_seconds = float(ttl_minutes) * 60.0
        self._max_sources_per_user = max(int(max_sources_per_user), 0)

    @property
    def root(self) -> Path:
        if self._root is None:
            raise RuntimeError("TopicSourceStore is not configured")
        return self._root

    def new_source_id(self) -> str:
        return uuid.uuid4().hex

    def save(
        self,
        owner_id: str,
        *,
        document: dict[str, Any] | None,
        images: list[TopicImageInput],
        jottings_text: str = "",
    ) -> TopicSourceRecord:
        source_id = self.new_source_id()
        directory = self.root / source_id
        directory.mkdir(parents=True, exist_ok=False)
        now = time.time()

        records: list[TopicImageRecord] = []
        try:
            for index, image in enumerate(images):
                path = directory / f"{index:03d}{_image_suffix(image.content_type)}"
                path.write_bytes(image.data)
                records.append(
                    TopicImageRecord(
                        index=index,
                        filename=image.filename,
                        content_type=image.content_type,
                        size_bytes=len(image.data),
                        width=image.width,
                        height=image.height,
                        path=path,
                        ocr_status=image.ocr_status,
                        text=image.text,
                        warnings=list(image.warnings),
                        transcript_source=image.transcript_source,
                        created_at=now,
                    )
                )

            record = TopicSourceRecord(
                source_id=source_id,
                owner_id=owner_id,
                document_id=(document or {}).get("document_id"),
                document_filename=(document or {}).get("filename"),
                document_format=(document or {}).get("format"),
                document_page_count=(document or {}).get("page_count"),
                status=_source_status(records, jottings_text),
                image_count=len(records),
                jottings_text=jottings_text,
                total_chars=len(jottings_text)
                + sum(len(image.text) for image in records),
                topics=[],
                warnings=_source_warnings(records, jottings_text, []),
                images=records,
                created_at=now,
                updated_at=now,
                expires_at=now + self._ttl_seconds,
                version=0,
            )
            if db.configured:
                with db.transaction(immediate=True) as conn:
                    active = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM topic_sources "
                            "WHERE owner_id = ? AND expires_at > ?",
                            (owner_id, now),
                        ).fetchone()[0]
                    )
                    if active >= self._max_sources_per_user:
                        raise ApiError(
                            "topic_source_limit_reached",
                            "The per-user topic source limit has been reached. "
                            "Delete an existing source and try again.",
                            429,
                            {"max_sources": self._max_sources_per_user},
                        )
                    self._insert_source(conn, record)
            else:
                with self._lock:
                    active = sum(
                        1
                        for existing in self._memory.values()
                        if existing.owner_id == owner_id
                        and existing.expires_at > now
                    )
                    if active >= self._max_sources_per_user:
                        raise ApiError(
                            "topic_source_limit_reached",
                            "The per-user topic source limit has been reached. "
                            "Delete an existing source and try again.",
                            429,
                            {"max_sources": self._max_sources_per_user},
                        )
                    self._memory[source_id] = record
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return record

    def get_for_owner(
        self, source_id: str, owner_id: str | None
    ) -> TopicSourceRecord | None:
        if not owner_id:
            return None
        record = self._get(source_id)
        if record is None:
            return None
        if record.owner_id != owner_id:
            return None
        if record.expires_at < time.time():
            return None
        return record

    def list_for_owner(
        self, owner_id: str | None, limit: int = 20, offset: int = 0
    ) -> list[TopicSourceRecord]:
        """Recent topic sources for one user, newest first."""
        if not owner_id:
            return []
        now = time.time()
        if db.configured:
            rows = db.query(
                """
                SELECT * FROM topic_sources
                 WHERE owner_id = ? AND expires_at > ?
                 ORDER BY created_at DESC, source_id DESC
                 LIMIT ? OFFSET ?
                """,
                (owner_id, now, limit, offset),
            )
            records: list[TopicSourceRecord] = []
            for row in rows:
                record = self._get(row["source_id"])
                if record is not None:
                    records.append(record)
            return records
        with self._lock:
            records = [
                record
                for record in self._memory.values()
                if record.owner_id == owner_id and record.expires_at > now
            ]
        records.sort(key=lambda item: (item.created_at, item.source_id), reverse=True)
        return records[offset : offset + limit]

    def delete_for_owner(
        self, source_id: str, owner_id: str | None
    ) -> bool:
        if not owner_id:
            return False
        record = self._get(source_id)
        if record is None or record.owner_id != owner_id:
            return False
        return self.delete(source_id)

    def update_for_owner(
        self,
        source_id: str,
        owner_id: str | None,
        *,
        topics: list[str] | None = None,
        transcripts: dict[int, str] | None = None,
        jottings_text: str | None = None,
    ) -> TopicSourceRecord | None:
        if not owner_id:
            return None

        if not db.configured:
            with self._lock:
                record = self._memory.get(source_id)
                if record is None or record.owner_id != owner_id:
                    return None
                if record.expires_at < time.time():
                    return None
                self._apply_update(record, topics, transcripts, jottings_text)
                record.version += 1
                self._memory[source_id] = record
                return record

        for _ in range(5):
            record = self._get(source_id)
            if record is None or record.owner_id != owner_id:
                return None
            if record.expires_at < time.time():
                return None
            self._apply_update(record, topics, transcripts, jottings_text)
            if self._apply_db_update(record, transcripts):
                return record

        raise ApiError(
            "topic_source_conflict",
            "The topic source was modified concurrently. Please retry.",
            409,
        )

    @staticmethod
    def _apply_update(
        record: TopicSourceRecord,
        topics: list[str] | None,
        transcripts: dict[int, str] | None,
        jottings_text: str | None,
    ) -> None:
        if jottings_text is not None:
            record.jottings_text = jottings_text

        if transcripts:
            by_index = {image.index: image for image in record.images}
            missing = [index for index in transcripts if index not in by_index]
            if missing:
                raise ApiError(
                    "image_not_found",
                    "The requested note image does not exist on this topic source.",
                    400,
                )
            for index, text in transcripts.items():
                image = by_index[index]
                image.text = text
                image.ocr_status = "manual" if text.strip() else "empty"
                image.transcript_source = "manual"
                image.warnings = _manual_warnings()

        if topics is not None:
            record.topics = list(topics)
        elif jottings_text is not None or transcripts:
            # Input text changed, so any previously saved topic list is stale.
            record.topics = []

        record.status = _source_status(record.images, record.jottings_text)
        record.total_chars = len(record.jottings_text) + sum(
            len(image.text) for image in record.images
        )
        record.warnings = _source_warnings(
            record.images, record.jottings_text, record.warnings
        )
        record.updated_at = time.time()

    def _apply_db_update(
        self,
        record: TopicSourceRecord,
        transcripts: dict[int, str] | None,
    ) -> bool:
        try:
            with db.transaction() as conn:
                if transcripts:
                    by_index = {image.index: image for image in record.images}
                    for index in transcripts:
                        image = by_index[index]
                        conn.execute(
                            """
                            UPDATE topic_source_images
                               SET ocr_text = ?, ocr_status = ?,
                                   warnings_json = ?, transcript_source = ?
                             WHERE source_id = ? AND image_index = ?
                            """,
                            (
                                image.text,
                                image.ocr_status,
                                json.dumps(image.warnings),
                                image.transcript_source,
                                record.source_id,
                                image.index,
                            ),
                        )
                cursor = conn.execute(
                    """
                    UPDATE topic_sources
                       SET status = ?, jottings_text = ?, total_chars = ?,
                           topics_json = ?, warnings_json = ?, updated_at = ?,
                           version = ?
                     WHERE source_id = ? AND owner_id = ? AND version = ?
                    """,
                    (
                        record.status,
                        record.jottings_text,
                        record.total_chars,
                        json.dumps(record.topics),
                        json.dumps(record.warnings),
                        record.updated_at,
                        record.version + 1,
                        record.source_id,
                        record.owner_id,
                        record.version,
                    ),
                )
                if cursor.rowcount == 0:
                    raise _StaleWrite
            record.version += 1
            return True
        except _StaleWrite:
            return False

    def delete(self, source_id: str) -> bool:
        if (
            not source_id
            or source_id in {".", ".."}
            or Path(source_id).name != source_id
        ):
            return False
        removed = False
        if db.configured:
            with db.transaction() as conn:
                cursor = conn.execute(
                    "DELETE FROM topic_sources WHERE source_id = ?", (source_id,)
                )
                removed = cursor.rowcount > 0
                conn.execute(
                    "DELETE FROM topic_source_images WHERE source_id = ?",
                    (source_id,),
                )
        with self._lock:
            if self._memory.pop(source_id, None) is not None:
                removed = True
        shutil.rmtree(self.root / source_id, ignore_errors=True)
        return removed

    def sweep(self) -> int:
        """Remove expired sources and unreferenced image directories.

        Orphan directories are only removed after the retention window has
        elapsed, so a directory created just before a failed database insert is
        not deleted while a request is still finishing.
        """
        now = time.time()
        removed = 0
        known: set[str] = set()

        if db.configured:
            rows = db.query("SELECT source_id, expires_at FROM topic_sources")
            for row in rows:
                source_id = row["source_id"]
                if float(row["expires_at"]) < now:
                    if self.delete(source_id):
                        removed += 1
                else:
                    known.add(source_id)
        else:
            with self._lock:
                records = list(self._memory.items())
            for source_id, record in records:
                if record.expires_at < now:
                    self.delete(source_id)
                    removed += 1
                else:
                    known.add(source_id)

        orphan_grace = max(self._ttl_seconds, 60.0)
        try:
            for candidate in self.root.iterdir():
                if not candidate.is_dir() or candidate.name in known:
                    continue
                try:
                    age = now - candidate.stat().st_mtime
                except OSError:
                    continue
                if age > orphan_grace:
                    shutil.rmtree(candidate, ignore_errors=True)
        except OSError:
            pass

        return removed

    def clear(self) -> None:
        if db.configured:
            with db.transaction() as conn:
                conn.execute("DELETE FROM topic_source_images")
                conn.execute("DELETE FROM topic_sources")
        with self._lock:
            self._memory.clear()
        if self._root is not None:
            shutil.rmtree(self._root, ignore_errors=True)
            self._root.mkdir(parents=True, exist_ok=True)

    def _get(self, source_id: str) -> TopicSourceRecord | None:
        if not db.configured:
            with self._lock:
                return self._memory.get(source_id)
        rows = db.query(
            "SELECT * FROM topic_sources WHERE source_id = ?", (source_id,)
        )
        if not rows:
            return None
        image_rows = db.query(
            "SELECT * FROM topic_source_images WHERE source_id = ? "
            "ORDER BY image_index",
            (source_id,),
        )
        return self._from_rows(rows[0], image_rows)

    def _insert_source(self, conn: Any, record: TopicSourceRecord) -> None:
        conn.execute(
            """
            INSERT INTO topic_sources (
                source_id, owner_id, document_id, document_filename,
                document_format, document_page_count, status, image_count,
                jottings_text, total_chars, topics_json, warnings_json,
                created_at, updated_at, expires_at, version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.source_id,
                record.owner_id,
                record.document_id,
                record.document_filename,
                record.document_format,
                record.document_page_count,
                record.status,
                record.image_count,
                record.jottings_text,
                record.total_chars,
                json.dumps(record.topics),
                json.dumps(record.warnings),
                record.created_at,
                record.updated_at,
                record.expires_at,
                record.version,
            ),
        )
        for image in record.images:
            conn.execute(
                """
                INSERT INTO topic_source_images (
                    source_id, image_index, filename, content_type,
                    size_bytes, width, height, path, ocr_status, ocr_text,
                    warnings_json, transcript_source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.source_id,
                    image.index,
                    image.filename,
                    image.content_type,
                    image.size_bytes,
                    image.width,
                    image.height,
                    str(image.path),
                    image.ocr_status,
                    image.text,
                    json.dumps(image.warnings),
                    image.transcript_source,
                    image.created_at,
                ),
            )

    @staticmethod
    def _from_rows(
        source_row: Any, image_rows: list[Any]
    ) -> TopicSourceRecord:
        images = [
            TopicImageRecord(
                index=int(row["image_index"]),
                filename=row["filename"],
                content_type=row["content_type"],
                size_bytes=int(row["size_bytes"]),
                width=int(row["width"]),
                height=int(row["height"]),
                path=Path(row["path"]),
                ocr_status=row["ocr_status"],
                text=row["ocr_text"],
                warnings=json.loads(row["warnings_json"] or "[]"),
                transcript_source=row["transcript_source"],
                created_at=float(row["created_at"]),
            )
            for row in image_rows
        ]
        return TopicSourceRecord(
            source_id=source_row["source_id"],
            owner_id=source_row["owner_id"],
            document_id=source_row["document_id"],
            document_filename=source_row["document_filename"],
            document_format=source_row["document_format"],
            document_page_count=source_row["document_page_count"],
            status=source_row["status"],
            image_count=int(source_row["image_count"]),
            jottings_text=source_row["jottings_text"] or "",
            total_chars=int(source_row["total_chars"]),
            topics=json.loads(source_row["topics_json"] or "[]"),
            warnings=json.loads(source_row["warnings_json"] or "[]"),
            images=images,
            created_at=float(source_row["created_at"]),
            updated_at=float(source_row["updated_at"]),
            expires_at=float(source_row["expires_at"]),
            version=int(source_row["version"]),
        )


topic_sources = TopicSourceStore()
