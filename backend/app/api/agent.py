from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse

from app.config import get_settings
from app.errors import ApiError
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentExportErrorData,
    AgentExportJobResponse,
    AgentExportRequest,
    AgentPreviewRequest,
    AgentPreviewResponse,
    AgentResumeRequest,
)
from app.services.agent_models import AgentModelError
from app.services.agent_exports import start_agent_export
from app.services.agent_service import (
    AgentExportError,
    agent_passage_id,
    build_agent_preview,
    resume_agent_turn,
    run_agent_turn,
)
from app.services.agent_store import AgentExportJob
from app.services.agent_store import agent_store
from app.services.storage import storage
from app.services.topic_sources import topic_sources

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

_AGENT_ERRORS = {
    "agent_rate_limited": ("The AI provider is rate limited.", 429),
    "agent_model_not_found": ("The configured agent model was not found.", 502),
    "agent_provider_auth_failed": (
        "The AI provider rejected the configured credentials.",
        502,
    ),
    "agent_provider_unavailable": ("The AI provider is unavailable.", 502),
    "agent_provider_error": ("The AI provider rejected the request.", 502),
    "agent_timed_out": ("The agent request timed out.", 504),
}


def _require_enabled() -> None:
    if not get_settings().agent_enabled:
        raise ApiError("agent_disabled", "The study agent is not enabled.", 404)


@router.post("/chat", response_model=AgentChatResponse)
async def agent_chat(payload: AgentChatRequest, request: Request) -> AgentChatResponse:
    settings = get_settings()
    _require_enabled()
    owner_id = request.state.user_id
    for attachment in payload.attachments:
        owned = (
            storage.get_for_owner(attachment.id, owner_id)
            if attachment.kind in {"pdf", "docx"}
            else topic_sources.get_for_owner(attachment.id, owner_id)
            if attachment.kind in {"image", "outline"}
            else None
        )
        if owned is None:
            logger.warning(
                "agent material missing user_id=%s attachment_id=%s kind=%s outcome=storage_missing",
                owner_id,
                attachment.id,
                attachment.kind,
            )
            raise ApiError("attachment_not_found", "An attachment was not found or is unavailable.", 404)
    active_source = (
        payload.active_source.model_dump() if payload.active_source else None
    )
    if active_source and not any(
        attachment.id == active_source["id"]
        and attachment.kind == active_source["kind"]
        for attachment in payload.attachments
    ):
        logger.warning(
            "agent active source missing user_id=%s source_id=%s kind=%s outcome=storage_missing",
            owner_id,
            active_source["id"],
            active_source["kind"],
        )
        raise ApiError(
            "active_source_not_found",
            "The selected source is not available in this chat. Reattach it and try again.",
            404,
        )
    try:
        blocks, conversation, result = await run_in_threadpool(
            run_agent_turn,
            owner_id,
            payload.message,
            payload.conversation_id,
            settings,
            [attachment.model_dump() for attachment in payload.attachments],
            active_source,
        )
    except ValueError as exc:
        if str(exc) == "conversation not found":
            raise ApiError("conversation_not_found", "The conversation was not found or has expired.", 404) from exc
        raise ApiError("agent_request_invalid", "The agent could not use that request or result.", 422) from exc
    except AgentModelError as exc:
        message, status = _AGENT_ERRORS.get(
            exc.code, ("The AI provider could not complete the request.", 502)
        )
        details = {}
        if exc.retry_after is not None:
            details["retry_after_s"] = exc.retry_after
        if exc.rate_limits:
            details["rate_limits"] = exc.rate_limits
        raise ApiError(exc.code, message, status, details or None) from exc
    except RuntimeError as exc:
        logger.info("agent unavailable error_type=%s", type(exc).__name__)
        raise ApiError("agent_unavailable", "The study agent is not configured or is temporarily unavailable.", 503) from exc
    return AgentChatResponse(
        reply=result.reply,
        conversation_id=conversation.conversation_id,
        blocks=blocks,
        warnings=result.warnings,
        status=result.status,
        usage={"model_calls": result.model_calls, "tool_calls": result.tool_calls},
    )


@router.post("/resume", response_model=AgentChatResponse)
async def agent_resume(
    payload: AgentResumeRequest, request: Request
) -> AgentChatResponse:
    settings = get_settings()
    _require_enabled()
    try:
        blocks, conversation, result = await run_in_threadpool(
            resume_agent_turn,
            request.state.user_id,
            payload.pending_id,
            payload.decision,
            settings,
        )
    except AgentModelError as exc:
        message, status = _AGENT_ERRORS.get(
            exc.code, ("The AI provider could not complete the request.", 502)
        )
        details = {}
        if exc.retry_after is not None:
            details["retry_after_s"] = exc.retry_after
        if exc.rate_limits:
            details["rate_limits"] = exc.rate_limits
        raise ApiError(exc.code, message, status, details or None) from exc
    except RuntimeError as exc:
        logger.info("agent unavailable error_type=%s", type(exc).__name__)
        raise ApiError(
            "agent_unavailable",
            "The study agent is not configured or is temporarily unavailable.",
            503,
        ) from exc
    return AgentChatResponse(
        reply=result.reply,
        conversation_id=conversation.conversation_id,
        blocks=blocks,
        warnings=result.warnings,
        status=result.status,
        usage={"model_calls": result.model_calls, "tool_calls": result.tool_calls},
    )


@router.get("/results/{result_id}")
def get_agent_result(result_id: str, request: Request) -> dict[str, Any]:
    _require_enabled()
    record = agent_store.get_result(result_id, request.state.user_id)
    if record is None:
        raise ApiError("agent_result_not_found", "The result was not found or has expired.", 404)
    summary_text = None
    if record.kind == "summary":
        summary_text = record.payload.get("summary", "")
        source_id = record.payload.get("source_result_id")
        if isinstance(source_id, str) and source_id:
            source = agent_store.get_result(source_id, request.state.user_id)
            if source is None:
                raise ApiError(
                    "agent_result_not_found",
                    "The result was not found or has expired.",
                    404,
                )
            record = source
    document = storage.get_for_owner(record.document_id or "", request.state.user_id)
    if document is None:
        raise ApiError("agent_result_not_found", "The result was not found or has expired.", 404)
    page_info = {}
    for page_record in storage.pages_for(document.document_id):
        number = page_record.get("page_number")
        if not isinstance(number, int):
            number = int(page_record.get("index", -1)) + 1
        if number > 0:
            page_info[number] = page_record
    topics = []
    for topic_index, item in enumerate(record.payload.get("topics", [])):
        passages = []
        for ref_index, ref in enumerate(item.get("passages", [])):
            passage_id = agent_passage_id(
                document.document_id,
                ref,
                f"{topic_index}:{ref_index}",
            )
            if ref.get("no_text"):
                passages.append({
                    "passage_id": passage_id,
                    "page": ref.get("page"),
                    "anchor": ref.get("anchor", {}),
                    "text": "No text on this page",
                    "has_page_image": bool(ref.get("has_page_image")),
                    "source": "none",
                    "ocr_status": "none",
                    "no_text": True,
                })
                continue
            section = next(
                (part for part in document.sections if int(part.get("index", -1)) == ref.get("section_index")),
                None,
            )
            passage_text = ref.get("passage_text")
            if section is None and not isinstance(passage_text, str):
                continue
            page = ref.get("page")
            page_record = page_info.get(page, {})
            passages.append({
                "passage_id": passage_id,
                "page": page,
                "anchor": ref.get("anchor", {}),
                "text": (
                    passage_text
                    if isinstance(passage_text, str)
                    else str(section.get("raw_text") or "")
                ),
                "has_page_image": bool(ref.get("has_page_image") or page_record.get("has_page_image")),
                "source": page_record.get(
                    "source", (section or {}).get("source", "native")
                ),
                "ocr_status": page_record.get("ocr_status", "none"),
            })
        topics.append({"topic": item.get("topic"), "passages": passages})
    response: dict[str, Any] = {"result_id": result_id, "document_id": document.document_id, "topics": topics}
    if summary_text is not None:
        response["summary"] = summary_text
        response["summary_label"] = "AI summary, not source text"
    return response


def _export_job_response(job: AgentExportJob) -> AgentExportJobResponse:
    error = None
    if job.error_code:
        error = AgentExportErrorData(
            code=job.error_code,
            message=job.error_message or "The export could not be generated.",
            details=job.details,
        )
    return AgentExportJobResponse(
        job_id=job.job_id,
        result_id=job.result_id,
        format=job.format,
        status=job.status,
        file_id=job.file_id,
        name=job.name,
        error=error,
    )


@router.post(
    "/results/{result_id}/preview",
    response_model=AgentPreviewResponse,
)
def preview_result(
    result_id: str, payload: AgentPreviewRequest, request: Request
) -> AgentPreviewResponse:
    _require_enabled()
    try:
        preview = build_agent_preview(
            request.state.user_id,
            result_id,
            payload.edits.model_dump() if payload.edits else None,
            payload.limit,
        )
    except AgentExportError as exc:
        raise ApiError(
            exc.code, exc.message, exc.status_code, exc.details or None
        ) from exc
    return AgentPreviewResponse(**preview)


@router.post(
    "/results/{result_id}/exports",
    response_model=AgentExportJobResponse,
    status_code=202,
)
def start_result_export(
    result_id: str, payload: AgentExportRequest, request: Request
) -> AgentExportJobResponse:
    _require_enabled()
    try:
        job = start_agent_export(
            request.state.user_id,
            result_id,
            payload.format,
            payload.allow_incomplete_ocr,
            payload.edits.model_dump() if payload.edits else None,
        )
    except AgentExportError as exc:
        raise ApiError(exc.code, exc.message, exc.status_code, exc.details or None) from exc
    return _export_job_response(job)


@router.get("/exports/{job_id}", response_model=AgentExportJobResponse)
def get_export_job(job_id: str, request: Request) -> AgentExportJobResponse:
    _require_enabled()
    job = agent_store.get_export_job(job_id, request.state.user_id)
    if job is None or job.format not in {"docx", "pdf"}:
        raise ApiError(
            "agent_export_not_found",
            "The export was not found or has expired.",
            404,
        )
    return _export_job_response(job)


@router.get("/files/{file_id}")
def download_agent_file(file_id: str, request: Request) -> FileResponse:
    _require_enabled()
    record = agent_store.get_file(file_id, request.state.user_id)
    if (
        record is None
        or record.format not in {"docx", "pdf"}
        or not record.path.is_file()
    ):
        raise ApiError("agent_file_not_found", "The file was not found or has expired.", 404)
    media_type = "application/pdf" if record.format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(
        path=record.path,
        media_type=media_type,
        filename=Path(record.name).name,
    )
