from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.errors import ApiError

_LIST_PREFIX = re.compile(
    r"^\s*(?:"
    r"\(?\d{1,3}[\).:\-]\s*"
    r"|\b(?:q|question|topic|chapter|unit|lecture)\s*\d+\s*[\).:\-]?\s*"
    r")",
    re.IGNORECASE,
)
_TOPIC_LABEL = re.compile(
    r"^\s*(?:study\s+)?topics?\s*[:\-\u2013\u2014]\s*", re.IGNORECASE
)
_NUMBERED_BREAK = re.compile(
    r"\s+(?=\(?\d{1,3}[\).:\-]\s+[A-Za-z0-9])"
)
_TOPIC_SEPARATOR = re.compile(
    r"(?:[;|\u00b7\u2022\u2023\u2043\u2219\u25a0\u25aa\u25cf]+"
    r"|\s+[\u2013\u2014]\s+)"
)
_PAGE_LABEL = re.compile(
    r"^(?:(?:page|pg\.?)\s*\d+(?:\s*(?:of|/)\s*\d+)?|\d+\s*/\s*\d+)$",
    re.IGNORECASE,
)
_PURE_NUMBER = re.compile(r"^\d{1,4}$")
_EDGE_NOISE = (
    " \t-"
    "\u2010\u2011\u2012\u2013\u2014\u2015"
    "\u00b7\u2022*_=~`|\\\"'"
    "\u2018\u2019\u201c\u201d"
)
_QUOTES = "\"'\u2018\u2019\u201c\u201d"
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u00ad\u200b\u200c\u200d\ufeff\ufffd"))


@dataclass(frozen=True)
class SegmentationResult:
    topics: list[str]
    warnings: list[dict[str, str]] = field(default_factory=list)


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _topic_key(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).casefold()
    ascii_text = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.sub(r"[^0-9a-z]+", "", ascii_text)


def _clean_edge(text: str) -> str:
    text = text.translate(_ZERO_WIDTH)
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(_EDGE_NOISE)
    text = text.strip(_QUOTES).strip()
    return re.sub(r"\s+", " ", text).strip()


def _clean_topic(text: str) -> str:
    text = _clean_edge(text)
    return text.strip(" .,;:")


def _truncate_topic(text: str, max_chars: int) -> str:
    limit = max(int(max_chars), 1)
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" .,;:") or text[:limit]


def segment_transcript(
    text: str,
    *,
    max_topics: int,
    max_chars: int,
) -> SegmentationResult:
    """Split corrected note text into ordered, de-duplicated study topics.

    The splitter is deliberately deterministic. It handles numbered lists,
    bullet/pipe/semicolon lists, OCR edge noise, page labels, and hyphenated
    line wraps. It only de-duplicates topics whose normalized text is identical;
    related but distinct topics are kept separate.
    """
    if max_topics <= 0:
        return SegmentationResult(
            topics=[],
            warnings=[
                _warning(
                    "topic_limit_reached",
                    "No topics were returned because the topic limit is zero.",
                )
            ],
        )

    prepared = (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\f", "\n")
        .replace("\v", "\n")
    )
    prepared = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", prepared)

    topics: list[str] = []
    seen: set[str] = set()
    limit_reached = False
    truncated = False

    for raw_line in prepared.splitlines():
        line = _clean_edge(raw_line)
        if not line:
            continue

        label = _TOPIC_LABEL.match(line)
        if label:
            line = _clean_edge(line[label.end() :])

        line = _LIST_PREFIX.sub("", line).strip()
        if not line:
            continue

        pieces: list[str] = []
        for separated in _TOPIC_SEPARATOR.split(line):
            for numbered in _NUMBERED_BREAK.split(separated):
                numbered = _LIST_PREFIX.sub("", numbered).strip()
                numbered = _clean_topic(numbered)
                if numbered:
                    pieces.append(numbered)

        for piece in pieces:
            if _PAGE_LABEL.fullmatch(piece) or _PURE_NUMBER.fullmatch(piece):
                continue
            if len(piece) > max_chars:
                piece = _truncate_topic(piece, max_chars)
                truncated = True
            key = _topic_key(piece)
            if not key or key in seen:
                continue
            if len(topics) >= max_topics:
                limit_reached = True
                break
            seen.add(key)
            topics.append(piece)

        if limit_reached:
            break

    warnings: list[dict[str, str]] = []
    if limit_reached:
        warnings.append(
            _warning(
                "topic_limit_reached",
                f"Only the first {max_topics} topics were returned.",
            )
        )
    if truncated:
        warnings.append(
            _warning(
                "topic_truncated",
                f"One or more topics were shortened to {max_chars} characters.",
            )
        )

    return SegmentationResult(topics=topics, warnings=warnings)


def prepare_provided_topics(
    raw_topics: list[str],
    *,
    max_topics: int,
    max_chars: int,
) -> list[str]:
    """Validate a reviewed topic list and remove identical duplicates."""
    if len(raw_topics) > max_topics:
        raise ApiError(
            "too_many_topics",
            f"At most {max_topics} topics are allowed.",
            422,
            {"max_topics": max_topics},
        )

    cleaned: list[str] = []
    seen: set[str] = set()
    for raw_topic in raw_topics:
        topic = raw_topic.strip()
        if not topic:
            raise ApiError(
                "invalid_topic",
                "Topics cannot be empty.",
                422,
            )
        if len(topic) > max_chars:
            raise ApiError(
                "topic_too_long",
                f"Each topic must be at most {max_chars} characters.",
                422,
                {"max_chars": max_chars},
            )
        key = _topic_key(topic)
        if not key:
            raise ApiError(
                "invalid_topic",
                "Topics must contain letters or numbers.",
                422,
            )
        if key not in seen:
            seen.add(key)
            cleaned.append(topic)
    return cleaned
