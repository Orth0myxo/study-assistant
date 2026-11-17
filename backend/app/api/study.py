from __future__ import annotations

import asyncio
import copy
import logging
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.errors import ApiError
from app.schemas.study import (
    ExportFormat,
    StudyJobRequest,
    StudyJobResponse,
    StudyJobSummary,
)
from app.schemas.range_extraction import (
    RangeExtractionRequest,
    RangeExtractionResponse,
    RangeStudyRequest,
)
from app.services.ai_client import AiNotConfigured
from app.services.exporter import (
    EXPORT_MEDIA_TYPES,
    add_page_export_context,
    export_filename,
    organize_study_job_payload,
    render,
)
from app.services.job_store import jobs
from app.services.storage import require_ready_document, storage
from app.services.range_extraction import extract_range
from app.services.page_ocr import (
    ensure_pdf_pages_ocr,
    ocr_progress,
    raise_if_ocr_incomplete,
    raise_if_no_source_text,
    requested_pdf_pages,
)
from app.services.study_processor import AI_MODES, process_document

EXPORTABLE_STATUSES = {"completed", "partial"}

router = APIRouter(prefix="/api/v1", tags=["study"])
logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task[None]] = set()


@router.post("/documents/{document_id}/ranges", response_model=RangeExtractionResponse)
def extract_document_range(
    document_id: str, payload: RangeExtractionRequest, request: Request
) -> RangeExtractionResponse:
    record = storage.get_for_owner(document_id, request.state.user_id)
    if record is None:
        raise ApiError("document_not_found", "The document was not found or has expired. Please upload it again.", 404)
    record = require_ready_document(record)
    ocr_report, _ = _ocr_legacy_range(record, payload.selector)
    result = extract_range(
        document_id=document_id,
        format=record.format,
        page_count=record.page_count,
        sections=record.sections,
        selector=payload.selector,
    )
    return RangeExtractionResponse(
        document_id=document_id,
        format=record.format,
        page_count=record.page_count,
        selector=result.selector,
        sections=result.sections,
        warnings=[*result.warnings, *ocr_report.warnings],
    )


@router.post(
    "/documents/{document_id}/range-study",
    response_model=StudyJobResponse,
)
async def create_range_study(
    document_id: str, payload: RangeStudyRequest, request: Request
) -> StudyJobResponse:
    settings = get_settings()
    record = storage.get_for_owner(document_id, request.state.user_id)
    if record is None:
        raise ApiError("document_not_found", "The document was not found or has expired. Please upload it again.", 404)
    record = require_ready_document(record)
    ocr_report, ocr_pages = await run_in_threadpool(
        _ocr_legacy_range, record, payload.selector
    )
    raise_if_ocr_incomplete(record, ocr_pages, action="start this range study")
    selected = extract_range(
        document_id=document_id,
        format=record.format,
        page_count=record.page_count,
        sections=record.sections,
        selector=payload.selector,
    )
    backend_mode = {
        "source_passages": "extracted_text_only",
        "both": "text_and_summaries",
        "ai_study_material": "summaries_only",
    }[payload.mode]
    if backend_mode in AI_MODES and not settings.ai_configured:
        raise ApiError("ai_not_configured", "AI summarization is not configured on the server.", 503)
    selected_record = replace(
        record,
        sections=selected.sections,
        section_count=len(selected.sections),
        total_chars=sum(len(str(section.get("raw_text") or "")) for section in selected.sections),
    )
    job_id = jobs.new_job_id()
    created_at = _now()
    base = _base_payload(job_id, selected_record, backend_mode, "running", created_at, request.state.user_id)
    try:
        outcome = await run_in_threadpool(process_document, selected_record, backend_mode, settings)
    except AiNotConfigured as exc:
        from app.services.study_processor import StudyOutcome

        outcome = StudyOutcome(
            status="failed",
            error={"code": exc.code, "message": exc.message},
            warnings=[{"code": exc.code, "message": exc.message}],
        )
    result = _apply(base, outcome)
    result["warnings"] = [
        *selected.warnings,
        *ocr_report.warnings,
        *result["warnings"],
    ]
    jobs.put(job_id, result)
    return StudyJobResponse.model_validate(_public(result))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _document_available(document_id: str, user_id: str | None) -> bool:
    record = storage.get_for_owner(document_id, user_id)
    return record is not None and record.available


def _legacy_selector_needs_page_ocr(selector: str) -> bool:
    """True when a legacy selector cannot name its pages before reading them."""
    from app.services.range_extraction import _legacy_ranges

    try:
        return not _legacy_ranges(selector)
    except Exception:
        return True


def _ocr_legacy_range(record: Any, selector: str) -> tuple[Any, list[int]]:
    pre_report = None
    if record.format == "pdf" and _legacy_selector_needs_page_ocr(selector):
        pending = ocr_progress(record).pending_pages
        if pending:
            pre_report = ensure_pdf_pages_ocr(record, pending, get_settings())
    native_sections: list[dict[str, Any]] = []
    try:
        native = extract_range(
            document_id=record.document_id,
            format=record.format,
            page_count=record.page_count,
            sections=record.sections,
            selector=selector,
        )
        native_sections = native.sections
    except (ApiError, ValueError):
        # Explicit page selectors can still OCR a page with no native section.
        pass
    pages = requested_pdf_pages(
        record,
        legacy_selector=selector,
        selected_sections=native_sections,
    )
    if pre_report is not None:
        return pre_report, pages
    return ensure_pdf_pages_ocr(record, pages, get_settings()), pages


def _public(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip internal bookkeeping (ownership) before sending a payload."""
    return {key: value for key, value in payload.items() if key != "owner_id"}


def _base_payload(
    job_id: str,
    record: Any,
    mode: str,
    status_value: str,
    now: str,
    owner_id: str,
) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "owner_id": owner_id,
        "document_id": record.document_id,
        "filename": record.filename,
        "format": record.format,
        "mode": mode,
        "status": status_value,
        "created_at": now,
        "updated_at": now,
        "page_count": record.page_count,
        "item_count": 0,
        "items": [],
        "document_summary": None,
        "warnings": [],
        "error": None,
        "progress": {"processed_units": 0, "total_units": 0},
        "document_available": record.available,
    }


def _apply(base: dict[str, Any], outcome: Any) -> dict[str, Any]:
    payload = dict(base)
    payload.update(
        status=outcome.status,
        updated_at=_now(),
        items=outcome.items,
        item_count=len(outcome.items),
        document_summary=outcome.document_summary,
        warnings=[*base.get("warnings", []), *outcome.warnings],
        diagnostics=outcome.diagnostics,
        error=outcome.error,
        progress={
            "processed_units": outcome.processed_units,
            "total_units": outcome.total_units,
        },
    )
    return payload


async def _run_background(
    job_id: str, document_id: str, mode: str, settings: Settings
) -> None:
    base = jobs.get(job_id)
    if base is None:
        return
    owner_id = base.get("owner_id")
    record = storage.get_for_owner(document_id, owner_id)
    if record is None or not record.available:
        message = (
            "The source document is no longer available, so it cannot be processed."
        )
        base.update(
            status="failed",
            updated_at=_now(),
            error={"code": "document_unavailable", "message": message},
            warnings=[{"code": "document_unavailable", "message": message}],
            document_available=False,
        )
        jobs.put(job_id, base)
        return

    from app.services.study_processor import StudyOutcome

    try:
        outcome = await asyncio.to_thread(process_document, record, mode, settings)
    except AiNotConfigured as exc:
        outcome = StudyOutcome(
            status="failed",
            error={"code": exc.code, "message": exc.message},
            warnings=[{"code": exc.code, "message": exc.message}],
        )
    jobs.put(job_id, _apply(base, outcome))


@router.post("/study-jobs", response_model=None, status_code=status.HTTP_200_OK)
async def create_study_job(
    payload: StudyJobRequest, response: Response, request: Request
) -> dict[str, Any]:
    settings = get_settings()
    user_id = request.state.user_id
    record = storage.get_for_owner(payload.document_id, user_id)
    if record is None:
        raise ApiError(
            "document_not_found",
            "The document was not found or has expired. Please upload it again.",
            404,
        )
    record = require_ready_document(record)
    if not record.available:
        raise ApiError(
            "document_unavailable",
            "The source document is no longer available. Completed results remain "
            "readable, but a new study job cannot be started.",
            409,
        )
    if payload.mode in AI_MODES and not settings.ai_configured:
        raise ApiError("ai_not_configured", "AI summarization is not configured on the server.", 503)
    requested_pages = (
        list(range(1, (record.page_count or 0) + 1))
        if record.format == "pdf"
        else []
    )
    ocr_report = await run_in_threadpool(
        ensure_pdf_pages_ocr, record, requested_pages, settings
    )
    raise_if_ocr_incomplete(
        record, requested_pages, action="start a whole-document study job"
    )
    if not record.text_extractable or not record.sections:
        raise ApiError(
            "no_extractable_text",
            "This document contains no extractable text to study.",
            409,
        )
    job_id = jobs.new_job_id()
    created_at = _now()

    if len(record.sections) <= settings.sync_processing_max_sections:
        base = _base_payload(
            job_id, record, payload.mode, "running", created_at, user_id
        )
        base["warnings"] = list(ocr_report.warnings)
        try:
            outcome = await run_in_threadpool(
                process_document, record, payload.mode, settings
            )
        except AiNotConfigured as exc:
            from app.services.study_processor import StudyOutcome

            outcome = StudyOutcome(
                status="failed",
                error={"code": exc.code, "message": exc.message},
                warnings=[{"code": exc.code, "message": exc.message}],
            )
        result = _apply(base, outcome)
        jobs.put(job_id, result)
        response.status_code = status.HTTP_200_OK
        return _public(result)

    queued = _base_payload(
        job_id, record, payload.mode, "queued", created_at, user_id
    )
    queued["warnings"] = list(ocr_report.warnings)
    jobs.put(job_id, queued)
    task = asyncio.create_task(
        _run_background(job_id, payload.document_id, payload.mode, settings)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    response.status_code = status.HTTP_202_ACCEPTED
    return _public(queued)


@router.get("/study-jobs", response_model=list[StudyJobSummary])
def list_study_jobs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[dict[str, Any]]:
    user_id = request.state.user_id
    summaries: list[dict[str, Any]] = []
    for payload in jobs.list_for_owner(user_id, limit=limit, offset=offset):
        summaries.append(
            {
                "job_id": payload.get("job_id"),
                "document_id": payload.get("document_id"),
                "filename": payload.get("filename"),
                "format": payload.get("format"),
                "mode": payload.get("mode"),
                "status": payload.get("status"),
                "created_at": payload.get("created_at"),
                "updated_at": payload.get("updated_at"),
                "page_count": payload.get("page_count"),
                "item_count": payload.get("item_count") or 0,
                "document_available": _document_available(
                    payload.get("document_id") or "", user_id
                ),
            }
        )
    return summaries


@router.get("/study-jobs/{job_id}")
def get_study_job(job_id: str, request: Request) -> dict[str, Any]:
    user_id = request.state.user_id
    payload = jobs.get_for_owner(job_id, user_id)
    if payload is None:
        raise ApiError(
            "job_not_found",
            "The study job was not found or has expired.",
            404,
        )
    result = _public(dict(payload))
    result["document_available"] = _document_available(
        payload.get("document_id") or "", user_id
    )
    return result


@router.get("/study-jobs/{job_id}/export")
async def export_study_job(
    job_id: str,
    request: Request,
    format: ExportFormat = Query(...),
    include_page_images: bool = Query(default=True),
    include_parts: bool = Query(default=False),
    text_align: Literal["left", "center", "right", "justify"] = Query(
        default="left"
    ),
    heading_align: Literal["left", "center"] = Query(default="left"),
) -> Response:
    started = time.perf_counter()
    request_id = getattr(request.state, "request_id", "req_unknown")
    payload = jobs.get_for_owner(job_id, request.state.user_id)
    if payload is None:
        raise ApiError(
            "job_not_found",
            "The study job was not found or has expired.",
            404,
        )
    if payload.get("status") not in EXPORTABLE_STATUSES:
        raise ApiError(
            "job_not_ready",
            "The study job has not finished successfully yet.",
            409,
        )
    payload = copy.deepcopy(payload)
    ocr_report = None
    document = storage.get_for_owner(
        str(payload.get("document_id") or ""), request.state.user_id
    )
    if document is not None and document.format == "pdf":
        refs = [
            ref
            for item in payload.get("items") or []
            for ref in item.get("source_refs") or []
        ]
        pages = requested_pdf_pages(document, selected_sections=[{"anchor": ref} for ref in refs])
        ocr_report = await run_in_threadpool(
            ensure_pdf_pages_ocr, document, pages, get_settings()
        )
        raise_if_ocr_incomplete(document, pages, action="export this study job")
        refreshed = storage.get_for_owner(document.document_id, request.state.user_id)
        if refreshed is not None:
            sections = {
                int((section.get("anchor") or {}).get("page")): section
                for section in refreshed.sections
                if (section.get("anchor") or {}).get("kind") == "pdf_page"
            }
            source_passages = [
                {"text": str(sections[int(ref["page"])].get("raw_text") or "")}
                for item in payload.get("items") or []
                for ref in item.get("source_refs") or []
                if ref.get("kind") == "pdf_page"
                and int(ref.get("page", 0)) in sections
            ]
            for item in payload.get("items") or []:
                if item.get("text") is None:
                    continue
                page_passages = []
                texts = [
                    str(sections[int(ref["page"])].get("raw_text") or "")
                    for ref in item.get("source_refs") or []
                    if ref.get("kind") == "pdf_page"
                    and int(ref.get("page", 0)) in sections
                ]
                for ref in item.get("source_refs") or []:
                    page_number = int(ref.get("page", 0))
                    if ref.get("kind") == "pdf_page" and page_number in sections:
                        page_passages.append(
                            {
                                "page": page_number,
                                "text": str(
                                    sections[page_number].get("raw_text") or ""
                                ),
                            }
                        )
                item["_export_page_passages"] = page_passages
                if texts:
                    item["text"] = "\n\n".join(texts)
            raise_if_no_source_text(
                document,
                pages,
                source_passages,
                action="export this study job",
            )
    payload["text_align"] = text_align
    payload["heading_align"] = heading_align
    payload["include_parts"] = include_parts
    page_records = storage.pages_for(document.document_id) if document is not None else []
    add_page_export_context(
        payload,
        document=document,
        page_records=page_records,
        cache_root=storage.root,
        include_page_images=include_page_images,
    )
    organize_study_job_payload(payload)
    render_started = time.perf_counter()
    try:
        data = await run_in_threadpool(render, payload, format)
    except Exception as exc:  # noqa: BLE001 - never leak internals to the client
        logger.warning(
            "export failed request_id=%s format=%s error_type=%s render_ms=%.1f",
            request_id,
            format,
            type(exc).__name__,
            (time.perf_counter() - render_started) * 1000,
        )
        raise ApiError(
            "export_failed",
            "The export could not be generated.",
            500,
        ) from exc
    render_ms = (time.perf_counter() - render_started) * 1000
    total_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "export completed request_id=%s mode=%s format=%s items=%d bytes=%d "
        "render_ms=%.1f total_ms=%.1f",
        request_id,
        payload.get("mode"),
        format,
        len(payload.get("items") or []),
        len(data),
        render_ms,
        total_ms,
    )
    filename = export_filename(payload, format)
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Server-Timing": (
            f"render;dur={render_ms:.1f}, total;dur={total_ms:.1f}"
        ),
    }
    if ocr_report and ocr_report.warnings:
        headers["X-OCR-Warning"] = "; ".join(
            warning["message"] for warning in ocr_report.warnings
        )
    return Response(
        content=data,
        media_type=EXPORT_MEDIA_TYPES[format],
        headers=headers,
    )
