from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.schemas.topic_sources import TopicReviewTopic
from app.services.ai_client import (
    AiClient,
    AiError,
    AiInvalidResponse,
    AiNotConfigured,
    get_ai_client,
    normalize_summary,
)

SOURCE_PASSAGES_MODE = "source_passages"
AI_STUDY_MODE = "ai_study_material"
BOTH_MODE = "both"
AI_OUTPUT_MODES = (AI_STUDY_MODE, BOTH_MODE)
SOURCE_ONLY_SCOPE = "source_only"
SOURCE_AND_BEYOND_SCOPE = "source_and_beyond"


@dataclass(frozen=True)
class SourceGroundedMaterial:
    text: str
    basis_passage_ids: list[str]
    basis_refs: list[dict[str, Any]]


@dataclass(frozen=True)
class BeyondSourceMaterial:
    text: str


@dataclass(frozen=True)
class GeneratedStudyMaterial:
    source_grounded: SourceGroundedMaterial
    beyond_source: BeyondSourceMaterial | None = None


@dataclass
class StudyOutputTopic:
    topic_id: str
    text: str
    match_status: str
    material_status: str
    source_passages: list[Any] = field(default_factory=list)
    study_material: GeneratedStudyMaterial | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)


@dataclass
class StudyOutputOutcome:
    status: str
    topics: list[StudyOutputTopic]
    warnings: list[dict[str, str]] = field(default_factory=list)
    error: dict[str, str] | None = None
    ai_requested: bool = False
    ai_scope: str = SOURCE_ONLY_SCOPE
    ai_configured: bool = False
    generated_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    beyond_generated_count: int = 0
    beyond_failed_count: int = 0


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _ref_label(anchor: dict[str, Any]) -> str:
    if anchor.get("kind") == "pdf_page" and isinstance(anchor.get("page"), int):
        return f"Page {anchor['page']}"
    label = anchor.get("section_label")
    paragraph = anchor.get("paragraph_index")
    parts: list[str] = []
    if label:
        parts.append(f"Section: {label}")
    if isinstance(paragraph, int):
        parts.append(f"paragraph {paragraph}")
    return ", ".join(parts) or "document section"


def _select_passages(matches: list[Any], limit: int) -> list[Any]:
    """Select passages for the AI prompt without ever exceeding ``limit``.

    A single oversized section is truncated to a verbatim prefix so the prompt
    budget is a hard cap. Only the prompt copy is shortened; the passages
    returned to the client remain complete.
    """
    limit = max(int(limit), 1)
    selected: list[Any] = []
    length = 0
    for match in matches:
        label_length = len(_ref_label(match.anchor)) + 3
        block_length = len(match.passage) + label_length
        if selected and length + block_length > limit:
            break
        if not selected and block_length > limit:
            available = max(limit - label_length, 1)
            passage = str(match.passage)
            if available < len(passage):
                match = _truncate_passage(match, available)
            selected.append(match)
            break
        selected.append(match)
        length += block_length
        if length >= limit:
            break
    return selected


def _truncate_passage(match: Any, available: int) -> Any:
    passage = str(match.passage)
    clipped = passage[:available]
    if hasattr(match, "model_copy"):
        return match.model_copy(
            update={
                "passage": clipped,
                "passage_end": min(int(match.passage_end), available),
                "excerpt": str(getattr(match, "excerpt", "") or "")[:available],
                "excerpt_end": min(
                    int(getattr(match, "excerpt_end", 0) or 0), available
                ),
            }
        )
    return match


def _source_label(topic: str, matches: list[Any]) -> str:
    labels = [_ref_label(match.anchor) for match in matches]
    if not labels:
        return f"Topic: {topic}"
    return f"Topic: {topic}; " + "; ".join(labels)


def _source_text(matches: list[Any]) -> str:
    return "\n\n".join(
        f"[{_ref_label(match.anchor)}]\n{match.passage}" for match in matches
    )


def _beyond_context(topic: str, grounded_text: str) -> str:
    return (
        f"Topic: {topic}\n\n"
        "Source-grounded study notes (from the uploaded material):\n"
        f"{grounded_text}"
    )


def _topic_warnings(topic: TopicReviewTopic) -> list[dict[str, str]]:
    return [warning.model_dump() for warning in topic.warnings]


def _skipped_status(topic: TopicReviewTopic) -> str:
    if topic.status == "ambiguous":
        return "skipped_ambiguous"
    if topic.status == "unmatched":
        return "skipped_unmatched"
    return "skipped_no_evidence"


def build_study_output(
    topics: list[TopicReviewTopic],
    *,
    mode: str,
    ai_scope: str,
    settings: Settings,
    ai_client: AiClient | None = None,
) -> StudyOutputOutcome:
    ai_requested = mode in AI_OUTPUT_MODES
    include_passages = mode in (SOURCE_PASSAGES_MODE, BOTH_MODE)
    include_ai = ai_requested
    outcome_warnings: list[dict[str, str]] = []

    output_topics: list[StudyOutputTopic] = []
    for topic in topics:
        output_topics.append(
            StudyOutputTopic(
                topic_id=topic.topic_id,
                text=topic.text,
                match_status=topic.status,
                material_status=(
                    "not_requested" if not include_ai else _skipped_status(topic)
                ),
                source_passages=(
                    list(topic.matches) if include_passages else []
                ),
                warnings=_topic_warnings(topic),
            )
        )

    eligible = [
        (index, topic)
        for index, topic in enumerate(topics)
        if topic.status == "matched" and topic.matches
    ]
    skipped_count = len(topics) - len(eligible) if include_ai else 0

    if not include_ai or not eligible:
        if include_ai and topics:
            outcome_warnings.append(
                _warning(
                    "no_grounded_topics",
                    "No topic has a single strong source match, so no AI study "
                    "material was generated.",
                )
            )
        return StudyOutputOutcome(
            status="completed",
            topics=output_topics,
            warnings=outcome_warnings,
            ai_requested=ai_requested,
            ai_scope=ai_scope,
            ai_configured=settings.ai_configured,
            skipped_count=skipped_count,
        )

    client = ai_client if ai_client is not None else get_ai_client(settings)
    generated_count = 0
    failed_count = 0
    beyond_generated_count = 0
    beyond_failed_count = 0
    first_error: dict[str, str] | None = None

    for index, topic in eligible:
        output = output_topics[index]
        matches = _select_passages(
            list(topic.matches), settings.max_summary_chunk_chars
        )
        source_text = _source_text(matches)
        source_label = _source_label(topic.text, matches)
        topic_warnings = list(output.warnings)

        try:
            grounded = normalize_summary(
                client.summarize(
                    source_text=source_text,
                    source_label=source_label,
                    purpose="topic",
                )
            )
            if not grounded:
                raise AiInvalidResponse(
                    "The AI provider returned an empty study summary.",
                    code="ai_invalid_response",
                    status_code=502,
                )
        except AiNotConfigured:
            raise
        except AiError as exc:
            failed_count += 1
            message = (
                f"Could not generate study material for {topic.text}: "
                f"{exc.message}"
            )
            warning = _warning(exc.code, message)
            topic_warnings.append(warning)
            outcome_warnings.append(warning)
            if first_error is None:
                first_error = warning
            output.material_status = "ai_failed"
            output.warnings = topic_warnings
            continue

        beyond: BeyondSourceMaterial | None = None
        if ai_scope == SOURCE_AND_BEYOND_SCOPE:
            try:
                beyond_text = normalize_summary(
                    client.summarize(
                        source_text=_beyond_context(topic.text, grounded),
                        source_label=topic.text,
                        purpose="topic_beyond",
                    )
                )
                if not beyond_text:
                    raise AiInvalidResponse(
                        "The AI provider returned an empty beyond-source study "
                        "note.",
                        code="ai_invalid_response",
                        status_code=502,
                    )
            except AiNotConfigured:
                raise
            except AiError as exc:
                beyond_failed_count += 1
                warning = _warning(
                    exc.code,
                    "Could not generate the optional beyond-source study note "
                    f"for {topic.text}: {exc.message}",
                )
                topic_warnings.append(warning)
                outcome_warnings.append(warning)
            else:
                beyond_generated_count += 1
                beyond = BeyondSourceMaterial(text=beyond_text)

        generated_count += 1
        output.material_status = "generated"
        output.study_material = GeneratedStudyMaterial(
            source_grounded=SourceGroundedMaterial(
                text=grounded,
                basis_passage_ids=[
                    match.passage_id for match in matches
                ],
                basis_refs=[dict(match.anchor) for match in matches],
            ),
            beyond_source=beyond,
        )
        output.warnings = topic_warnings

    status = "completed"
    if failed_count and generated_count == 0:
        status = "failed"
    elif failed_count or beyond_failed_count:
        status = "partial"

    return StudyOutputOutcome(
        status=status,
        topics=output_topics,
        warnings=outcome_warnings,
        error=first_error if status == "failed" else None,
        ai_requested=ai_requested,
        ai_scope=ai_scope,
        ai_configured=settings.ai_configured,
        generated_count=generated_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        beyond_generated_count=beyond_generated_count,
        beyond_failed_count=beyond_failed_count,
    )
