from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr


class AgentAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: StrictStr = Field(min_length=1, max_length=100)
    kind: StrictStr = Field(min_length=1, max_length=20)
    name: StrictStr = Field(min_length=1, max_length=200)


class AgentChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    message: StrictStr = Field(min_length=1, max_length=5000)
    conversation_id: StrictStr | None = Field(default=None, max_length=100)
    attachments: list[AgentAttachment] = Field(default_factory=list, max_length=20)


class AgentChatResponse(BaseModel):
    reply: str
    conversation_id: str
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    status: str | None = None
    usage: dict[str, int]


class AgentExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    format: Literal["docx", "pdf"]


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
