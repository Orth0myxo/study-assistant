from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class ChatCommandContext(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    document_names: list[StrictStr] = Field(max_length=20)
    page_count: StrictInt | None
    doc_format: Literal["pdf", "docx"] | None


class ChatCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    message: StrictStr = Field(min_length=1, max_length=5000)
    context: ChatCommandContext


class ChatCommandSelector(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["topic", "pages", "headings", "items", "whole"]
    topic: StrictStr | None
    pages: list[tuple[StrictInt, StrictInt]]
    chapter: StrictStr | None
    items: list[tuple[StrictInt, StrictInt]]


class ChatCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["extract", "summarize_whole", "export", "clarify", "help"]
    selector: ChatCommandSelector
    mode: Literal["source_passages", "both", "ai_study_material"]
    export_format: Literal["pdf", "docx"] | None
    question: StrictStr | None

    @model_validator(mode="after")
    def validate_complete_command(self) -> ChatCommand:
        if self.action == "extract":
            if self.selector.type == "topic" and not self.selector.topic:
                raise ValueError("extract topic command requires selector.topic")
            if self.selector.type == "pages" and not self.selector.pages:
                raise ValueError("extract pages command requires selector.pages")
            if self.selector.type == "headings" and not self.selector.chapter:
                raise ValueError("extract headings command requires selector.chapter")
            if self.selector.type == "items" and not self.selector.items:
                raise ValueError("extract items command requires selector.items")
            if self.selector.type == "whole":
                raise ValueError("extract command cannot use whole selector")
        if self.action == "summarize_whole" and self.selector.type != "whole":
            raise ValueError("whole summary command requires whole selector")
        if self.action == "export" and self.export_format is None:
            raise ValueError("export command requires export_format")
        if self.action == "clarify" and not self.question:
            raise ValueError("clarify command requires question")
        return self
