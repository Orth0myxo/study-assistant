from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import Any

from app.config import Settings, get_settings
from app.errors import ApiError
from app.services.document_parser import Page, parse_document
from app.services.storage import storage

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None
_executor_workers = 0
_executor_lock = threading.Lock()
_active: set[str] = set()
_active_lock = threading.Lock()


def _sections_payload(parsed: Any) -> list[dict[str, Any]]:
    return [
        {
            "index": section.index,
            "anchor": section.anchor,
            "raw_text": section.raw_text,
            "normalized_text": section.normalized_text,
        }
        for section in parsed.sections
    ]


def _pages_payload(pages: list[Page]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for page in pages:
        item = asdict(page)
        item["structure"] = [asdict(block) for block in page.structure]
        payload.append(item)
    return payload


def _executor_for(settings: Settings) -> ThreadPoolExecutor:
    global _executor, _executor_workers
    workers = max(int(settings.ingest_workers), 1)
    with _executor_lock:
        if _executor is None or _executor_workers != workers:
            if _executor is not None:
                _executor.shutdown(wait=False, cancel_futures=True)
            _executor = ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="ingest"
            )
            _executor_workers = workers
        return _executor


def start_ingest(document_id: str) -> None:
    with _active_lock:
        if document_id in _active:
            return
        _active.add(document_id)
    settings = get_settings()
    try:
        _executor_for(settings).submit(_run_ingest, document_id)
    except Exception:
        with _active_lock:
            _active.discard(document_id)
        raise


def _run_ingest(document_id: str) -> None:
    try:
        record = storage.get(document_id)
        if record is None:
            return
        settings = get_settings()
        data = record.path.read_bytes()

        def progress(processed: int, total: int, label: str) -> None:
            storage.update_progress(document_id, processed, total, label)

        parsed = parse_document(
            record.filename, data, settings, progress=progress
        )
        pages = _pages_payload(parsed.pages)
        storage.complete_ingest(
            document_id,
            page_count=parsed.page_count,
            extraction_status=parsed.extraction_status,
            text_extractable=parsed.text_extractable,
            total_chars=parsed.total_chars,
            sections=_sections_payload(parsed),
            pages=pages,
            warnings=parsed.warnings,
            progress={
                "processed_units": len(pages),
                "total_units": len(pages),
            },
            diagnostics=parsed.diagnostics,
        )
    except ApiError as exc:
        storage.fail_ingest(document_id, exc.code, exc.message)
    except Exception as exc:  # noqa: BLE001 - background failures must be visible
        logger.warning(
            "background ingest failed document_id=%s error_type=%s",
            document_id,
            type(exc).__name__,
        )
        storage.fail_ingest(
            document_id,
            "processing_failed",
            "The document could not be processed. Upload it again.",
        )
    finally:
        with _active_lock:
            _active.discard(document_id)


def resume_pending() -> int:
    if not get_settings().background_ingest:
        return 0
    pending = storage.pending_ids()
    for document_id in pending:
        start_ingest(document_id)
    return len(pending)
