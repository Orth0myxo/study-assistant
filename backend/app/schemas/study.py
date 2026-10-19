from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

StudyMode = Literal["extracted_text_only", "text_and_summaries", "summaries_only"]
JobStatus = Literal["queued", "running", "completed", "partial", "failed"]
ExportFormat = Literal["pdf", "docx"]


class StudyJobRequest(BaseModel):
    document_id: str = Field(min_length=1)
    mode: StudyMode


class StudyWarning(BaseModel):
    code: str
    message: str


class StudyError(BaseModel):
    code: str
    message: str


class StudyProgress(BaseModel):
    processed_units: int = Field(ge=0)
    total_units: int = Field(ge=0)


class StudyItem(BaseModel):
    index: int = Field(ge=0)
    source_refs: list[dict[str, Any]] = Field(default_factory=list)
    text: str | None = None
    summary: str | None = None
    warnings: list[StudyWarning] = Field(default_factory=list)


class StudyJobResponse(BaseModel):
    job_id: str
    document_id: str
    filename: str
    format: Literal["pdf", "docx"]
    mode: StudyMode
    status: JobStatus
    created_at: str
    updated_at: str
    page_count: int | None = None
    item_count: int = Field(ge=0)
    items: list[StudyItem] = Field(default_factory=list)
    document_summary: str | None = None
    warnings: list[StudyWarning] = Field(default_factory=list)
    error: StudyError | None = None
    progress: StudyProgress
    document_available: bool = True


class StudyJobSummary(BaseModel):
    job_id: str
    document_id: str
    filename: str
    format: Literal["pdf", "docx"]
    mode: StudyMode
    status: JobStatus
    created_at: str
    updated_at: str
    page_count: int | None = None
    item_count: int = Field(ge=0)
    document_available: bool
