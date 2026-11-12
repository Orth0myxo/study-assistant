from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Literal, Optional

from fastapi import APIRouter, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.config import Settings, get_settings
from app.errors import ApiError
from app.services import extraction_service as svc
from app.services.ai_chain import build_ai_client
from app.services.ai_client import AiNotConfigured
from app.services.exporter import EXPORT_MEDIA_TYPES, export_filename, render
from app.services.provider_settings import ProviderSettings, load_provider_settings
from app.services.range_extraction import RangeError
from app.services.storage import require_ready_document, storage

router = APIRouter(prefix="/api/v1", tags=["extraction"])
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Small per-user, per-minute limiter (uses the existing rate_limit_per_minute)
# --------------------------------------------------------------------------

_hits: dict[str, deque[float]] = defaultdict(deque)
_hits_lock = threading.Lock()


def _check_rate(request: Request, settings: Settings) -> None:
    limit = int(getattr(settings, "rate_limit_per_minute", 0) or 0)
    if limit <= 0:
        return
    key = str(getattr(request.state, "user_id", None) or "anonymous")
    now = time.monotonic()
    with _hits_lock:
        window = _hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= limit:
            raise ApiError(
                "rate_limited",
                "Too many requests. Please wait a moment and try again.",
                429,
            )
        window.append(now)


def reset_rate_limits() -> None:
    with _hits_lock:
        _hits.clear()


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class ExtractSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pages", "items", "paragraphs", "heading", "chapter", "whole"]
    spec: Optional[str] = Field(default=None, max_length=200)


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selector: ExtractSelector
    mode: Literal["source_passages", "both", "ai_study_material"] = "source_passages"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _document(request: Request, document_id: str):
    record = storage.get_for_owner(
        document_id, getattr(request.state, "user_id", None)
    )
    if record is None:
        raise ApiError("document_not_found", "Document not found.", 404)
    return require_ready_document(record)


def _summary_client(
    settings: Settings,
    provider: ProviderSettings,
    mode: str,
    owner_id: str | None,
):
    if mode == "source_passages":
        return None
    try:
        return build_ai_client(settings, provider, owner_id=owner_id)
    except AiNotConfigured:
        return None


def _run(
    record: Any,
    selector_type: str,
    spec: Optional[str],
    mode: str,
    settings: Settings,
) -> dict[str, Any]:
    provider = load_provider_settings()
    try:
        selection = svc.select(
            document_id=record.document_id,
            sections=record.sections,
            page_count=record.page_count,
            selector_type=selector_type,
            spec=spec,
        )
    except RangeError as exc:
        raise ApiError(exc.code, exc.message, 422, exc.details) from exc
    client = _summary_client(settings, provider, mode, record.owner_id)
    result = svc.build_result(
        selection=selection, mode=mode, client=client, provider=provider
    )
    result["document_id"] = record.document_id
    result["filename"] = record.filename
    result["selector"] = {"type": selector_type, "spec": spec}
    return result


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@router.post("/documents/{document_id}/extract")
async def extract(
    request: Request, document_id: str, payload: ExtractRequest
) -> dict[str, Any]:
    settings = get_settings()
    _check_rate(request, settings)
    record = _document(request, document_id)
    return await run_in_threadpool(
        _run,
        record,
        payload.selector.type,
        payload.selector.spec,
        payload.mode,
        settings,
    )


@router.get("/documents/{document_id}/extract/export")
async def export_extract(
    request: Request,
    document_id: str,
    format: Literal["pdf", "docx"] = Query(...),
    type: Literal["pages", "items", "paragraphs", "heading", "chapter", "whole"] = Query(...),
    spec: Optional[str] = Query(default=None, max_length=200),
    mode: Literal["source_passages", "both", "ai_study_material"] = Query(
        default="source_passages"
    ),
) -> Response:
    """Regenerate the selection and render it. Summaries come from the cache."""
    settings = get_settings()
    _check_rate(request, settings)
    record = _document(request, document_id)
    result = await run_in_threadpool(_run, record, type, spec, mode, settings)
    label = "Selected text" if type == "whole" else f"{type.title()} {spec or ''}".strip()
    payload = svc.to_topic_payload(
        filename=record.filename, mode=mode, label=label, result=result
    )
    try:
        data = await run_in_threadpool(render, payload, format)
    except Exception as exc:  # noqa: BLE001
        logger.warning("extract export failed error_type=%s", type(exc).__name__)
        raise ApiError("export_failed", "The export could not be generated.", 500) from exc
    filename = export_filename(payload, format)
    return Response(
        content=data,
        media_type=EXPORT_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
