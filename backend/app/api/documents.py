from __future__ import annotations

import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.errors import ApiError
from app.schemas.documents import (
    DocumentResponse,
    ExtractionInfo,
    IngestError,
    IngestProgress,
    Warning,
)
from app.services.document_parser import (
    _detect_format,
    parse_document,
    validate_document_limits,
)
from app.services.ingest import start_ingest
from app.services.storage import StoredDocument, storage

router = APIRouter(prefix="/api/v1", tags=["documents"])
logger = logging.getLogger(__name__)


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


def _pages_payload(parsed: Any) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for page in parsed.pages:
        item = asdict(page)
        item["structure"] = [asdict(block) for block in page.structure]
        pages.append(item)
    return pages


def _document_response(
    record: StoredDocument, *, extraction_status: str | None = None
) -> DocumentResponse:
    if record.ingest_status == "processing":
        extraction_status = "pending"
        text_extractable = False
        section_count = 0
        total_chars = 0
    else:
        extraction_status = extraction_status or record.extraction_status
        text_extractable = record.text_extractable
        section_count = record.section_count
        total_chars = record.total_chars
    progress = record.ingest_progress or {}
    return DocumentResponse(
        document_id=record.document_id,
        filename=record.filename,
        format=record.format,
        size_bytes=record.size_bytes,
        page_count=record.page_count,
        status=record.ingest_status,
        extraction=ExtractionInfo(
            status=extraction_status,
            text_extractable=text_extractable,
            section_count=section_count,
            total_chars=total_chars,
        ),
        warnings=[Warning(**warning) for warning in record.warnings],
        progress=IngestProgress(
            processed_units=int(progress.get("processed_units") or 0),
            total_units=int(progress.get("total_units") or 0),
            label=progress.get("label"),
        ),
        error=IngestError(**record.ingest_error) if record.ingest_error else None,
    )


@router.post(
    "/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    wait: bool = False,
) -> DocumentResponse:
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    started = time.perf_counter()
    request_id = getattr(request.state, "request_id", "req_unknown")

    read_started = time.perf_counter()
    data = await file.read(max_bytes + 1)
    read_ms = (time.perf_counter() - read_started) * 1000

    if len(data) > max_bytes:
        logger.info(
            "upload rejected request_id=%s reason=file_too_large size_bytes=%d "
            "limit_mb=%d",
            request_id,
            len(data),
            settings.max_upload_mb,
        )
        raise ApiError(
            "file_too_large",
            f"The file exceeds the {settings.max_upload_mb} MB upload limit.",
            413,
            {"limit_mb": settings.max_upload_mb},
        )

    filename = file.filename or "upload"
    use_background = settings.background_ingest and not wait
    # Cheap preflight keeps oversized uploads from being accepted and failing later.
    fmt = (
        validate_document_limits(filename, data, settings)
        if use_background
        else _detect_format(filename, data)
    )
    logger.info(
        "upload received request_id=%s filename_ext=%s size_bytes=%d",
        request_id,
        Path(filename).suffix.lower() or "none",
        len(data),
    )

    if use_background:
        document_id = storage.new_document_id()
        store_started = time.perf_counter()
        storage.create_pending(
            document_id,
            filename,
            data,
            owner_id=request.state.user_id,
            format=fmt,
        )
        store_ms = (time.perf_counter() - store_started) * 1000
        start_ingest(document_id)
        total_ms = (time.perf_counter() - started) * 1000
        response.headers["Server-Timing"] = (
            f"read;dur={read_ms:.1f}, store;dur={store_ms:.1f}, "
            f"total;dur={total_ms:.1f}"
        )
        record = storage.get_for_owner(document_id, request.state.user_id)
        if record is None:
            raise ApiError(
                "processing_failed",
                "The document could not be queued.",
                500,
            )
        return _document_response(record)

    parse_started = time.perf_counter()
    parsed = await run_in_threadpool(parse_document, filename, data, settings)
    parse_ms = (time.perf_counter() - parse_started) * 1000

    document_id = storage.new_document_id()
    sections = _sections_payload(parsed)
    pages = _pages_payload(parsed)
    store_started = time.perf_counter()
    record = storage.save(
        document_id,
        filename,
        data,
        owner_id=request.state.user_id,
        format=parsed.format,
        page_count=parsed.page_count,
        extraction_status=parsed.extraction_status,
        text_extractable=parsed.text_extractable,
        total_chars=parsed.total_chars,
        sections=sections,
        warnings=parsed.warnings,
        pages=pages,
    )
    store_ms = (time.perf_counter() - store_started) * 1000
    total_ms = (time.perf_counter() - started) * 1000

    diagnostics = parsed.diagnostics
    ocr_ms = float(diagnostics.get("ocr_duration_ms", 0.0) or 0.0)
    logger.info(
        "upload completed request_id=%s format=%s size_bytes=%d page_count=%s "
        "sections=%d chars=%d native_text_pages=%s ocr_candidates=%s "
        "ocr_attempts=%s ocr_succeeded=%s ocr_timeout=%s ocr_failed=%s "
        "ocr_skipped=%s ocr_ms=%.1f read_ms=%.1f parse_ms=%.1f store_ms=%.1f "
        "total_ms=%.1f",
        request_id,
        parsed.format,
        len(data),
        parsed.page_count,
        len(parsed.sections),
        parsed.total_chars,
        diagnostics.get("native_text_pages", 0),
        diagnostics.get("ocr_candidates", diagnostics.get("images_seen", 0)),
        diagnostics.get("ocr_attempts", 0),
        diagnostics.get(
            "ocr_succeeded_pages", diagnostics.get("ocr_succeeded_images", 0)
        ),
        diagnostics.get(
            "ocr_timeout_pages", diagnostics.get("ocr_timeout_images", 0)
        ),
        diagnostics.get(
            "ocr_failed_pages", diagnostics.get("ocr_failed_images", 0)
        ),
        diagnostics.get(
            "ocr_skipped_pages", diagnostics.get("ocr_skipped_images", 0)
        ),
        ocr_ms,
        read_ms,
        parse_ms,
        store_ms,
        total_ms,
    )

    timing = (
        f"read;dur={read_ms:.1f}, parse;dur={parse_ms:.1f}, "
        f"store;dur={store_ms:.1f}, total;dur={total_ms:.1f}"
    )
    if ocr_ms > 0:
        timing += f", ocr;dur={ocr_ms:.1f}"
    response.headers["Server-Timing"] = timing
    return _document_response(record)


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
)
def get_document(document_id: str, request: Request) -> DocumentResponse:
    record = storage.get_for_owner(document_id, request.state.user_id)
    if record is None:
        raise ApiError(
            "document_not_found",
            "The document was not found or has expired.",
            404,
        )
    return _document_response(record)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_document(document_id: str, request: Request) -> Response:
    # A non-owned id is reported exactly like a missing one so callers cannot
    # probe for other users' document ids.
    if not storage.delete_for_owner(document_id, request.state.user_id):
        raise ApiError(
            "document_not_found",
            "The document was not found or has expired.",
            404,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
