from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Request, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.errors import ApiError
from app.schemas.documents import DocumentResponse, ExtractionInfo, Warning
from app.services.document_parser import parse_document
from app.services.storage import storage

router = APIRouter(prefix="/api/v1", tags=["documents"])
logger = logging.getLogger(__name__)


@router.post(
    "/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
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
    logger.info(
        "upload received request_id=%s filename_ext=%s size_bytes=%d",
        request_id,
        Path(filename).suffix.lower() or "none",
        len(data),
    )

    parse_started = time.perf_counter()
    parsed = await run_in_threadpool(parse_document, filename, data, settings)
    parse_ms = (time.perf_counter() - parse_started) * 1000

    document_id = storage.new_document_id()
    sections: list[dict[str, Any]] = [
        {
            "index": section.index,
            "anchor": section.anchor,
            "raw_text": section.raw_text,
            "normalized_text": section.normalized_text,
        }
        for section in parsed.sections
    ]
    store_started = time.perf_counter()
    storage.save(
        document_id,
        filename,
        data,
        format=parsed.format,
        page_count=parsed.page_count,
        extraction_status=parsed.extraction_status,
        text_extractable=parsed.text_extractable,
        total_chars=parsed.total_chars,
        sections=sections,
        warnings=parsed.warnings,
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

    return DocumentResponse(
        document_id=document_id,
        filename=filename,
        format=parsed.format,
        size_bytes=len(data),
        page_count=parsed.page_count,
        extraction=ExtractionInfo(
            status=parsed.extraction_status,
            text_extractable=parsed.text_extractable,
            section_count=len(parsed.sections),
            total_chars=parsed.total_chars,
        ),
        warnings=[Warning(**warning) for warning in parsed.warnings],
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_document(document_id: str) -> Response:
    if not storage.delete(document_id):
        raise ApiError(
            "document_not_found",
            "The document was not found or has expired.",
            404,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
