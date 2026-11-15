from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Warning(BaseModel):
    code: str
    message: str


class ExtractionInfo(BaseModel):
    status: Literal["pending", "ok", "no_text", "partial"]
    text_extractable: bool
    section_count: int = Field(ge=0)
    total_chars: int = Field(ge=0)


class OcrProgressResponse(BaseModel):
    document_id: str
    format: Literal["pdf", "docx"]
    page_count: int | None = None
    status: Literal["not_needed", "pending", "complete", "incomplete"]
    total_pages: int = Field(ge=0)
    needs_ocr_pages: int = Field(ge=0)
    completed_pages: int = Field(ge=0)
    pending_pages: list[int] = Field(default_factory=list)
    failed_pages: list[int] = Field(default_factory=list)
    warnings: list[Warning] = Field(default_factory=list)


class OcrPrepareRequest(BaseModel):
    pages: list[int] | None = Field(default=None, max_length=500)


class OcrBatchResponse(OcrProgressResponse):
    attempted_pages: list[int] = Field(default_factory=list)
    completed_this_batch: list[int] = Field(default_factory=list)
    failed_this_batch: list[int] = Field(default_factory=list)


class IngestProgress(BaseModel):
    processed_units: int = Field(ge=0)
    total_units: int = Field(ge=0)
    label: str | None = None


class IngestError(BaseModel):
    code: str
    message: str


class DocumentResponse(BaseModel):
    document_id: str
    filename: str
    format: Literal["pdf", "docx"]
    size_bytes: int = Field(ge=0)
    page_count: int | None = None
    status: Literal["processing", "ready", "failed"] = "ready"
    extraction: ExtractionInfo
    warnings: list[Warning] = Field(default_factory=list)
    progress: IngestProgress = Field(default_factory=IngestProgress)
    error: IngestError | None = None
