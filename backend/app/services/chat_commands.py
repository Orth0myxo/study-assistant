from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.schemas.chat_command import (
    ChatCommand,
    ChatCommandRequest,
    ChatCommandSelector,
)
from app.services.ai_client import AiError, AiInvalidResponse, AiNotConfigured

logger = logging.getLogger(__name__)
_MODE_VERBATIM = re.compile(r"\b(verbatim|exact(?:ly)?|as written|original text)\b", re.I)
_MODE_BOTH = re.compile(r"\b(with|plus|and)\s+(?:a\s+)?(?:short\s+)?(?:summary|explanation)\b|\bsummary\s+and\s+original\b", re.I)
_MODE_AI = re.compile(r"\b(summary only|summar(?:y|ize|ise) only|explain|no original text|without the original)\b", re.I)


def _mode(message: str, *, summary_default: bool = False) -> str:
    if _MODE_BOTH.search(message) and _MODE_VERBATIM.search(message):
        return "both"
    if _MODE_AI.search(message):
        return "ai_study_material"
    if _MODE_VERBATIM.search(message):
        return "source_passages"
    return "ai_study_material" if summary_default else "source_passages"


def _export_format(message: str) -> str | None:
    if re.search(r"\b(word|docx|document file)\b", message, re.I):
        return "docx"
    if re.search(r"\b(pdf|printable)\b", message, re.I):
        return "pdf"
    return None


def _empty_selector(kind: str, **values: Any) -> ChatCommandSelector:
    return ChatCommandSelector(
        type=kind,
        topic=values.get("topic"),
        pages=values.get("pages", []),
        chapter=values.get("chapter"),
        items=values.get("items", []),
    )


def fast_path(message: str) -> ChatCommand | None:
    """Deterministic handling for common commands; never calls a provider."""
    text = " ".join(message.strip().split())
    if not text:
        return ChatCommand(
            action="clarify", selector=_empty_selector("whole"),
            mode="source_passages", export_format=None,
            question="What would you like to study?",
        )

    page_match = re.search(
        r"\bpages?\s+((?:\d+\s*(?:(?:-|\bto\b)\s*\d+)?\s*(?:(?:,|\band\b)\s*)?)*)",
        text,
        re.I,
    )
    if page_match:
        raw = page_match.group(1)
        spans = [match.span() for match in re.finditer(r"\d+\s*(?:-|\bto\b)\s*\d+", raw, re.I)]
        pairs = [(int(m.group(1)), int(m.group(2))) for m in re.finditer(r"(\d+)\s*(?:-|\bto\b)\s*(\d+)", raw, re.I)]
        residue = list(raw)
        for start, end in spans:
            residue[start:end] = " " * (end - start)
        pairs.extend((int(value), int(value)) for value in re.findall(r"\d+", "".join(residue)))
        if pairs:
            fmt = _export_format(text)
            return ChatCommand(
                action="extract", selector=_empty_selector("pages", pages=pairs),
                mode=_mode(text), export_format=fmt, question=None,
            )

    chapter = re.search(r"\bchapter\s+([\w.-]+)", text, re.I)
    if chapter:
        return ChatCommand(
            action="extract", selector=_empty_selector("headings", chapter=chapter.group(1)),
            mode=_mode(text), export_format=_export_format(text), question=None,
        )

    items = re.search(r"\b(?:questions?|items?)\s+(\d+)\s*(?:-|\bto\b)\s*(\d+)", text, re.I)
    if items:
        pair = (int(items.group(1)), int(items.group(2)))
        return ChatCommand(
            action="extract", selector=_empty_selector("items", items=[pair]),
            mode=_mode(text), export_format=_export_format(text), question=None,
        )

    if re.search(r"\b(?:summari[sz]e|summary|overview)\b", text, re.I) and re.search(
        r"\b(?:everything|whole|entire|all of it|full material|whole material)\b", text, re.I
    ):
        return ChatCommand(
            action="summarize_whole", selector=_empty_selector("whole"),
            mode=_mode(text, summary_default=True), export_format=_export_format(text), question=None,
        )

    if re.fullmatch(r"(?:hi|hello|hey|help|thanks|thank you)[.!? ]*", text, re.I):
        return ChatCommand(
            action="help", selector=_empty_selector("whole"), mode="source_passages",
            export_format=None, question=None,
        )

    topic_match = re.search(
        r"^(?:please\s+)?(?:find|extract|locate|search(?:\s+for)?|look\s+up|identify)\s+(?:the\s+)?(?:topic\s+)?(?:about\s+|on\s+)?(.+?)\s*[?.!]*$",
        text, re.I,
    )
    if topic_match:
        topic = topic_match.group(1).strip(" \t.,!?;:")
        if topic:
            return ChatCommand(
                action="extract", selector=_empty_selector("topic", topic=topic),
                mode=_mode(text), export_format=_export_format(text), question=None,
            )

    export_format = _export_format(text)
    if export_format and re.search(r"\b(export|download|save|file)\b", text, re.I):
        return ChatCommand(
            action="export", selector=_empty_selector("whole"),
            mode="source_passages", export_format=export_format, question=None,
        )
    return None


def regex_fallback(message: str) -> ChatCommand:
    """Conservative backend equivalent of the mobile regex intent fallback."""
    text = " ".join(message.strip().split())
    fast = fast_path(text)
    if fast is not None:
        return fast
    whole = re.search(r"\b(?:whole|entire|everything|all of it|full material)\b", text, re.I)
    if whole and re.search(r"\b(?:summari[sz]|explain|teach|study|notes?)\b", text, re.I):
        return ChatCommand(
            action="summarize_whole", selector=_empty_selector("whole"),
            mode=_mode(text, summary_default=True), export_format=_export_format(text), question=None,
        )
    topic_match = re.search(
        r"^(?:what|who)\s+(?:is|are)\s+(.+)$|^(?:explain|tell me about|notes on|revise)\s+(.+)$|^topics?\s*[:\-]\s*(.+)$",
        text, re.I,
    )
    if topic_match:
        topic = next((value for value in topic_match.groups() if value), "").strip(" .?!")
        if topic:
            return ChatCommand(
                action="extract", selector=_empty_selector("topic", topic=topic),
                mode=_mode(text), export_format=_export_format(text), question=None,
            )
    return ChatCommand(
        action="clarify", selector=_empty_selector("whole"), mode="source_passages",
        export_format=None, question="Could you name a topic, page range, or study action?",
    )


def _prompt(message: str, context: dict[str, Any]) -> str:
    schema = ChatCommand.model_json_schema()
    return (
        "Convert the user's single request to a command. Return JSON only matching this schema. "
        "Do not execute commands. Never infer document contents; context contains names and metadata only. "
        "Mode rules: verbatim/exact/as written means source_passages; verbatim with summary/explanation means both; "
        "summary only/explain/no original text means ai_study_material; if unclear choose source_passages.\n"
        f"Schema: {json.dumps(schema, separators=(',', ':'))}\n"
        f"Context: {json.dumps(context, ensure_ascii=False, separators=(',', ':'))}\n"
        f"User request: {message[:500]}"
    )


def groq_command(
    payload: ChatCommandRequest,
    settings: Settings,
    *,
    http_client: httpx.Client | None = None,
) -> ChatCommand:
    if not settings.groq_api_key or not settings.groq_command_model:
        raise AiNotConfigured("Groq command understanding is not configured.")
    own = http_client is None
    client = http_client or httpx.Client()
    request_body = {
        "model": settings.groq_command_model,
        "messages": [
            {"role": "system", "content": "You are a strict command parser. Output JSON only."},
            {"role": "user", "content": _prompt(payload.message[:500], payload.context.model_dump())},
        ],
        "temperature": 0,
        "max_tokens": 350,
        "response_format": {"type": "json_object"},
    }
    try:
        try:
            response = client.post(
                f"{settings.groq_base_url.rstrip('/')}/chat/completions",
                json=request_body,
                headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
                timeout=settings.groq_request_timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise AiError("Groq command request timed out.", code="ai_timeout", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise AiError("Groq command service is unavailable.", code="ai_provider_error", retryable=True) from exc
        if response.status_code == 429:
            raise AiError("Groq command service is rate limited.", code="ai_rate_limited", retryable=True)
        if response.status_code >= 400:
            raise AiError("Groq command service rejected the request.", code="ai_provider_error", retryable=False)
        try:
            raw = response.json()["choices"][0]["message"]["content"]
            parsed = ChatCommand.model_validate_json(raw)
        except (ValueError, KeyError, IndexError, TypeError, ValidationError) as exc:
            raise AiInvalidResponse("The command parser returned invalid JSON.", code="ai_invalid_response") from exc
        return parsed
    finally:
        if own:
            client.close()


def resolve_command(payload: ChatCommandRequest, settings: Settings) -> ChatCommand:
    fast = fast_path(payload.message[:500])
    if fast is not None:
        return fast
    try:
        return groq_command(payload, settings)
    except AiError as exc:
        logger.info("chat command fallback code=%s", exc.code)
        return regex_fallback(payload.message[:500])
    except Exception as exc:  # noqa: BLE001 - a parser outage must not break chat
        logger.info("chat command fallback error_type=%s", type(exc).__name__)
        return regex_fallback(payload.message[:500])
