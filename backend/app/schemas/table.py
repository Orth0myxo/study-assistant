from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class TableReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source_id: str = Field(min_length=1, max_length=64)
    image_index: int = Field(default=0, ge=0, le=9)
    image_indices: list[int] | None = Field(
        default=None, min_length=1, max_length=10
    )
    engine: Literal["local", "ai"] = "local"


class TableTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=200_000)


class TableWarning(BaseModel):
    code: str
    message: str


class TableResultResponse(BaseModel):
    result_id: str
    source_id: str
    image_index: int
    engine: Literal["local", "ai", "text"]
    title: str = ""
    has_header: bool = True
    columns: list[str]
    rows: list[list[str]]
    confidence: list[list[float]]
    low_confidence: list[list[bool]]
    row_count: int
    column_count: int
    warnings: list[TableWarning] = Field(default_factory=list)
    created_at: float
    expires_at: float


class TableExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["csv", "xlsx", "docx"]
    title: str | None = None
    columns: list[str] | None = None
    rows: list[list[str]] | None = None


class TableExportErrorData(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class TableExportJobResponse(BaseModel):
    job_id: str
    result_id: str
    format: Literal["csv", "xlsx", "docx"]
    status: Literal["queued", "running", "completed", "failed"]
    file_id: str | None = None
    name: str | None = None
    error: TableExportErrorData | None = None
