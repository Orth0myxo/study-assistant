"""Verbatim range extraction with optional AI summaries.

Original text always comes from stored ``Section.raw_text`` via code. The AI is
only called for the summary modes, on bounded chunks, through the cached
provider chain, and its failure never removes the original text.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.services.ai_chain import LIMIT_REACHED_MESSAGE, AiAllProvidersFailed
from app.services.ai_client import AiError
from app.services.provider_settings import ProviderSettings
from app.services.range_extraction import (
    RangeError,
    RangeSelection,
    chunk_passages,
    extract_range,
)

logger = logging.getLogger(__name__)

MODES = ("source_passages", "both", "ai_study_material")
SELECTOR_TYPES = ("pages", "items", "paragraphs", "heading", "chapter", "whole")


def select(
    *,
    document_id: str,
    sections: list[dict[str, Any]],
    page_count: Optional[int],
    selector_type: str,
    spec: Optional[str],
) -> RangeSelection:
    if selector_type == "whole":
        passages = [
            {
                "passage_id": f"{document_id}:{int(s.get('index', i))}",
                "section_index": int(s.get("index", i)),
                "anchor": dict(s.get("anchor") or {}),
                "label": _label(s),
                "text": str(s.get("raw_text") or ""),
                "item_number": None,
            }
            for i, s in enumerate(sections)
            if str(s.get("raw_text") or "").strip()
        ]
        if not passages:
            raise RangeError("no_text", "This document has no extractable text.")
        return RangeSelection(passages, [])
    return extract_range(
        document_id=document_id,
        sections=sections,
        page_count=page_count,
        selector_type=selector_type,
        spec=spec,
        query=spec,
    )


def _label(section: dict[str, Any]) -> str:
    from app.services.range_extraction import ref_label

    return ref_label(dict(section.get("anchor") or {}))


def build_result(
    *,
    selection: RangeSelection,
    mode: str,
    client: Any,
    provider: ProviderSettings,
) -> dict[str, Any]:
    warnings = [dict(w) for w in selection.warnings]
    summaries: list[dict[str, Any]] = []
    ai_used = False
    cached = 0
    summaries_incomplete = False

    if mode != "source_passages":
        chunks = chunk_passages(selection.passages, provider.summary_chunk_chars)
        if len(chunks) > provider.summary_max_chunks:
            warnings.append(
                {
                    "code": "summary_truncated",
                    "message": (
                        f"Only the first {provider.summary_max_chunks} of "
                        f"{len(chunks)} parts were summarised. Request a smaller "
                        "range for the rest."
                    ),
                }
            )
            chunks = chunks[: provider.summary_max_chunks]
            summaries_incomplete = True
        if client is None:
            warnings.append(_limit_warning("ai_not_configured"))
            summaries_incomplete = True
        else:
            for chunk in chunks:
                hits_before = getattr(client, "hits", 0)
                try:
                    text = client.summarize(
                        source_text=chunk.text,
                        source_label=chunk.label,
                        purpose="section",
                    )
                except AiAllProvidersFailed:
                    warnings.append(_limit_warning("ai_quota_exhausted"))
                    summaries_incomplete = True
                    break
                except AiError as exc:
                    logger.info("summary failed code=%s", exc.code)
                    warnings.append(_limit_warning("ai_failed"))
                    summaries_incomplete = True
                    break
                ai_used = True
                cached += getattr(client, "hits", 0) - hits_before
                summaries.append(
                    {
                        "label": chunk.label,
                        "text": text,
                        "passage_ids": chunk.passage_ids,
                    }
                )

    # Summary-only requests still return the originals when summaries are
    # missing or incomplete, so the user never loses unsummarised source text.
    include_passages = (
        mode != "ai_study_material" or not summaries or summaries_incomplete
    )
    return {
        "mode": mode,
        "passages": selection.passages if include_passages else [],
        "summaries": summaries,
        "warnings": warnings,
        "ai": {"used": ai_used, "cached_chunks": cached},
    }


def _limit_warning(code: str) -> dict[str, str]:
    if code == "ai_not_configured":
        message = "Summaries are not configured. Original text is still available."
    else:
        message = LIMIT_REACHED_MESSAGE
    return {"code": code, "message": message}


def to_topic_payload(
    *,
    filename: str,
    mode: str,
    label: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Shape a result as the ``topic_study`` payload the exporter already renders.

    One exporter "topic" per summary chunk (or one for all passages), so the
    existing PDF/DOCX code is reused unchanged.
    """
    passages = result["passages"]
    by_id = {p["passage_id"]: p for p in passages}

    def passage_entries(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {"passage": p["text"], "excerpt": p["text"], "anchor": p["anchor"]}
            for p in items
        ]

    topics: list[dict[str, Any]] = []
    if result["summaries"]:
        used: set[str] = set()
        for s in result["summaries"]:
            linked = [
                by_id[i]
                for i in s["passage_ids"]
                if i in by_id and i not in used
            ]
            used.update(p["passage_id"] for p in linked)
            topics.append(
                {
                    "text": s["label"],
                    "match_status": "matched",
                    "material_status": "generated",
                    "source_passages": passage_entries(linked),
                    "study_material": {
                        "source_grounded": {"text": s["text"], "basis_refs": []}
                    },
                    "warnings": [],
                }
            )
        rest = [p for p in passages if p["passage_id"] not in used]
        if rest:
            topics.append(_plain_topic("Other selected text", passage_entries(rest)))
    else:
        topics.append(_plain_topic(label, passage_entries(passages)))
    return {
        "kind": "topic_study",
        "filename": filename,
        "mode": mode,
        "status": "completed",
        "topics": topics,
        "warnings": result["warnings"],
    }


def _plain_topic(text: str, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "text": text,
        "match_status": "matched",
        "material_status": "not_requested",
        "source_passages": entries,
        "study_material": {},
        "warnings": [],
    }
