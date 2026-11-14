from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.config import get_settings
from app.errors import ApiError
from app.schemas.document_edit import DocumentEditRequest, DocumentEditResponse
from app.services.ai_client import AiError, GeminiClient
from app.services.document_edit import (
    apply_safe_word_removals,
    require_approved_edit_review,
)
from app.services.job_store import jobs
from app.services.storage import require_ready_document, storage

router = APIRouter(prefix="/api/v1/documents", tags=["document-editing"])


@router.post("/{document_id}/edit", response_model=DocumentEditResponse)
def edit_document(document_id: str, payload: DocumentEditRequest, request: Request) -> DocumentEditResponse:
    record = storage.get_for_owner(document_id, request.state.user_id)
    if record is None:
        raise ApiError("document_not_found", "The document was not found or has expired.", 404)
    record = require_ready_document(record)
    if not record.available or not record.text_extractable:
        raise ApiError("document_unavailable", "This document has no available editable text.", 409)
    original = "\n\n".join(str(section.get("raw_text") or "") for section in record.sections).strip()
    client: GeminiClient | None = None
    try:
        client = GeminiClient(get_settings())
        proposals = client.propose_safe_word_removals(
            source_text=original, count=payload.remove_words
        )
        edited, removed = apply_safe_word_removals(
            original, payload.remove_words, proposals
        )
        review = client.review_document_edit(
            original_text=original,
            edited_text=edited,
            removals=proposals,
        )
        require_approved_edit_review(review)
    except AiError as exc:
        raise ApiError(exc.code, exc.message, exc.status_code) from exc
    finally:
        if client is not None:
            client.close()
    job_id = jobs.new_job_id()
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    jobs.put(job_id, {
        "job_id": job_id, "owner_id": request.state.user_id,
        "document_id": document_id, "filename": record.filename,
        "format": record.format, "mode": "extracted_text_only",
        "status": "completed", "created_at": now, "updated_at": now,
        "page_count": record.page_count, "item_count": 1,
        "items": [{"index": 0, "source_refs": [], "text": edited, "summary": None, "warnings": []}],
        "document_summary": None, "warnings": [], "error": None,
        "progress": {"processed_units": 1, "total_units": 1},
        "document_available": True, "edit": {"removed_count": len(removed)},
    })
    return DocumentEditResponse(
        job_id=job_id, document_id=document_id, filename=record.filename,
        original_text=original, edited_text=edited,
        removed_words=removed, removed_count=len(removed),
    )
