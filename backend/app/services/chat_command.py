"""Turn one chat sentence into a validated command for the backend.

The model only classifies the request. It never sees document text or chat
history and never produces study content: only the user's message (truncated)
and a few facts about the open document are sent. Output is validated strictly;
anything invalid is discarded so the client can fall back to its own parser.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.ai_client import AiError
from app.services.provider_settings import ProviderSettings

logger = logging.getLogger(__name__)

Action = Literal["extract", "summarize_whole", "export", "clarify", "help"]
SelectorType = Literal[
    "topic", "pages", "items", "chapter", "heading", "paragraphs", "whole"
]
Mode = Literal["source_passages", "both", "ai_study_material"]
ExportFormat = Literal["pdf", "docx"]


class CommandSelector(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: SelectorType
    topic: Optional[str] = Field(default=None, max_length=200)
    pages: Optional[str] = Field(default=None, max_length=100)
    items: Optional[str] = Field(default=None, max_length=100)
    chapter: Optional[str] = Field(default=None, max_length=100)
    heading: Optional[str] = Field(default=None, max_length=200)
    paragraphs: Optional[str] = Field(default=None, max_length=100)

    def required_value(self) -> Optional[str]:
        if self.type == "whole":
            return ""
        value = getattr(self, self.type, None)
        return value.strip() if isinstance(value, str) and value.strip() else None


class ChatCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    selector: Optional[CommandSelector] = None
    mode: Mode = "source_passages"
    export_format: Optional[ExportFormat] = None
    question: Optional[str] = Field(default=None, max_length=300)


class CommandContext(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_names: list[str] = Field(default_factory=list, max_length=5)
    page_count: Optional[int] = Field(default=None, ge=0, le=100000)
    doc_format: Optional[str] = Field(default=None, max_length=10)


SYSTEM_PROMPT = """You convert one chat message from a student into a JSON command \
for a study-document tool. You do not answer questions and you never write study \
content. Reply with a single JSON object only.

Schema:
{"action": "extract"|"summarize_whole"|"export"|"clarify"|"help",
 "selector": {"type": "topic"|"pages"|"items"|"chapter"|"heading"|"paragraphs"|"whole",
              "topic": string|null, "pages": string|null, "items": string|null,
              "chapter": string|null, "heading": string|null, "paragraphs": string|null} | null,
 "mode": "source_passages"|"both"|"ai_study_material",
 "export_format": "pdf"|"docx"|null,
 "question": string|null}

Rules:
- mode "source_passages": the student wants the original text copied exactly \
(words like verbatim, exact, copy, extract, as written) or gives no preference.
- mode "both": original text plus a summary/explanation.
- mode "ai_study_material": a summary or explanation only, no original text.
- selector.type "pages" with pages like "12-15, 20"; "items" for numbered \
questions/items like "5-10"; "chapter" with a number or name; "heading" for a \
named section; "paragraphs" for paragraph numbers; "topic" for a subject the \
student names in words; "whole" for the entire document.
- action "export" when they only ask to download or export the last result; set \
export_format to "pdf" or "docx" (null if unstated). Selector may be null.
- action "summarize_whole": a summary of the entire document.
- action "clarify" with a short "question" when the request is unclear.
- action "help" when they ask what the tool can do.
- Use only values the student gave. Do not invent page numbers or topics."""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _user_prompt(message: str, context: CommandContext) -> str:
    facts: dict[str, Any] = {}
    names = [n[:80] for n in context.document_names[:5] if n]
    if names:
        facts["documents"] = names
    if context.page_count is not None:
        facts["page_count"] = context.page_count
    if context.doc_format:
        facts["format"] = context.doc_format
    return f"Document facts: {json.dumps(facts)}\nStudent message: {json.dumps(message)}"


def _loads(raw: str) -> Optional[dict[str, Any]]:
    text = raw.strip()
    for candidate in (text, (_JSON_OBJECT.search(text) or [None])[0]):
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def validate_command(
    data: dict[str, Any], *, has_document: bool = True
) -> Optional[ChatCommand]:
    """Strict validation. Returns ``None`` for anything unusable."""
    try:
        command = ChatCommand.model_validate(data)
    except ValidationError:
        return None
    if command.action in {"extract", "summarize_whole"} and not has_document:
        return ChatCommand(
            action="clarify", question="Please upload a document first."
        )
    if command.action == "extract":
        selector = command.selector
        if selector is None or selector.required_value() is None:
            return None
    if command.action == "clarify" and not (command.question or "").strip():
        return None
    return command


def interpret_message(
    *,
    client: Any,
    provider: ProviderSettings,
    message: str,
    context: CommandContext,
) -> Optional[ChatCommand]:
    """Ask Groq for a command. ``None`` means 'use the local parser instead'."""
    text = (message or "").strip()[: provider.command_max_message_chars]
    if not text:
        return None
    try:
        raw = client.complete(
            SYSTEM_PROMPT,
            _user_prompt(text, context),
            model=provider.groq_command_model,
            max_tokens=300,
            temperature=0,
            json_mode=True,
            timeout_s=15.0,
            retries=1,
        )
    except AiError as exc:
        logger.info("chat command unavailable code=%s", exc.code)
        return None
    data = _loads(raw)
    if data is None:
        return None
    return validate_command(data, has_document=bool(context.document_names))
