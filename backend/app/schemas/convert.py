from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

InputKind = Literal["csv", "xlsx", "docx", "pdf", "text"]
OutputKind = Literal["xlsx", "docx", "pdf", "csv"]


class ConversionOption(BaseModel):
    conversion_id: str
    input_kind: InputKind
    output_kind: OutputKind
    label: str
    description: str
    output_label: str = ""
    needs_ai: bool = False
    input_mode: Literal["file", "text"]


class ConversionInput(BaseModel):
    kind: InputKind
    label: str
    accept: list[str] = Field(default_factory=list)
    supports_text: bool = False
    conversions: list[ConversionOption] = Field(default_factory=list)


class ConversionLimits(BaseModel):
    max_file_mb: int
    max_rows: int
    max_columns: int
    max_cell_chars: int
    max_pages: int


class ConversionFormatsResponse(BaseModel):
    inputs: list[ConversionInput]
    limits: ConversionLimits


class ConversionTable(BaseModel):
    title: str = ""
    columns: list[str]
    rows: list[list[str]]


class ConversionFileInfo(BaseModel):
    name: str
    format: OutputKind
    size_bytes: int


class ConversionErrorData(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ConversionJobResponse(BaseModel):
    job_id: str
    conversion_id: str
    input_kind: InputKind
    output_kind: OutputKind
    status: Literal["queued", "running", "completed", "failed"]
    needs_ai: bool = False
    editable: bool = False
    file: ConversionFileInfo | None = None
    table: ConversionTable | None = None
    warnings: list[str] = Field(default_factory=list)
    error: ConversionErrorData | None = None
    created_at: float
    expires_at: float


class ConversionTableExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["xlsx", "docx"]
    title: str | None = Field(default=None, max_length=200)
    columns: list[str] = Field(min_length=1, max_length=200)
    rows: list[list[str]] = Field(default_factory=list, max_length=5000)
