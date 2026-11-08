from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.concurrency import run_in_threadpool

from app.config import Settings, get_settings
from app.errors import ApiError
from app.schemas.study import ExportFormat, StudyJobRequest, StudyJobSummary
from app.schemas.range_extraction import RangeExtractionRequest, RangeExtractionResponse
from app.services.ai_client import AiNotConfigured
from app.services.exporter import EXPORT_MEDIA_TYPES, export_filename, render
from app.services.job_store import jobs
from app.services.storage import storage
from app.services.range_extraction import extract_range
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
        warnings=result.warnings,
    )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _document_available(document_id: str, user_id: str | None) -> bool:
    record = storage.get_for_owner(document_id, user_id)
    return record is not None and record.available


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
        warnings=outcome.warnings,
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
    if not record.available:
        raise ApiError(
            "document_unavailable",
            "The source document is no longer available. Completed results remain "
            "readable, but a new study job cannot be started.",
            409,
        )
    if not record.text_extractable or not record.sections:
        raise ApiError(
            "no_extractable_text",
            "This document contains no extractable text to study.",
            409,
        )
    if payload.mode in AI_MODES and not settings.ai_configured:
        raise ApiError(
            "ai_not_configured",
            "AI summarization is not configured on the server.",
            503,
        )

    job_id = jobs.new_job_id()
    created_at = _now()

    if len(record.sections) <= settings.sync_processing_max_sections:
        base = _base_payload(
            job_id, record, payload.mode, "running", created_at, user_id
        )
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
    return Response(
        content=data,
        media_type=EXPORT_MEDIA_TYPES[format],
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Server-Timing": (
                f"render;dur={render_ms:.1f}, total;dur={total_ms:.1f}"
            ),
        },
    )
