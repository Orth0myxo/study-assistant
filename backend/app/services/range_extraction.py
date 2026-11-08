from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.errors import ApiError


@dataclass(frozen=True)
class RangeExtraction:
    selector: str
    sections: list[dict[str, Any]]
    warnings: list[dict[str, str]]


_PAGE_WORDS = re.compile(r"\bpages?\b", re.IGNORECASE)
_NUMBER_RANGE = re.compile(r"(?<!\d)(\d+)\s*(?:-|\bto\b)\s*(\d+)(?!\d)", re.I)


def _ranges(selector: str) -> list[tuple[int, int]]:
    without_prefix = _PAGE_WORDS.sub("", selector).strip()
    if not re.fullmatch(r"[\d\s,;&andto-]+", without_prefix, re.I):
        return []
    found: list[tuple[int, int]] = []
    occupied: list[tuple[int, int]] = []
    for match in _NUMBER_RANGE.finditer(without_prefix):
        found.append((int(match.group(1)), int(match.group(2))))
        occupied.append(match.span())
    residue = list(without_prefix)
    for start, end in occupied:
        residue[start:end] = " " * (end - start)
    singles = [int(value) for value in re.findall(r"\d+", "".join(residue))]
    found.extend((value, value) for value in singles)
    return found


def _in_lines(text: str, kind: str, low: int, high: int) -> bool:
    label = "question|q" if kind == "question" else "item|question|q"
    return any(
        low <= int(match.group(1)) <= high
        for line in text.splitlines()
        if (match := re.match(rf"\s*(?:{label}\s*)?\s*(\d+)[.)\]:-]", line, re.I))
    )


def extract_range(
    *, document_id: str, format: str, page_count: int | None,
    sections: list[dict[str, Any]], selector: str,
) -> RangeExtraction:
    """Select stored sections without rewriting or synthesizing their text."""
    cleaned = selector.strip()
    lowered = cleaned.casefold()
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
            if re.search(rf"\bchapter\s+{re.escape(label)}\b", heading, re.I) or any(
                re.search(rf"^\s*chapter\s+{re.escape(label)}\b", line, re.I)
                for line in text.splitlines()
            ):
                selected.append(section)
        if not selected:
            raise ApiError("range_not_found", f"No section matched {cleaned!r}.", 422)
        if format == "docx":
            warnings.append({"code": "docx_no_pages", "message": "DOCX has no page anchors; chapter selectors use stored heading sections."})
    elif question_range:
        start, end = sorted((int(question_range.group(1)), int(question_range.group(2))))
        for section in sections:
            if _in_lines(str(section.get("raw_text") or ""), "question", start, end):
                selected.append(section)
        if format == "docx":
            warnings.append({"code": "docx_no_pages", "message": "DOCX has no page anchors; numbered selectors use paragraph sections."})
    else:
        ranges = _ranges(cleaned)
        if not ranges:
            raise ApiError("invalid_selector", "Use page numbers/ranges, question or item ranges, or a chapter heading.", 422)
        if format == "pdf":
            if page_count is None:
                raise ApiError("page_count_unavailable", "The PDF page count is unavailable.", 422)
            requested = set()
            for first, last in ranges:
                low, high = sorted((first, last))
                if low < 1 or high > page_count:
                    raise ApiError("page_out_of_range", f"Page selectors must be within 1-{page_count} for this document.", 422, {"page_count": page_count, "requested": [first, last]})
                requested.update(range(low, high + 1))
            by_page = {int((item.get("anchor") or {}).get("page", -1)): item for item in sections}
            selected = [by_page[page] for page in sorted(requested) if page in by_page]
            dropped = sorted(requested - by_page.keys())
            if dropped:
                warnings.append({"code": "unreadable_pages_dropped", "message": "No stored text was available for page(s): " + ", ".join(map(str, dropped)) + "."})
        else:
            warnings.append({"code": "docx_no_pages", "message": "DOCX has no page anchors; numeric selectors map to paragraph indices."})
            requested = set()
            for first, last in ranges:
                low, high = sorted((first, last))
                requested.update(range(low, high + 1))
            selected = [section for section in sections if int((section.get("anchor") or {}).get("paragraph_index", -1)) in requested]
    if not selected:
        raise ApiError("range_not_found", f"No stored sections matched {cleaned!r}.", 422)
    # Copy references only; raw_text remains byte-for-byte the persisted value.
    return RangeExtraction(selector=cleaned, sections=[dict(section) for section in selected], warnings=warnings)
