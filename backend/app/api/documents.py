from __future__ import annotations

import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.config import get_settings
from app.errors import ApiError
from app.schemas.documents import (
    DocumentResponse,
    ExtractionInfo,
    IngestError,
    IngestProgress,
    OcrBatchResponse,
    OcrPrepareRequest,
    OcrProgressResponse,
    Warning,
)
from app.services.document_parser import (
    _detect_format,
    parse_document,
    validate_document_limits,
)
from app.services.ingest import start_ingest
from app.services.page_ocr import OcrReport, ensure_pdf_pages_ocr, ocr_progress
from app.services.page_images import render_page_image
from app.services.storage import StoredDocument, require_ready_document, storage

router = APIRouter(prefix="/api/v1", tags=["documents"])
logger = logging.getLogger(__name__)


def _sections_payload(parsed: Any) -> list[dict[str, Any]]:
    return [
        {
            "index": section.index,
            "anchor": section.anchor,
            "raw_text": section.raw_text,
            "normalized_text": section.normalized_text,
            "has_page_image": section.has_page_image,
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


def _ocr_warnings(
    pending: list[int], failed: list[int], empty: list[int]
) -> list[Warning]:
    warnings: list[Warning] = []
    if pending:
        warnings.append(
            Warning(
                code="ocr_pending_pages",
                message="OCR still needs to read page(s): "
                + ", ".join(map(str, pending))
                + ".",
            )
        )
    if failed:
        warnings.append(
            Warning(
                code="ocr_failed_pages",
                message="OCR failed on page(s): "
                + ", ".join(map(str, failed))
                + ". Native text is shown where available.",
            )
        )
    if empty:
        warnings.append(
            Warning(
                code="ocr_empty_pages",
                message="Picture page(s), no text could be read: "
                + ", ".join(map(str, empty))
                + ".",
            )
        )
    return warnings


def _ocr_progress_response(
    record: StoredDocument, page_numbers: list[int] | None = None
) -> OcrProgressResponse:
    progress = ocr_progress(record, page_numbers)
    return OcrProgressResponse(
        document_id=record.document_id,
        format=record.format,  # type: ignore[arg-type]
        page_count=record.page_count,
        status=progress.status,  # type: ignore[arg-type]
        total_pages=progress.total_pages,
        needs_ocr_pages=progress.needs_ocr_pages,
        completed_pages=progress.completed_pages,
        pending_pages=progress.pending_pages,
        failed_pages=progress.failed_pages,
        empty_pages=progress.empty_pages,
        warnings=_ocr_warnings(
            progress.pending_pages, progress.failed_pages, progress.empty_pages
        ),
    )


def _ocr_batch_response(
    record: StoredDocument, page_numbers: list[int] | None, report: OcrReport
) -> OcrBatchResponse:
    progress = _ocr_progress_response(record, page_numbers)
    return OcrBatchResponse(
        **progress.model_dump(),
        attempted_pages=report.attempted_pages,
        completed_this_batch=report.completed_pages,
        failed_this_batch=report.failed_pages,
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
    fmt = _detect_format(filename, data)
    if use_background and fmt == "docx":
        validate_document_limits(filename, data, settings)
    # Native PDF text parsing is now cheap because ingest defers OCR. Keep it
    # in the upload request so the response includes read/parse/store timings
    # and the document is immediately ready. DOCX embedded-image OCR remains
    # asynchronous unless the caller explicitly waits.
    use_background = use_background and fmt != "pdf"
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


def _ready_owned_document(document_id: str, request: Request) -> StoredDocument:
    record = storage.get_for_owner(document_id, request.state.user_id)
    if record is None:
        raise ApiError(
            "document_not_found",
            "The document was not found or has expired.",
            404,
        )
    if not record.available:
        raise ApiError(
            "document_unavailable",
            "The source document is no longer available.",
            409,
        )
    return require_ready_document(record)


@router.get(
    "/documents/{document_id}/ocr",
    response_model=OcrProgressResponse,
)
def get_document_ocr(document_id: str, request: Request) -> OcrProgressResponse:
    """Report resumable OCR state without starting OCR work."""
    record = _ready_owned_document(document_id, request)
    return _ocr_progress_response(record)


@router.post(
    "/documents/{document_id}/ocr",
    response_model=OcrBatchResponse,
)
def prepare_document_ocr(
    document_id: str,
    request: Request,
    payload: OcrPrepareRequest | None = None,
) -> OcrBatchResponse:
    """OCR one bounded batch so callers can resume after a request timeout."""
    record = _ready_owned_document(document_id, request)
    if record.format != "pdf" or not record.page_count:
        return _ocr_batch_response(record, None, OcrReport([], [], [], [], []))
    requested: list[int] | None = None
    if payload is not None and payload.pages is not None:
        requested = sorted(
            {
                page
                for page in payload.pages
                if 1 <= page <= (record.page_count or 0)
            }
        )
    if requested is None:
        requested = _ocr_progress_response(record).pending_pages
    report = ensure_pdf_pages_ocr(record, requested, get_settings())
    return _ocr_batch_response(record, requested, report)


@router.get("/documents/{document_id}/pages/{page_number}/image")
def get_document_page_image(
    document_id: str, page_number: int, request: Request
) -> Response:
    """Return a bounded, retention-scoped PNG rendering of one PDF page."""
    record = storage.get_for_owner(document_id, request.state.user_id)
    if record is None:
        raise ApiError("document_not_found", "The document was not found or has expired.", 404)
    require_ready_document(record)
    if (
        record.format != "pdf"
        or record.page_count is None
        or page_number < 1
        or page_number > record.page_count
        or not record.available
    ):
        raise ApiError("page_not_found", "The requested PDF page was not found.", 404)

    image_path = render_page_image(
        document_id=document_id,
        source_path=record.path,
        cache_root=storage.root,
        page_number=page_number,
    )
    return FileResponse(image_path, media_type="image/png")


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
