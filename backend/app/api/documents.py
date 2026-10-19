from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Response, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.errors import ApiError
from app.schemas.documents import DocumentResponse, ExtractionInfo, Warning
from app.services.document_parser import parse_document
from app.services.storage import storage

router = APIRouter(prefix="/api/v1", tags=["documents"])


@router.post(
    "/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(file: UploadFile = File(...)) -> DocumentResponse:
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024

    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ApiError(
            "file_too_large",
            f"The file exceeds the {settings.max_upload_mb} MB upload limit.",
            413,
            {"limit_mb": settings.max_upload_mb},
        )

    filename = file.filename or "upload"
    parsed = await run_in_threadpool(parse_document, filename, data, settings)

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
