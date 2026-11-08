from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

MIN_MATCH_SCORE = 0.70
AMBIGUITY_GAP = 0.08
MAX_MATCHES_PER_TOPIC = 3

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
) -> TopicMatchResult:
    topic_text = topic.strip()
    normalized_topic = normalize_for_match(topic_text)
    terms = _content_terms(normalized_topic)
    phrase = normalized_topic

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

        section_terms = set(normalized_section.split())
        matched_terms = [term for term in terms if term in section_terms]
        if not matched_terms:
            continue

        coverage = len(matched_terms) / len(terms)
        phrase_match = f" {phrase} " in f" {normalized_section} "
        score = round(0.80 * coverage + 0.20 * (1.0 if phrase_match else 0.0), 6)
        if score < MIN_MATCH_SCORE:
            continue

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

    candidates.sort(key=lambda item: (-item.score, item.section_index))
    top = candidates[0]
    runner_up = candidates[1] if len(candidates) > 1 else None
    ambiguous = bool(
        runner_up is not None
        and top.score - runner_up.score < AMBIGUITY_GAP
    )

    warnings: list[dict[str, str]] = []
    matches = candidates[:MAX_MATCHES_PER_TOPIC]
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
    if len(candidates) > MAX_MATCHES_PER_TOPIC:
        warnings.append(
            _warning(
                "matches_truncated",
                f"Only the best {MAX_MATCHES_PER_TOPIC} source passages are shown.",
            )
        )

    return TopicMatchResult(
        text=topic_text,
        status=status,
        confidence=round(top.score, 3),
        matches=matches,
        warnings=warnings,
    )


def match_topics(
    topics: list[str],
    sections: list[dict[str, Any]],
    *,
    document_id: str,
) -> list[TopicMatchResult]:
    """Match topics to exact extracted sections using deterministic term scoring."""
    return [
        _matches_for_topic(topic, sections, document_id) for topic in topics
    ]
