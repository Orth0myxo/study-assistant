from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.config import Settings

MIN_MATCH_SCORE = 0.62
AMBIGUITY_GAP = 0.08
MAX_MATCHES_PER_TOPIC = 10
logger = logging.getLogger(__name__)


def _load_synonyms() -> dict[str, set[str]]:
    path = Path(__file__).resolve().parents[1] / "data" / "synonyms.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, set[str]] = {}
    for key, values in payload.items():
        if not isinstance(key, str) or not isinstance(values, list):
            continue
        normalized = normalize_for_match(key)
        aliases = {normalize_for_match(value) for value in values if isinstance(value, str)}
        aliases.discard("")
        if normalized and aliases:
            result[normalized] = aliases
    return result


_SYNONYMS = _load_synonyms()

_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "chapter",
    "for",
    "from",
    "in",
    "into",
    "is",
    "lecture",
    "notes",
    "of",
    "on",
    "or",
    "study",
    "the",
    "to",
    "topic",
    "unit",
    "versus",
    "vs",
    "with",
}


@dataclass(frozen=True)
class PassageMatch:
    passage_id: str
    section_index: int
    anchor: dict[str, Any]
    passage: str
    passage_start: int
    passage_end: int
    score: float
    coverage: float
    phrase_match: bool
    excerpt: str = ""
    excerpt_start: int = 0
    excerpt_end: int = 0
    matched_terms: list[str] = field(default_factory=list)
    has_page_image: bool = False


@dataclass(frozen=True)
class TopicMatchResult:
    text: str
    status: str
    confidence: float
    matches: list[PassageMatch] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def normalize_for_match(text: str) -> str:
    """Normalize text without changing the stored passage itself."""
    normalized = unicodedata.normalize("NFKD", text).casefold()
    ascii_text = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    words = re.findall(r"[a-z0-9]+", ascii_text)
    return " ".join(words)


def _content_terms(normalized: str) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in normalized.split():
        if len(term) < 2 or term in _STOPWORDS or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _stem(term: str) -> str:
    """Apply a deliberately small English inflection normalizer."""
    value = term
    if len(value) > 5 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 5 and value.endswith("ing"):
        value = value[:-3]
        if len(value) > 2 and value[-1] == value[-2]:
            value = value[:-1]
        elif value.endswith(("c", "k", "s", "v")):
            value += "e"
        return value
    if len(value) > 4 and value.endswith("ed"):
        value = value[:-2]
        if len(value) > 2 and value[-1] == value[-2]:
            value = value[:-1]
        elif value.endswith(("c", "k", "s", "v")):
            value += "e"
        return value
    if len(value) > 4 and value.endswith(("ches", "shes", "xes", "zes", "sses")):
        return value[:-2]
    if len(value) > 3 and value.endswith("s") and not value.endswith(("ss", "us", "is")):
        return value[:-1]
    return value


def _variants(term: str) -> set[str]:
    variants = {_stem(term)}
    for alias in _SYNONYMS.get(term, set()):
        variants.update(_stem(part) for part in alias.split())
    return variants


def _ordered_matches(candidates: list[PassageMatch]) -> list[PassageMatch]:
    def anchor_order(match: PassageMatch) -> tuple[str, int, int]:
        if match.anchor.get("kind") == "pdf_page" and isinstance(match.anchor.get("page"), int):
            return ("pdf_page", int(match.anchor["page"]), match.section_index)
        paragraph = match.anchor.get("paragraph_index")
        if isinstance(paragraph, int):
            return ("docx_section", paragraph, match.section_index)
        return ("section", match.section_index, match.section_index)

    return sorted(candidates, key=anchor_order)


def _has_contiguous_run(matches: list[PassageMatch]) -> bool:
    ordered = _ordered_matches(matches)
    for left, right in zip(ordered, ordered[1:]):
        left_kind, left_order, _ = (left.anchor.get("kind"), left.anchor.get("page", left.anchor.get("paragraph_index", left.section_index)), left.section_index)
        right_kind, right_order, _ = (right.anchor.get("kind"), right.anchor.get("page", right.anchor.get("paragraph_index", right.section_index)), right.section_index)
        if left_kind == right_kind and isinstance(left_order, int) and right_order == left_order + 1:
            return True
    return False


def _section_index(section: dict[str, Any], position: int) -> int:
    try:
        return int(section.get("index", position))
    except (TypeError, ValueError):
        return position


def _excerpt(
    raw_text: str, matched_terms: list[str]
) -> tuple[str, int, int]:
    """Return a deterministic verbatim excerpt around the strongest match.

    ``passage`` remains the complete section; the excerpt only narrows the
    provenance window reported to the caller. Offsets are relative to the
    section text and never fabricated.
    """
    if not raw_text:
        return "", 0, 0

    best_count = -1
    best_start = 0
    best_end = min(len(raw_text), 400)
    offset = 0
    for paragraph in raw_text.split("\n"):
        start = offset
        end = offset + len(paragraph)
        offset = end + 1
        if not paragraph.strip():
            continue
        count = sum(
            1
            for term in matched_terms
            if re.search(rf"\b{re.escape(term)}\b", paragraph, re.IGNORECASE)
        )
        if count > best_count:
            best_count = count
            best_start = start
            best_end = end

    paragraph = raw_text[best_start:best_end]
    if len(paragraph) <= 600:
        return paragraph, best_start, best_end

    index = 0
    for term in matched_terms:
        found = re.search(rf"\b{re.escape(term)}\b", paragraph, re.IGNORECASE)
        if found:
            index = found.start()
            break
    window_start = max(0, index - 180)
    window_end = min(len(paragraph), index + 420)
    return (
        paragraph[window_start:window_end],
        best_start + window_start,
        best_start + window_end,
    )


def _matches_for_topic(
    topic: str,
    sections: list[dict[str, Any]],
    document_id: str,
    settings: Settings | None = None,
) -> TopicMatchResult:
    min_score = settings.min_match_score if settings is not None else MIN_MATCH_SCORE
    match_cap = max(settings.max_matches_per_topic, 1) if settings is not None else MAX_MATCHES_PER_TOPIC
    topic_text = topic.strip()
    normalized_topic = normalize_for_match(topic_text)
    terms = _content_terms(normalized_topic)
    phrase = " ".join(_stem(term) for term in normalized_topic.split())

    if not topic_text or not terms or not phrase:
        return TopicMatchResult(
            text=topic_text,
            status="unmatched",
            confidence=0.0,
            warnings=[
                _warning(
                    "topic_has_no_searchable_terms",
                    "This topic has no letters or numbers that can be matched "
                    "against source passages.",
                )
            ],
        )

    if not sections:
        return TopicMatchResult(
            text=topic_text,
            status="unmatched",
            confidence=0.0,
            warnings=[
                _warning(
                    "no_source_material",
                    "No extracted source passages are available for matching.",
                )
            ],
        )

    candidates: list[PassageMatch] = []
    for position, section in enumerate(sections):
        raw_text = str(section.get("raw_text") or "")
        if not raw_text.strip():
            continue
        normalized_section = normalize_for_match(raw_text)
        if not normalized_section:
            continue

        section_tokens = normalized_section.split()
        section_stems = [_stem(token) for token in section_tokens]
        section_terms = set(section_stems)
        matched_topic_terms = [term for term in terms if _variants(term) & section_terms]
        if not matched_topic_terms:
            continue

        coverage = len(matched_topic_terms) / len(terms)
        phrase_match = f" {phrase} " in f" {' '.join(section_stems)} "
        score = round(0.80 * coverage + 0.20 * (1.0 if phrase_match else 0.0), 6)
        if score < min_score:
            continue

        matched_terms: list[str] = []
        for term in matched_topic_terms:
            variants = _variants(term)
            source_term = next(
                (token for token, stem in zip(section_tokens, section_stems) if stem in variants),
                term,
            )
            if source_term not in matched_terms:
                matched_terms.append(source_term)

        index = _section_index(section, position)
        excerpt, excerpt_start, excerpt_end = _excerpt(raw_text, matched_terms)
        candidates.append(
            PassageMatch(
                passage_id=f"{document_id}:{index}" if document_id else f"section:{index}",
                section_index=index,
                anchor=dict(section.get("anchor") or {}),
                passage=raw_text,
                passage_start=0,
                passage_end=len(raw_text),
                score=score,
                coverage=round(coverage, 3),
                phrase_match=phrase_match,
                excerpt=excerpt,
                excerpt_start=excerpt_start,
                excerpt_end=excerpt_end,
                matched_terms=matched_terms,
                has_page_image=bool(section.get("has_page_image", False)),
            )
        )

    if not candidates:
        return TopicMatchResult(
            text=topic_text,
            status="unmatched",
            confidence=0.0,
            warnings=[
                _warning(
                    "no_strong_source_match",
                    "No source passage met the deterministic matching threshold; "
                    "no weak match was accepted.",
                )
            ],
        )

    ranked = sorted(candidates, key=lambda item: (-item.score, item.section_index))
    top = ranked[0]
    runner_up = ranked[1] if len(ranked) > 1 else None
    ambiguous = bool(
        runner_up is not None
        and top.score - runner_up.score < AMBIGUITY_GAP
    )

    warnings: list[dict[str, str]] = []
    matches = _ordered_matches(candidates) if _has_contiguous_run(candidates) else ranked
    matches = matches[:match_cap]
    if ambiguous:
        status = "ambiguous"
        warnings.append(
            _warning(
                "ambiguous_source_match",
                "Multiple source passages matched this topic with similar "
                "strength; choose the intended passage.",
            )
        )
    else:
        status = "matched"
        if len(candidates) > 1:
            warnings.append(
                _warning(
                    "multiple_source_matches",
                    "Multiple source passages matched; the highest-scoring "
                    "passage is listed first.",
                )
            )
    if len(candidates) > match_cap:
        warnings.append(
            _warning(
                "matches_truncated",
                f"Only {match_cap} source passages are shown; the match cap was reached.",
            )
        )

    return TopicMatchResult(
        text=topic_text,
        status=status,
        confidence=round(top.score, 3),
        matches=matches,
        warnings=warnings,
    )


def _table_row_numbers(table: dict[str, Any]) -> list[int]:
    rows = table.get("rows") or []
    header_indices = set(table.get("header_row_indices") or [])
    numbers: list[int] = []
    for index, row in enumerate(rows):
        if index in header_indices or not isinstance(row, list) or not row:
            continue
        value = str(row[0] or "").strip()
        match = re.match(r"^(\d{1,4})(?:[.)]|\s|$)", value)
        if match:
            numbers.append(int(match.group(1)))
    return numbers


def _table_continuation_reason(
    previous: dict[str, Any], following: dict[str, Any]
) -> str | None:
    if previous.get("column_count") != following.get("column_count"):
        return None
    previous_rows = previous.get("rows") or []
    following_rows = following.get("rows") or []
    if not previous_rows or not following_rows:
        return None
    reasons: list[str] = []
    if (
        0 in (previous.get("header_row_indices") or [])
        and 0 in (following.get("header_row_indices") or [])
        and previous_rows[0] == following_rows[0]
    ):
        reasons.append("repeated_header")
    previous_numbers = _table_row_numbers(previous)
    following_numbers = _table_row_numbers(following)
    if (
        previous_numbers
        and following_numbers
        and following_numbers[0] == previous_numbers[-1] + 1
    ):
        reasons.append("row_number_sequence")
    if not reasons and float(following.get("top_ratio") or 1.0) <= 0.25:
        previous_bbox = previous.get("bbox") or []
        following_bbox = following.get("bbox") or []
        if (
            len(previous_bbox) == 4
            and len(following_bbox) == 4
            and abs(float(previous_bbox[0]) - float(following_bbox[0])) <= 4
            and abs(float(previous_bbox[2]) - float(following_bbox[2])) <= 4
        ):
            reasons.append("same_table_geometry")
    return "+".join(reasons) if reasons else None


def _new_heading_precedes_table(page: dict[str, Any], table: dict[str, Any]) -> bool:
    table_start = table.get("source_start")
    for block in page.get("structure") or []:
        if not isinstance(block, dict) or block.get("role") not in {"title", "heading"}:
            continue
        start = block.get("start")
        if not isinstance(start, int) or not isinstance(table_start, int):
            return True
        if start < table_start:
            return True
    return False


def _extend_table_continuations(
    results: list[TopicMatchResult],
    sections: list[dict[str, Any]],
    page_records: list[dict[str, Any]],
    document_id: str,
) -> list[TopicMatchResult]:
    pages: dict[int, dict[str, Any]] = {}
    for page in page_records:
        number = page.get("page_number")
        if not isinstance(number, int):
            index = page.get("index")
            number = int(index) + 1 if isinstance(index, int) else None
        if isinstance(number, int):
            pages[number] = page
    sections_by_page: dict[int, dict[str, Any]] = {}
    for section in sections:
        anchor = section.get("anchor") or {}
        number = anchor.get("page")
        if anchor.get("kind") == "pdf_page" and isinstance(number, int):
            sections_by_page[number] = section

    extended_results: list[TopicMatchResult] = []
    for result in results:
        if result.status != "matched" or result.confidence < MIN_MATCH_SCORE:
            extended_results.append(result)
            continue
        matches = list(result.matches)
        known_indices = {match.section_index for match in matches}
        for anchor_match in list(matches):
            anchor = anchor_match.anchor
            if anchor.get("kind") != "pdf_page" or not isinstance(anchor.get("page"), int):
                continue
            start_page = current_page = int(anchor["page"])
            current_tables = (pages.get(current_page) or {}).get("tables") or []
            if not current_tables:
                continue
            continuation_reasons: list[str] = []
            while True:
                next_page = current_page + 1
                next_record = pages.get(next_page)
                next_section = sections_by_page.get(next_page)
                if next_record is None or next_section is None:
                    break
                next_tables = next_record.get("tables") or []
                continuation = None
                for previous_table in current_tables:
                    for next_table in next_tables:
                        if _new_heading_precedes_table(next_record, next_table):
                            continue
                        reason = _table_continuation_reason(previous_table, next_table)
                        if reason:
                            continuation = (next_table, reason)
                            break
                    if continuation:
                        break
                if continuation is None:
                    break
                next_table, reason = continuation
                section_index = _section_index(next_section, next_page - 1)
                current_page = next_page
                current_tables = [next_table]
                continuation_reasons.append(reason)
                if section_index not in known_indices:
                    raw_text = str(next_section.get("raw_text") or "")
                    anchor = dict(next_section.get("anchor") or {})
                    matches.append(
                        PassageMatch(
                            passage_id=(f"{document_id}:{section_index}" if document_id else f"section:{section_index}"),
                            section_index=section_index,
                            anchor=anchor,
                            passage=raw_text,
                            passage_start=0,
                            passage_end=len(raw_text),
                            score=0.0,
                            coverage=0.0,
                            phrase_match=False,
                            excerpt=raw_text[:600],
                            excerpt_start=0,
                            excerpt_end=min(len(raw_text), 600),
                            has_page_image=bool(next_section.get("has_page_image", False)),
                        )
                    )
                    known_indices.add(section_index)
            if current_page > start_page:
                reason = "+".join(sorted(set(continuation_reasons)))
                logger.info(
                    "topic continuation pages=%d-%d reason=%s",
                    start_page,
                    current_page,
                    reason,
                )
        extended_results.append(
            TopicMatchResult(
                text=result.text,
                status=result.status,
                confidence=result.confidence,
                matches=matches,
                warnings=result.warnings,
            )
        )
    return extended_results


def match_topics(
    topics: list[str],
    sections: list[dict[str, Any]],
    *,
    document_id: str,
    settings: Settings | None = None,
    page_records: list[dict[str, Any]] | None = None,
) -> list[TopicMatchResult]:
    """Match topics to exact extracted sections using deterministic term scoring."""
    results = [
        _matches_for_topic(topic, sections, document_id, settings) for topic in topics
    ]
    if page_records:
        return _extend_table_continuations(results, sections, page_records, document_id)
    return results
