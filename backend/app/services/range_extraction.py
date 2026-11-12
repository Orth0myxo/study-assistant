"""Deterministic range extraction over already-parsed document sections.

No model is involved anywhere in this module. Every returned passage text is
copied from the stored ``Section.raw_text`` values (numbered items are rebuilt
from consecutive lines of those sections), never generated.

Supported selectors
-------------------
``pages``       PDF page numbers/ranges: ``12``, ``12-15``, ``pages 3, 5 and 8 to 10``
``items``       numbered items such as MCQs: ``1-50``, ``questions 4 to 9``
``paragraphs``  1-based positions of non-empty blocks (mainly for DOCX)
``heading``     DOCX sections under a heading (matches heading text)
``chapter``     ``Chapter N`` (DOCX heading, or PDF page run detected from headings)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from app.errors import ApiError

MAX_RANGES = 50
MAX_SPAN = 20000
_MAX_LISTED = 8


class RangeError(ValueError):
    """A request that cannot be satisfied. Maps to an HTTP 422 in the API."""

    def __init__(
        self, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass
class RangeSelection:
    passages: list[dict[str, Any]]
    warnings: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SummaryChunk:
    label: str
    text: str
    passage_ids: list[str]


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


# --------------------------------------------------------------------------
# Number / range parsing
# --------------------------------------------------------------------------

_LABEL_WORDS = re.compile(
    r"\b(?:pages?|pgs?|pp|p|questions?|qs?|q|items?|paragraphs?|"
    r"sections?|numbers?|nos?)\b\.?",
    re.IGNORECASE,
)
_TO_WORDS = re.compile(r"\s*\b(?:to|through|thru|until)\b\s*", re.IGNORECASE)
_AND_WORDS = re.compile(r"\s*(?:\band\b|&|;)\s*", re.IGNORECASE)
_TOKEN = re.compile(r"(\d+)\s*-\s*(\d+)|(\d+)")


def parse_number_ranges(spec: str) -> list[tuple[int, int]]:
    """Parse ``"3, 5 and 8-10"`` into merged, sorted inclusive ranges.

    Reversed ranges (``15-12``) are normalised. Raises ``RangeError`` for empty,
    zero, absurdly large, or unparseable input.
    """
    text = (spec or "").strip().lower()
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    text = _LABEL_WORDS.sub(" ", text)
    text = _TO_WORDS.sub("-", text)
    text = _AND_WORDS.sub(",", text)

    ranges: list[tuple[int, int]] = []
    leftover = _TOKEN.sub(" ", text)
    if re.sub(r"[\s,]", "", leftover):
        raise RangeError(
            "invalid_range",
            "Could not understand that range. Use numbers such as 12-15 or 3, 5, 8-10.",
        )
    for match in _TOKEN.finditer(text):
        if match.group(3) is not None:
            start = end = int(match.group(3))
        else:
            start, end = int(match.group(1)), int(match.group(2))
        if start > end:
            start, end = end, start
        if start < 1:
            raise RangeError("invalid_range", "Numbers in a range must start at 1.")
        if end - start + 1 > MAX_SPAN:
            raise RangeError("invalid_range", "That range is too large.")
        ranges.append((start, end))
    if not ranges:
        raise RangeError(
            "invalid_range",
            "No numbers were found. Use a range such as 12-15.",
        )
    if len(ranges) > MAX_RANGES:
        raise RangeError(
            "invalid_range", f"At most {MAX_RANGES} ranges are allowed per request."
        )
    return _merge(ranges)


def _merge(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(a, b) for a, b in merged]


def _numbers(ranges: list[tuple[int, int]]) -> list[int]:
    return [n for start, end in ranges for n in range(start, end + 1)]


def _format_numbers(values: list[int], limit: int = _MAX_LISTED) -> str:
    shown = ", ".join(str(v) for v in values[:limit])
    if len(values) > limit:
        shown += f", +{len(values) - limit} more"
    return shown


# --------------------------------------------------------------------------
# Passages
# --------------------------------------------------------------------------


def ref_label(anchor: dict[str, Any]) -> str:
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


def _passage(
    document_id: str,
    section: dict[str, Any],
    *,
    text: str | None = None,
    passage_id: str | None = None,
    item_number: int | None = None,
) -> dict[str, Any]:
    index = int(section.get("index", 0))
    anchor = dict(section.get("anchor") or {})
    return {
        "passage_id": passage_id or f"{document_id}:{index}",
        "section_index": index,
        "anchor": anchor,
        "label": ref_label(anchor),
        "text": str(section.get("raw_text") or "") if text is None else text,
        "item_number": item_number,
    }


def _is_pdf_section(section: dict[str, Any]) -> bool:
    return (section.get("anchor") or {}).get("kind") == "pdf_page"


# --------------------------------------------------------------------------
# Selectors
# --------------------------------------------------------------------------


def select_pdf_pages(
    document_id: str,
    sections: list[dict[str, Any]],
    page_count: int | None,
    ranges: list[tuple[int, int]],
) -> RangeSelection:
    if page_count is None:
        raise RangeError(
            "pages_not_supported",
            "This document has no page numbers. Use a heading, chapter or "
            "paragraph range instead.",
        )
    if ranges[0][0] > page_count:
        raise RangeError(
            "page_out_of_range",
            f"The document has {page_count} page(s); page {ranges[0][0]} is out of range.",
            {"page_count": page_count},
        )

    warnings: list[dict[str, str]] = []
    clipped = [(s, min(e, page_count)) for s, e in ranges if s <= page_count]
    if any(e > page_count for _, e in ranges):
        warnings.append(
            _warning(
                "range_clipped",
                f"Pages beyond {page_count} were ignored because the document has "
                f"{page_count} page(s).",
            )
        )

    by_page = {
        int(s["anchor"]["page"]): s
        for s in sections
        if _is_pdf_section(s) and isinstance((s.get("anchor") or {}).get("page"), int)
    }
    passages: list[dict[str, Any]] = []
    missing: list[int] = []
    for page in _numbers(clipped):
        section = by_page.get(page)
        if section is None:
            missing.append(page)
            continue
        passages.append(_passage(document_id, section))

    if missing:
        warnings.append(
            _warning(
                "pages_without_text",
                f"No readable text was found on page(s) {_format_numbers(missing)} "
                "(blank, image-only or unreadable).",
            )
        )
    if not passages:
        raise RangeError(
            "range_has_no_text",
            "None of the requested pages contain readable text.",
            {"pages": missing[:50]},
        )
    return RangeSelection(passages, warnings)


def select_paragraphs(
    document_id: str,
    sections: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
) -> RangeSelection:
    total = len(sections)
    if ranges[0][0] > total:
        raise RangeError(
            "range_out_of_bounds",
            f"The document has {total} text block(s); {ranges[0][0]} is out of range.",
            {"block_count": total},
        )
    warnings: list[dict[str, str]] = []
    if any(e > total for _, e in ranges):
        warnings.append(
            _warning(
                "range_clipped",
                f"Blocks beyond {total} were ignored because the document has "
                f"{total} text block(s).",
            )
        )
    wanted = {n for n in _numbers([(s, min(e, total)) for s, e in ranges if s <= total])}
    passages = [
        _passage(document_id, section)
        for position, section in enumerate(sections, start=1)
        if position in wanted
    ]
    if not passages:
        raise RangeError("range_has_no_text", "No text blocks matched that range.")
    warnings.append(
        _warning(
            "block_numbering",
            "Blocks are numbered by position among non-empty paragraphs and tables.",
        )
    )
    return RangeSelection(passages, warnings)


def _norm(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text).casefold()
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def select_by_heading(
    document_id: str,
    sections: list[dict[str, Any]],
    query: str,
) -> RangeSelection:
    if any(_is_pdf_section(s) for s in sections):
        raise RangeError(
            "headings_not_supported",
            "Headings are only detected in DOCX files. For a PDF use a page range, "
            "chapter number or topic.",
        )
    query_terms = _norm(query).split()
    if not query_terms:
        raise RangeError("invalid_range", "Give the heading text to look for.")

    labels: list[str] = []
    for section in sections:
        label = (section.get("anchor") or {}).get("section_label")
        if label and label not in labels:
            labels.append(label)
    matching = [
        label
        for label in labels
        if all(term in _norm(label).split() for term in query_terms)
    ]
    if not matching:
        raise RangeError(
            "heading_not_found",
            "No heading matched that text.",
            {"available_headings": labels[:_MAX_LISTED]},
        )
    chosen = set(matching)
    passages = [
        _passage(document_id, s)
        for s in sections
        if (s.get("anchor") or {}).get("section_label") in chosen
    ]
    warnings: list[dict[str, str]] = []
    if len(matching) > 1:
        warnings.append(
            _warning(
                "multiple_headings_matched",
                "More than one heading matched: " + "; ".join(matching[:_MAX_LISTED]),
            )
        )
    return RangeSelection(passages, warnings)


_ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
_WORD_NUMBERS = {
    w: i
    for i, w in enumerate(
        "one two three four five six seven eight nine ten eleven twelve thirteen "
        "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split(),
        start=1,
    )
}


def _roman_to_int(text: str) -> int | None:
    text = text.lower()
    if not text or any(c not in _ROMAN for c in text):
        return None
    total = 0
    for i, char in enumerate(text):
        value = _ROMAN[char]
        if i + 1 < len(text) and _ROMAN[text[i + 1]] > value:
            total -= value
        else:
            total += value
    return total


def parse_chapter_number(spec: str) -> int:
    text = (spec or "").strip().lower()
    text = re.sub(r"^(?:chapter|ch\.?|unit)\s*", "", text).strip()
    if text.isdigit() and int(text) >= 1:
        return int(text)
    if text in _WORD_NUMBERS:
        return _WORD_NUMBERS[text]
    roman = _roman_to_int(text)
    if roman:
        return roman
    raise RangeError(
        "invalid_range", "Give a chapter number such as 3, III or three."
    )


_CHAPTER_LINE = re.compile(r"^\s*chapter\s+([0-9]+|[ivxlc]+|[a-z]+)\b(.*)$", re.IGNORECASE)
_TOC_TAIL = re.compile(r"(?:\.{3,}|…|\s\d{1,4}\s*$)")


def _chapter_lines(raw_text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for line in raw_text.split("\n"):
        match = _CHAPTER_LINE.match(line)
        if not match:
            continue
        token = match.group(1)
        number: int | None
        if token.isdigit():
            number = int(token)
        elif token.lower() in _WORD_NUMBERS:
            number = _WORD_NUMBERS[token.lower()]
        else:
            number = _roman_to_int(token)
        if number is None:
            continue
        found.append((number, match.group(2)))
    return found


def select_chapter(
    document_id: str,
    sections: list[dict[str, Any]],
    page_count: int | None,
    chapter_spec: str,
) -> RangeSelection:
    number = parse_chapter_number(chapter_spec)

    if sections and not _is_pdf_section(sections[0]):
        # DOCX: a heading such as "Chapter 3: ..." owns the sections under it.
        labels: list[str] = []
        for section in sections:
            label = (section.get("anchor") or {}).get("section_label")
            if label and label not in labels:
                labels.append(label)
        matching = [
            label
            for label in labels
            if any(n == number for n, _ in _chapter_lines(label))
        ]
        if not matching:
            raise RangeError(
                "chapter_not_found",
                f"No heading for chapter {number} was found.",
                {"available_headings": labels[:_MAX_LISTED]},
            )
        chosen = set(matching)
        passages = [
            _passage(document_id, s)
            for s in sections
            if (s.get("anchor") or {}).get("section_label") in chosen
        ]
        return RangeSelection(passages, [])

    # PDF: detect pages whose text has a "Chapter N" heading line.
    start_page: int | None = None
    next_page: int | None = None
    pdf_sections = sorted(
        (s for s in sections if _is_pdf_section(s)),
        key=lambda s: int(s["anchor"]["page"]),
    )
    for section in pdf_sections:
        page = int(section["anchor"]["page"])
        lines = _chapter_lines(str(section.get("raw_text") or ""))
        if len(lines) >= 3:
            continue  # looks like a table of contents
        real = [(n, tail) for n, tail in lines if not _TOC_TAIL.search(tail)]
        numbers_here = {n for n, _ in real}
        if start_page is None and number in numbers_here:
            start_page = page
        elif start_page is not None and page > start_page and (number + 1) in numbers_here:
            next_page = page
            break
    if start_page is None:
        raise RangeError(
            "chapter_not_found",
            f"No 'Chapter {number}' heading was found. Try a page range instead.",
        )
    last = (next_page - 1) if next_page else (page_count or start_page)
    selection = select_pdf_pages(
        document_id, sections, page_count, [(start_page, max(last, start_page))]
    )
    selection.warnings.append(
        _warning(
            "chapter_bounds_heuristic",
            f"Chapter {number} was detected from page headings and covers whole pages "
            f"{start_page}-{max(last, start_page)}. Check the first and last page.",
        )
    )
    return selection


_ITEM_START = re.compile(r"^\s*(?:q(?:uestion)?\s*)?(\d{1,4})\s*[.)]\s+\S", re.IGNORECASE)
_ITEM_MAX_JUMP = 25
_ITEM_FIRST_MAX = 200


def select_numbered_items(
    document_id: str,
    sections: list[dict[str, Any]],
    ranges: list[tuple[int, int]],
) -> RangeSelection:
    """Rebuild numbered items (``1.`` / ``Q2)``) from consecutive source lines.

    A line starts a new item only if its number is greater than the previous item
    and at most 25 higher, which ignores stray numbers and nested list restarts.
    Item text runs from its numbered line up to (not including) the next item
    line, and may continue across page boundaries.
    """
    items: dict[int, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    last_number = 0

    for section in sections:
        anchor = dict(section.get("anchor") or {})
        for line in str(section.get("raw_text") or "").split("\n"):
            match = _ITEM_START.match(line)
            if match:
                number = int(match.group(1))
                accepted = (
                    1 <= number <= _ITEM_FIRST_MAX
                    if last_number == 0
                    else last_number < number <= last_number + _ITEM_MAX_JUMP
                )
                if accepted:
                    last_number = number
                    current = {
                        "number": number,
                        "lines": [line],
                        "section": section,
                        "anchor": anchor,
                        "end_anchor": anchor,
                    }
                    items[number] = current
                    continue
            if current is not None:
                current["lines"].append(line)
                current["end_anchor"] = anchor

    wanted = _numbers(ranges)
    passages: list[dict[str, Any]] = []
    missing: list[int] = []
    for number in wanted:
        item = items.get(number)
        if item is None:
            missing.append(number)
            continue
        lines = list(item["lines"])
        while lines and not lines[-1].strip():
            lines.pop()
        passage = _passage(
            document_id,
            item["section"],
            text="\n".join(lines),
            passage_id=f"{document_id}:item:{number}",
            item_number=number,
        )
        if item["end_anchor"] != item["anchor"]:
            passage["label"] += f" to {ref_label(item['end_anchor'])}"
        passages.append(passage)

    if not passages:
        raise RangeError(
            "items_not_found",
            "No numbered items were found in that range.",
            {"found_range": [min(items), max(items)] if items else None},
        )
    warnings: list[dict[str, str]] = []
    if missing:
        warnings.append(
            _warning(
                "items_not_found",
                f"Item(s) {_format_numbers(missing)} were not found in the document.",
            )
        )
    return RangeSelection(passages, warnings)


def _extract_range_v2(
    *,
    document_id: str,
    sections: list[dict[str, Any]],
    page_count: int | None,
    selector_type: str,
    spec: str | None = None,
    query: str | None = None,
) -> RangeSelection:
    """Dispatch a selector to the right deterministic extractor."""
    if selector_type == "pages":
        return select_pdf_pages(
            document_id, sections, page_count, parse_number_ranges(spec or "")
        )
    if selector_type == "items":
        return select_numbered_items(
            document_id, sections, parse_number_ranges(spec or "")
        )
    if selector_type == "paragraphs":
        return select_paragraphs(
            document_id, sections, parse_number_ranges(spec or "")
        )
    if selector_type == "heading":
        return select_by_heading(document_id, sections, query or "")
    if selector_type == "chapter":
        return select_chapter(document_id, sections, page_count, spec or query or "")
    raise RangeError("invalid_selector", f"Unknown selector type: {selector_type}")


# --------------------------------------------------------------------------
# Chunking for summaries
# --------------------------------------------------------------------------


def _split_text(text: str, limit: int) -> list[str]:
    """Split on blank lines, then lines, then whitespace; never mid-word."""
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    buffer = ""

    def push_long(token: str) -> None:
        nonlocal buffer
        for word in re.split(r"(\s+)", token):
            while len(word) > limit:  # a single enormous token
                if buffer:
                    pieces.append(buffer)
                    buffer = ""
                pieces.append(word[:limit])
                word = word[limit:]
            if buffer and len(buffer) + len(word) > limit:
                pieces.append(buffer)
                buffer = ""
            buffer += word

    for token in re.split(r"(\n\s*\n)", text):
        if len(buffer) + len(token) <= limit:
            buffer += token
        elif len(token) <= limit:
            pieces.append(buffer)
            buffer = token
        else:
            if buffer:
                pieces.append(buffer)
                buffer = ""
            push_long(token)
    if buffer:
        pieces.append(buffer)
    return [p.strip() for p in pieces if p.strip()]


def _group_label(labels: list[str]) -> str:
    pages = []
    for label in labels:
        match = re.fullmatch(r"Page (\d+)", label)
        if match:
            pages.append(int(match.group(1)))
    if pages and len(pages) == len(labels):
        runs: list[list[int]] = []
        for page in sorted(set(pages)):
            if runs and page == runs[-1][-1] + 1:
                runs[-1].append(page)
            else:
                runs.append([page])
        text = ", ".join(
            f"{run[0]}" if len(run) == 1 else f"{run[0]}-{run[-1]}" for run in runs
        )
        return f"Page {text}" if len(pages) == 1 else f"Pages {text}"
    unique: list[str] = []
    for label in labels:
        if label not in unique:
            unique.append(label)
    shown = "; ".join(unique[:3])
    return shown + (f"; +{len(unique) - 3} more" if len(unique) > 3 else "")


def chunk_passages(
    passages: list[dict[str, Any]], max_chars: int
) -> list[SummaryChunk]:
    """Group passages into prompt-sized chunks for summarising.

    Only these chunk copies are ever shortened; the passages returned to the
    caller stay complete.
    """
    limit = max(int(max_chars), 200)
    chunks: list[SummaryChunk] = []
    labels: list[str] = []
    ids: list[str] = []
    buffer = ""

    def flush() -> None:
        nonlocal labels, ids, buffer
        if buffer.strip():
            chunks.append(SummaryChunk(_group_label(labels), buffer.strip(), ids))
        labels, ids, buffer = [], [], ""

    for passage in passages:
        header = f"[{passage['label']}]\n"
        for part in _split_text(str(passage["text"]), max(limit - len(header), 100)):
            block = header + part
            if buffer and len(buffer) + 2 + len(block) > limit:
                flush()
            buffer = f"{buffer}\n\n{block}" if buffer else block
            labels.append(passage["label"])
            if passage["passage_id"] not in ids:
                ids.append(passage["passage_id"])
    flush()
    return chunks


# --------------------------------------------------------------------------
# Legacy selector interface used by /documents/{id}/ranges and range-study
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RangeExtraction:
    selector: str
    sections: list[dict[str, Any]]
    warnings: list[dict[str, str]]


_LEGACY_PAGE_WORDS = re.compile(r"\bpages?\b", re.IGNORECASE)
_LEGACY_NUMBER_RANGE = re.compile(
    r"(?<!\d)(\d+)\s*(?:-|\bto\b)\s*(\d+)(?!\d)", re.I
)


def _legacy_ranges(selector: str) -> list[tuple[int, int]]:
    without_prefix = _LEGACY_PAGE_WORDS.sub("", selector).strip()
    if not re.fullmatch(r"[\d\s,;&andto-]+", without_prefix, re.I):
        return []
    found: list[tuple[int, int]] = []
    occupied: list[tuple[int, int]] = []
    for match in _LEGACY_NUMBER_RANGE.finditer(without_prefix):
        found.append((int(match.group(1)), int(match.group(2))))
        occupied.append(match.span())
    residue = list(without_prefix)
    for start, end in occupied:
        residue[start:end] = " " * (end - start)
    singles = [int(value) for value in re.findall(r"\d+", "".join(residue))]
    found.extend((value, value) for value in singles)
    return found


def _legacy_in_lines(text: str, kind: str, low: int, high: int) -> bool:
    label = "question|q" if kind == "question" else "item|question|q"
    return any(
        low <= int(match.group(1)) <= high
        for line in text.splitlines()
        if (match := re.match(rf"\s*(?:{label}\s*)?\s*(\d+)[.)\]:-]", line, re.I))
    )


def _extract_range_legacy(
    *,
    document_id: str,
    format: str,
    page_count: int | None,
    sections: list[dict[str, Any]],
    selector: str,
) -> RangeExtraction:
    """Select stored sections without rewriting or synthesizing their text."""
    cleaned = selector.strip()
    warnings: list[dict[str, str]] = []
    selected: list[dict[str, Any]] = []

    chapter = re.search(r"\bchapter\s+([\w.-]+)", cleaned, re.I)
    question_range = re.search(
        r"\b(?:questions?|items?)\s+(\d+)\s*(?:-|\bto\b)\s*(\d+)", cleaned, re.I
    )
    if chapter:
        label = chapter.group(1).casefold()
        for section in sections:
            text = str(section.get("raw_text") or "")
            anchor = section.get("anchor") or {}
            heading = str(anchor.get("section_label") or "")
            if re.search(
                rf"\bchapter\s+{re.escape(label)}\b", heading, re.I
            ) or any(
                re.search(
                    rf"^\s*chapter\s+{re.escape(label)}\b", line, re.I
                )
                for line in text.splitlines()
            ):
                selected.append(section)
        if not selected:
            raise ApiError("range_not_found", f"No section matched {cleaned!r}.", 422)
        if format == "docx":
            warnings.append(
                {
                    "code": "docx_no_pages",
                    "message": (
                        "DOCX has no page anchors; chapter selectors use stored "
                        "heading sections."
                    ),
                }
            )
    elif question_range:
        start, end = sorted(
            (int(question_range.group(1)), int(question_range.group(2)))
        )
        for section in sections:
            if _legacy_in_lines(
                str(section.get("raw_text") or ""), "question", start, end
            ):
                selected.append(section)
        if format == "docx":
            warnings.append(
                {
                    "code": "docx_no_pages",
                    "message": (
                        "DOCX has no page anchors; numbered selectors use "
                        "paragraph sections."
                    ),
                }
            )
    else:
        ranges = _legacy_ranges(cleaned)
        if not ranges:
            raise ApiError(
                "invalid_selector",
                "Use page numbers/ranges, question or item ranges, or a chapter heading.",
                422,
            )
        if format == "pdf":
            if page_count is None:
                raise ApiError(
                    "page_count_unavailable",
                    "The PDF page count is unavailable.",
                    422,
                )
            requested = set()
            for first, last in ranges:
                low, high = sorted((first, last))
                if low < 1 or high > page_count:
                    raise ApiError(
                        "page_out_of_range",
                        f"Page selectors must be within 1-{page_count} for this document.",
                        422,
                        {"page_count": page_count, "requested": [first, last]},
                    )
                requested.update(range(low, high + 1))
            by_page = {
                int((item.get("anchor") or {}).get("page", -1)): item
                for item in sections
            }
            selected = [
                by_page[page] for page in sorted(requested) if page in by_page
            ]
            dropped = sorted(requested - by_page.keys())
            if dropped:
                warnings.append(
                    {
                        "code": "unreadable_pages_dropped",
                        "message": (
                            "No stored text was available for page(s): "
                            + ", ".join(map(str, dropped))
                            + "."
                        ),
                    }
                )
        else:
            warnings.append(
                {
                    "code": "docx_no_pages",
                    "message": (
                        "DOCX has no page anchors; numeric selectors map to "
                        "paragraph indices."
                    ),
                }
            )
            requested = set()
            for first, last in ranges:
                low, high = sorted((first, last))
                requested.update(range(low, high + 1))
            selected = [
                section
                for section in sections
                if int((section.get("anchor") or {}).get("paragraph_index", -1))
                in requested
            ]
    if not selected:
        raise ApiError(
            "range_not_found", f"No stored sections matched {cleaned!r}.", 422
        )
    # Copy references only; raw_text remains byte-for-byte the persisted value.
    return RangeExtraction(
        selector=cleaned,
        sections=[dict(section) for section in selected],
        warnings=warnings,
    )


def extract_range(
    *,
    document_id: str,
    sections: list[dict[str, Any]],
    page_count: int | None,
    selector_type: str | None = None,
    spec: str | None = None,
    query: str | None = None,
    format: str | None = None,
    selector: str | None = None,
) -> RangeSelection | RangeExtraction:
    """Dispatch branch selector requests and the legacy selector string API."""
    if selector is not None or selector_type is None:
        if format is None or selector is None:
            raise RangeError(
                "invalid_selector", "A document format and selector are required."
            )
        return _extract_range_legacy(
            document_id=document_id,
            format=format,
            page_count=page_count,
            sections=sections,
            selector=selector,
        )
    return _extract_range_v2(
        document_id=document_id,
        sections=sections,
        page_count=page_count,
        selector_type=selector_type,
        spec=spec,
        query=query,
    )
