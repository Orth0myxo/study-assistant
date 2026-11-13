from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.config import Settings
from app.services.ai_client import (
    AiClient,
    AiError,
    AiInvalidResponse,
    AiNotConfigured,
    normalize_summary,
)
# Provider chain (Groq -> Gemini) with summary cache. Kept under the old name so
# existing call sites and tests that patch ``get_ai_client`` keep working.
from app.services.ai_chain import AiAllProvidersFailed
from app.services.ai_chain import build_ai_client as get_ai_client
from app.services.provider_settings import ProviderSettings, load_provider_settings
from app.services.storage import StoredDocument
from app.services.summary_cache import CachedAiClient

logger = logging.getLogger(__name__)

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
    diagnostics: dict[str, Any] = field(default_factory=dict)


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


def _batch_summaries(summaries: list[str], limit: int) -> list[str]:
    """Group summaries into batches no larger than limit without dropping text."""
    batches: list[str] = []
    current: list[str] = []
    current_length = 0
    for summary in summaries:
        if not summary:
            continue
        for piece in _split_text(summary, limit):
            piece_length = len(piece)
            if current and current_length + 2 + piece_length > limit:
                batches.append("\n\n".join(current))
                current = []
                current_length = 0
            current.append(piece)
            current_length += (2 if len(current) > 1 else 0) + piece_length
    if current:
        batches.append("\n\n".join(current))
    return batches


def _summarize_overview(
    client: AiClient,
    summaries: list[str],
    limit: int,
    warnings: list[dict[str, str]],
) -> str | None:
    combined = "\n\n".join(summary for summary in summaries if summary)
    if not combined:
        return None

    batches = _batch_summaries(summaries, limit) if len(combined) > limit else [combined]
    parts: list[str] = []
    for index, batch in enumerate(batches, start=1):
        label = (
            "whole document"
            if len(batches) == 1
            else f"document overview part {index}"
        )
        try:
            part = normalize_summary(
                client.summarize(
                    source_text=batch,
                    source_label=label,
                    purpose="overview",
                )
            )
            if not part:
                raise AiInvalidResponse(
                    "The AI provider returned an empty overview.",
                    code="ai_invalid_response",
                    status_code=502,
                )
        except AiNotConfigured:
            raise
        except AiError as exc:
            prefix = (
                "Could not build the overall summary: "
                if len(batches) == 1
                else f"Could not build overall summary part {index}: "
            )
            warnings.append({"code": exc.code, "message": prefix + exc.message})
            if exc.code in {"ai_quota_exhausted", "ai_rate_limited"}:
                break
            continue
        parts.append(part)

    if not parts:
        return None
    if len(parts) < len(batches):
        warnings.append(
            {
                "code": "overview_partial",
                "message": (
                    "The overall summary is incomplete: "
                    f"{len(batches) - len(parts)} of {len(batches)} parts failed."
                ),
            }
        )
    if len(parts) > 1:
        warnings.append(
            {
                "code": "overview_reduced",
                "message": (
                    "The overall summary was assembled from "
                    f"{len(parts)} separately summarized parts because the section "
                    "summaries exceeded the summary size limit."
                ),
            }
        )
    return "\n\n".join(parts)


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


class _PatientClient:
    """Summarizer wrapper for background jobs.

    Waits out short rate limits (bounded by a per-job budget) instead of failing
    every remaining part at once, and stops calling the providers as soon as all of
    them report that their quota is used up.
    """

    def __init__(
        self,
        client: AiClient,
        provider: ProviderSettings,
        sleep: Callable[[float], None],
    ) -> None:
        self._client = client
        self._provider = provider
        self._sleep = sleep
        self.waited = 0.0
        self.quota_error: AiAllProvidersFailed | None = None

    @property
    def hits(self) -> int:
        return getattr(self._client, "hits", 0)

    @property
    def cache_hits(self) -> int:
        return self.hits

    def summarize(
        self, *, source_text: str, source_label: str, purpose: str = "section"
    ) -> str:
        if self.quota_error is not None:
            raise self.quota_error
        attempts = self._provider.summary_rate_limit_retries + 1
        for attempt in range(attempts):
            try:
                return self._client.summarize(
                    source_text=source_text,
                    source_label=source_label,
                    purpose=purpose,
                )
            except AiAllProvidersFailed as exc:
                if exc.quota_only:
                    logger.info("summary stopped: every provider is out of quota")
                    self.quota_error = exc
                    raise
                wait = self._next_wait(exc) if attempt < attempts - 1 else None
                if wait is None:
                    raise
                logger.info(
                    "summary rate limited; waiting %.1fs (attempt %d of %d)",
                    wait,
                    attempt + 1,
                    attempts,
                )
                self._sleep(wait)
        raise AssertionError("unreachable")

    def _next_wait(self, exc: AiAllProvidersFailed) -> float | None:
        provider = self._provider
        hint = exc.wait_hint_s(provider.summary_rate_limit_default_wait_s)
        if hint is None:
            return None
        wait = min(hint, provider.summary_rate_limit_wait_cap_s)
        if wait <= 0 or self.waited + wait > provider.summary_wait_budget_s:
            return None
        self.waited += wait
        return wait


def process_document(
    record: StoredDocument,
    mode: str,
    settings: Settings,
    ai_client: AiClient | None = None,
    *,
    provider: ProviderSettings | None = None,
    sleep: Callable[[float], None] = time.sleep,
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

    provider = provider or load_provider_settings()
    client = _PatientClient(
        CachedAiClient(
            ai_client if ai_client is not None else get_ai_client(settings),
            owner_id=record.owner_id,
            retention_minutes=settings.retention_minutes,
        ),
        provider,
        sleep,
    )
    units = _build_units(sections, settings.max_summary_chunk_chars)
    include_text = mode == "text_and_summaries"

    items: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    summaries: list[str] = []
    failed = 0
    provider_stop_error: AiError | None = None

    for index, unit in enumerate(units):
        item_warnings: list[dict[str, str]] = []
        summary: str | None = None
        hits_before = client.hits
        try:
            if provider_stop_error is not None:
                raise provider_stop_error
            summary = normalize_summary(
                client.summarize(
                    source_text=unit.text,
                    source_label=unit.label,
                    purpose="section",
                )
            )
            if not summary:
                raise AiInvalidResponse(
                    "The AI provider returned an empty summary.",
                    code="ai_invalid_response",
                    status_code=502,
                )
        except AiNotConfigured:
            raise
        except AiError as exc:
            summary = None
            failed += 1
            if exc.code in {"ai_quota_exhausted", "ai_rate_limited"}:
                provider_stop_error = exc
            message = f"Could not summarise {unit.label}: {exc.message}" if unit.label else exc.message
            item_warnings.append({"code": exc.code, "message": message})
            warnings.append({"code": exc.code, "message": message})
        else:
            summaries.append(summary)
            # Space out fresh requests; cached summaries cost the provider nothing.
            fresh = client.hits == hits_before
            if fresh and index < len(units) - 1 and provider.summary_unit_delay_s > 0:
                sleep(provider.summary_unit_delay_s)

        items.append(
            {
                "index": index,
                "source_refs": list(unit.refs),
                # In summary-only mode, keep source for failed sections so a
                # provider outage never leaves the student with no usable text.
                "text": unit.text if include_text or summary is None else None,
                "summary": summary,
                "warnings": item_warnings,
            }
        )

    document_summary: str | None = None
    overview_failed = False
    if summaries and len(units) > 1 and provider_stop_error is None:
        warning_count = len(warnings)
        document_summary = _summarize_overview(
            client,
            summaries,
            settings.max_summary_chunk_chars,
            warnings,
        )
        overview_errors = [
            warning
            for warning in warnings[warning_count:]
            if warning["code"].startswith("ai_")
        ]
        overview_failed = any(
            warning["code"] != "ai_invalid_response"
            for warning in overview_errors
        )
        if overview_errors and not include_text:
            for item, unit in zip(items, units):
                if item["text"] is None:
                    item["text"] = unit.text
    elif summaries:
        document_summary = summaries[0]

    status = "completed"
    error: dict[str, str] | None = None
    if not summaries and units:
        status = (
            "partial"
            if include_text and warnings and warnings[0]["code"] == "ai_quota_exhausted"
            else "failed"
        )
        first = warnings[0] if warnings else None
        error = (
            {"code": first["code"], "message": first["message"]}
            if first is not None
            else {"code": "ai_provider_error", "message": "Summarization failed."}
        )
    elif failed or overview_failed:
        status = "partial"

    return StudyOutcome(
        items=items,
        document_summary=document_summary,
        warnings=warnings,
        status=status,
        error=error,
        total_units=len(units),
        processed_units=len(items),
        diagnostics={"summary_cache_hits": client.cache_hits},
    )
