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
_MODE_VERBATIM = re.compile(r"\b(verbatim|exact(?:ly)?|as written|original text|source text)\b", re.I)
_MODE_BOTH = re.compile(r"\b(with|plus|and)\s+(?:a\s+)?(?:short\s+)?(?:summary|explanation)\b|\bsummary\s+and\s+original\b", re.I)
_MODE_AI = re.compile(r"\b(summary only|summar(?:y|ize|ise) only|explain|no original text|without the original)\b", re.I)
_PAGE_REFERENCE = re.compile(
    r"\b(?:pages?|pp?\.?)\s*"
    r"((?:\d+\s*(?:(?:-|\u2013|\u2014|\bto\b|\bthrough\b|\bthru\b)\s*\d+)?"
    r"\s*(?:,\s*(?:and\s+)?|&\s*|\band\s+)?)+)",
    re.I,
)
_PAGE_RANGE = re.compile(
    r"(\d+)\s*(?:-|\u2013|\u2014|\bto\b|\bthrough\b|\bthru\b)\s*(\d+)", re.I
)
_WHOLE_SOURCE = re.compile(
    r"\b(?:whole|entire|full|complete)\s+(?:document|material|notes?|text|file|source)|"
    r"\b(?:the|this|my)\s+(?:document|material|notes?|file)\b|"
    r"\b(?:source text|document text|full text|everything|all of it|"
    r"all (?:the )?(?:exact |original |source )?(?:text|content))|"
    r"\b(?:(?:the|this|my|uploaded)\s+)?(?:pdf|docx|word document|lecture material)\b",
    re.I,
)
_TOPIC_MARKER = re.compile(r"\b(?:about|on|regarding|covering|discussing)\b", re.I)
_EXACT_ONLY = re.compile(
    r"^(?:please\s+)?(?:give|show|return|get|extract|fetch|copy|send)?\s*"
    r"(?:me\s+)?(?:the\s+)?(?:verbatim|exact|original)\s+"
    r"(?:text|source|content|material)(?:\s+text)?\s*[.!?]*$",
    re.I,
)


# A short message that is just a name ("Cervical smear") is a topic search. These
# words mark a message as being about the app, the file or the conversation instead,
# so those still get the clarifying question.
_MAX_BARE_TOPIC_WORDS = 6
_WORD = re.compile(r"[\w'’-]+")
_NON_TOPIC_FIRST_WORDS = frozenset(
    "i i'm im i've ive i'd we you my me can could would will should please pls "
    "need want give show get tell let lets let's what whats what's which how why "
    "when where who is are was were do does did has have there this that it yes "
    "no ok okay yeah sure thanks thank hi hello hey help also then now just exact "
    "full only cancel stop undo retry again continue next back clear reset".split()
)
_NON_TOPIC_WORDS = frozenset(
    "pdf docx doc document documents file files image images picture pictures photo "
    "photos pic pics notes note material text textbook verbatim summary summaries "
    "summarize summarise passage passages page pages export download topic topics "
    "whole entire everything answer answers".split()
)


def _bare_topic(text: str) -> str | None:
    """Return the text as a topic when it is only a short name, else None."""
    candidate = text.strip(" \t.,!?;:")
    words = _WORD.findall(candidate.lower())
    if len(candidate) < 3 or not 1 <= len(words) <= _MAX_BARE_TOPIC_WORDS:
        return None
    if words[0] in _NON_TOPIC_FIRST_WORDS or any(w in _NON_TOPIC_WORDS for w in words):
        return None
    return candidate


def _mode(message: str, *, summary_default: bool = False) -> str:
    if _MODE_BOTH.search(message) and _MODE_VERBATIM.search(message):
        return "both"
    if _MODE_AI.search(message):
        return "ai_study_material"
    if _MODE_VERBATIM.search(message):
        return "source_passages"
    return "ai_study_material" if summary_default else "source_passages"


def _page_pairs(text: str) -> list[tuple[int, int]]:
    pairs: list[tuple[int, int]] = []
    for reference in _PAGE_REFERENCE.finditer(text):
        raw = reference.group(1)
        spans = [match.span() for match in _PAGE_RANGE.finditer(raw)]
        residue = list(raw)
        for start, end in spans:
            residue[start:end] = " " * (end - start)
        pairs.extend(
            (int(match.group(1)), int(match.group(2)))
            for match in _PAGE_RANGE.finditer(raw)
        )
        pairs.extend(
            (int(value), int(value))
            for value in re.findall(r"\d+", "".join(residue))
        )
    deduped: list[tuple[int, int]] = []
    for pair in pairs:
        if pair not in deduped:
            deduped.append(pair)
    return deduped


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

    pairs = _page_pairs(text)
    if pairs:
        return ChatCommand(
            action="extract", selector=_empty_selector("pages", pages=pairs),
            mode=_mode(text), export_format=_export_format(text), question=None,
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

    if (
        _MODE_VERBATIM.search(text)
        and not _MODE_AI.search(text)
        and not _TOPIC_MARKER.search(text)
        and not re.search(r"\b(?:image|picture|photo)\b", text, re.I)
        and (_WHOLE_SOURCE.search(text) or _EXACT_ONLY.fullmatch(text))
    ):
        return ChatCommand(
            action="summarize_whole", selector=_empty_selector("whole"),
            mode=_mode(text), export_format=_export_format(text), question=None,
        )

    if re.search(r"\b(?:summari[sz]e|summary|overview)\b", text, re.I) and (
        re.search(
            r"\b(?:everything|whole|entire|all of it|full material|whole material)\b", text, re.I
        )
        or _WHOLE_SOURCE.search(text)
    ):
        return ChatCommand(
            action="summarize_whole", selector=_empty_selector("whole"),
            mode=_mode(text, summary_default=True), export_format=_export_format(text), question=None,
        )

    if re.fullmatch(
        r"(?:please\s+)?(?:(?:give\s+me)\s+)?(?:a\s+)?"
        r"(?:brief\s+|short\s+|quick\s+)?(?:summary|summaries)(?:\s+only)?[.!?]*",
        text,
        re.I,
    ):
        return ChatCommand(
            action="summarize_whole", selector=_empty_selector("whole"),
            mode="ai_study_material", export_format=_export_format(text), question=None,
        )

    topic_summary = re.search(
        r"^(?:please\s+)?(?:i\s+(?:need|want)\s+)?(?:give\s+me\s+)?"
        r"(?:a\s+)?(?:brief\s+|short\s+|quick\s+)?"
        r"(?:summari[sz](?:e|ing)?|summary|overview)\s+(?:of|about|on|for)\s+(.+?)\s*[?.!]*$",
        text,
        re.I,
    )
    if topic_summary:
        topic = topic_summary.group(1).strip(" \\t.,!?;:")
        if topic and not _WHOLE_SOURCE.search(topic):
            return ChatCommand(
                action="extract", selector=_empty_selector("topic", topic=topic),
                mode="ai_study_material", export_format=_export_format(text), question=None,
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
                mode=_mode(
                    text,
                    summary_default=bool(re.search(r"\b(?:summari[sz]|summary|overview)\b", text, re.I)),
                ),
                export_format=_export_format(text), question=None,
            )
    contextual_topic = re.search(
        r"^(?:(?:that|the|this)\s+)?(?:part|section|bit)\s+(?:about|on|regarding|discussing|covering)\s+(.+?)\s*[?.!]*$",
        text,
        re.I,
    )
    if contextual_topic:
        topic = contextual_topic.group(1).strip(" \t.,!?;:")
        if topic:
            return ChatCommand(
                action="extract", selector=_empty_selector("topic", topic=topic),
                mode=_mode(text), export_format=_export_format(text), question=None,
            )
    if re.search(r"\b(?:summari[sz]e|summary|overview)\b", text, re.I):
        topic_summary = re.search(
            r"^(?:please\s+)?(?:i\s+(?:need|want)\s+)?(?:give\s+me\s+)?"
            r"(?:a\s+)?(?:brief\s+|short\s+|quick\s+)?"
            r"(?:summari[sz](?:e|ing)?|summary|overview)\s+(?:of|about|on|for)\s+(.+?)\s*[?.!]*$",
            text,
            re.I,
        )
        if topic_summary:
            topic = topic_summary.group(1).strip(" \t.,!?;:")
            if topic and not _WHOLE_SOURCE.search(topic.removesuffix(" only")):
                topic = re.sub(r"\s+only$", "", topic, flags=re.I)
                return ChatCommand(
                    action="extract", selector=_empty_selector("topic", topic=topic),
                    mode="ai_study_material", export_format=_export_format(text), question=None,
                )
    # A request for text associated with a picture has an unclear source and
    # needs review; do not interpret its nearby PDF mention as whole-document.
    if _MODE_VERBATIM.search(text) and re.search(r"\b(?:image|picture|photo)\b", text, re.I):
        return ChatCommand(
            action="clarify", selector=_empty_selector("whole"),
            mode="source_passages", export_format=None,
            question="Do you mean text from the image or passages from the PDF?",
        )
    bare = _bare_topic(text)
    if bare is not None:
        return ChatCommand(
            action="extract", selector=_empty_selector("topic", topic=bare),
            mode="source_passages", export_format=None, question=None,
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
