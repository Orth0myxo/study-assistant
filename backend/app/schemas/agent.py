from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class AgentAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: StrictStr = Field(min_length=1, max_length=100)
    kind: StrictStr = Field(min_length=1, max_length=20)
    name: StrictStr = Field(min_length=1, max_length=200)


class AgentActiveSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: StrictStr = Field(min_length=1, max_length=100)
    kind: Literal["pdf", "docx", "image", "outline"]


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: StrictStr = Field(min_length=1, max_length=5000)
    conversation_id: StrictStr | None = Field(default=None, max_length=100)
    attachments: list[AgentAttachment] = Field(default_factory=list, max_length=20)
    active_source: AgentActiveSource | None = None


class AgentChatResponse(BaseModel):
    reply: str
    conversation_id: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str | None = None
    usage: dict[str, int]


class AgentResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    pending_id: StrictStr = Field(min_length=1, max_length=100)
    decision: Literal["allow_once", "remember", "deny"]


class AgentResultEdits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: StrictStr | None = Field(default=None, max_length=50_000)
    removed_passage_ids: list[StrictStr] = Field(
        default_factory=list, max_length=500
    )
    ordered_passage_ids: list[StrictStr] | None = Field(
        default=None, max_length=500
    )
    included_pages: list[int] | None = Field(default=None, max_length=500)


class AgentExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["docx", "pdf"]
    allow_incomplete_ocr: bool = False
    edits: AgentResultEdits | None = None


class AgentPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    edits: AgentResultEdits | None = None
    limit: int = Field(default=12, ge=1, le=40)


class AgentPreviewBlock(BaseModel):
    type: str
    text: str = ""
    rows: list[list[str]] | None = None


class AgentPreviewResponse(BaseModel):
    result_id: str
    blocks: list[AgentPreviewBlock]
    total_blocks: int
    has_more: bool


class AgentExportErrorData(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AgentExportJobResponse(BaseModel):
    job_id: str
    result_id: str
    format: Literal["docx", "pdf"]
    status: Literal["queued", "running", "completed", "failed"]
    file_id: str | None = None
    name: str | None = None
    error: AgentExportErrorData | None = None
