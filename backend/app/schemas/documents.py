from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Warning(BaseModel):
    code: str
    message: str


class ExtractionInfo(BaseModel):
    status: Literal["ok", "no_text", "partial"]
    text_extractable: bool
    section_count: int = Field(ge=0)
    total_chars: int = Field(ge=0)


class DocumentResponse(BaseModel):
    document_id: str
    filename: str
    format: Literal["pdf", "docx"]
    size_bytes: int = Field(ge=0)
    page_count: int | None = None
    extraction: ExtractionInfo
    warnings: list[Warning] = Field(default_factory=list)
