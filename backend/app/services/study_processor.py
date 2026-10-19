from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.services.ai_client import AiClient, AiError, AiNotConfigured, get_ai_client
from app.services.storage import StoredDocument

AI_MODES = ("text_and_summaries", "summaries_only")
STUDY_MODES = ("extracted_text_only",) + AI_MODES


@dataclass
class StudyOutcome:
    items: list[dict[str, Any]] = field(default_factory=list)
    document_summary: str | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)
    status: str = "completed"
    error: dict[str, str] | None = None
    total_units: int = 0
    processed_units: int = 0


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


def _split_text(text: str, limit: int) -> list[str]:
    """Split text into pieces no longer than limit, preferring sentence boundaries."""
    if limit <= 0 or len(text) <= limit:
        return [text]
    pieces: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max(window.rfind(". "), window.rfind("\n"), window.rfind("; "))
        if cut < limit // 2:
            cut = window.rfind(" ")
        if cut <= 0:
            cut = limit
        else:
            cut += 1
        pieces.append(remaining[:cut].strip())
        remaining = remaining[cut:].lstrip()
    if remaining.strip():
        pieces.append(remaining.strip())
    return [piece for piece in pieces if piece]


@dataclass
class _Unit:
    text: str
    refs: list[dict[str, Any]]
    label: str


def _build_units(
    sections: list[dict[str, Any]], max_chars: int
) -> list[_Unit]:
    units: list[_Unit] = []
    parts: list[str] = []
    refs: list[dict[str, Any]] = []
    length = 0

    def flush() -> None:
        nonlocal parts, refs, length
        if parts:
            units.append(
                _Unit(text="\n\n".join(parts), refs=list(refs), label=_unit_label(refs))
            )
        parts = []
        refs = []
        length = 0

    for section in sections:
        text = (section.get("raw_text") or "").strip()
        anchor = section.get("anchor") or {}
        if not text:
            continue
        for piece in _split_text(text, max_chars):
            if length and length + len(piece) + 2 > max_chars:
                flush()
            if anchor not in refs:
                refs.append(anchor)
            parts.append(piece)
            length += len(piece) + 2
    flush()
    return units


def _unit_label(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "document section"
    labels = [_ref_label(anchor) for anchor in refs]
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} to {labels[-1]}"


def process_document(
    record: StoredDocument,
    mode: str,
    settings: Settings,
    ai_client: AiClient | None = None,
) -> StudyOutcome:
    sections = record.sections or []

    if mode == "extracted_text_only":
        items = [
            {
                "index": index,
                "source_refs": [section.get("anchor") or {}],
                "text": section.get("raw_text") or "",
                "summary": None,
                "warnings": [],
            }
            for index, section in enumerate(sections)
            if (section.get("raw_text") or "").strip()
        ]
        return StudyOutcome(
            items=items,
            status="completed",
            total_units=len(items),
            processed_units=len(items),
        )

    client = ai_client if ai_client is not None else get_ai_client(settings)
    units = _build_units(sections, settings.max_summary_chunk_chars)
    include_text = mode == "text_and_summaries"

    items: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    summaries: list[str] = []
    failed = 0

    for index, unit in enumerate(units):
        item_warnings: list[dict[str, str]] = []
        summary: str | None = None
        try:
            summary = client.summarize(
                source_text=unit.text,
                source_label=unit.label,
                purpose="section",
            )
        except AiNotConfigured:
            raise
        except AiError as exc:
            failed += 1
            message = (
                f"Could not summarise {unit.label}: {exc.message}"
                if unit.label
                else exc.message
            )
            item_warnings.append({"code": exc.code, "message": message})
            warnings.append({"code": exc.code, "message": message})
        else:
            summaries.append(summary)

        items.append(
            {
                "index": index,
                "source_refs": list(unit.refs),
                "text": unit.text if include_text else None,
                "summary": summary,
                "warnings": item_warnings,
            }
        )

    document_summary: str | None = None
    if summaries and len(units) > 1:
        combined = "\n\n".join(summaries)
        if len(combined) > settings.max_summary_chunk_chars:
            combined = combined[: settings.max_summary_chunk_chars]
        try:
            document_summary = client.summarize(
                source_text=combined,
                source_label="whole document",
                purpose="overview",
            )
        except AiNotConfigured:
            raise
        except AiError as exc:
            warnings.append(
                {
                    "code": exc.code,
                    "message": f"Could not build the overall summary: {exc.message}",
                }
            )
    elif summaries:
        document_summary = summaries[0]

    status = "completed"
    error: dict[str, str] | None = None
    if not summaries and units:
        status = "failed"
        first = warnings[0] if warnings else None
        error = (
            {"code": first["code"], "message": first["message"]}
            if first is not None
            else {"code": "ai_provider_error", "message": "Summarization failed."}
        )
    elif failed:
        status = "partial"

    return StudyOutcome(
        items=items,
        document_summary=document_summary,
        warnings=warnings,
        status=status,
        error=error,
        total_units=len(units),
        processed_units=len(items),
    )
