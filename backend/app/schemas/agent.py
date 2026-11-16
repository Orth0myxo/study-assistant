from __future__ import annotations

from typing import Any

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
    usage: dict[str, int]
