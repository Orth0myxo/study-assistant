from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pymupdf
from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.errors import ApiError
from app.services.page_images import render_export_page_image

PAGE_W = 595.0
PAGE_H = 842.0
MARGIN = 56.0
PDF_BODY_SIZE = 13.5
PDF_SUBHEAD_SIZE = PDF_BODY_SIZE
PDF_META_SIZE = 11.0
PDF_SOURCE_SIZE = 11.0
PDF_OCR_SIZE = 11.0
PDF_WARNING_SIZE = 11.0
PDF_TITLE_SIZE = 25.0
PDF_H2_SIZE = 17.0
PDF_H3_SIZE = 14.0
PDF_FRONT_SIZE = 12.0
DOCX_BODY_SIZE = 13.5
DOCX_SUBHEAD_SIZE = DOCX_BODY_SIZE
DOCX_META_SIZE = 11.0
DOCX_SOURCE_SIZE = 11.0
DOCX_OCR_SIZE = 11.0
DOCX_WARNING_SIZE = 11.0
DOCX_TITLE_SIZE = 24.0
DOCX_H2_SIZE = 17.0
DOCX_H3_SIZE = 14.0
DOCX_FRONT_SIZE = 12.0
BODY_FONT = "body"
BOLD_FONT = "bold"
_HEADING_COLOR = (0.12, 0.18, 0.25)
_MUTED_COLOR = (0.42, 0.45, 0.50)
_DOCX_HEADING_COLOR = RGBColor(0x1F, 0x3A, 0x5F)
_DOCX_MUTED_COLOR = RGBColor(0x62, 0x6B, 0x78)
_DOCX_BODY_FONT = "Calibri"
AI_SUMMARY_HEADING = "AI summary, not source text"
AI_SUMMARY_NOTE = "AI-written summary, not source text"
SUMMARY_HEADING = "Summary"
SOURCE_TEXT_HEADING = "Original source text"
OCR_TEXT_LABEL = "Text read by OCR; may contain errors"
MAX_EXPORT_IMAGES = 30
MAX_EXPORT_IMAGE_BYTES = 25 * 1024 * 1024
_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_PDF_FONT_FILES = {
    BODY_FONT: _FONT_DIR / "DejaVuSans.ttf",
    BOLD_FONT: _FONT_DIR / "DejaVuSans-Bold.ttf",
}
_PDF_FONT_ALIASES = {BODY_FONT: "F0", BOLD_FONT: "F1"}

_PDF_TEXT_ALIGN = {"center": 1, "right": 2, "justify": 3}
_DOCX_TEXT_ALIGN = {
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
    "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
}

# Block kinds understood by every renderer.
Block = tuple[str, str]

# Keep exported punctuation consistent and readable. The embedded Unicode font
# (rather than a Latin-1 built-in font) handles scientific and medical symbols.
_ASCII_TRANSLATION = str.maketrans(
    {
        "\u2022": "-",
        "\u2013": "-",
        "\u2014": "-",
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2026": "...",
        "\u2212": "-",
        "\u00b7": "-",
        "\u00a0": " ",
        "\u200b": "",
    }
)


def _sanitize(text: str) -> str:
    translated = text.translate(_ASCII_TRANSLATION)
    return "".join(ch for ch in translated if ch in "\n\t" or ord(ch) >= 32)


def _display_filename(value: object) -> str:
    """Readable file name for export lines: URL escapes decoded, no folders."""
    raw = unquote(str(value or "").strip())
    return Path(raw.replace("\\", "/")).name or raw


def _display_title(value: object) -> str:
    """Turn a stored (often URL-encoded) filename into a readable title."""
    raw = unquote(str(value or "").strip())
    name = Path(raw.replace("\\", "/")).name
    stem = Path(name).stem if name else ""
    stem = re.sub(r"(?i)[\s_-]+compressed$", "", stem)
    stem = re.sub(r"^\d+\s*[-._)]\s*", "", stem)
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._-")
    if not stem:
        return "Study results"

    return stem[:180].rstrip(" ._-") or "Study results"


_PAGE_MARKER_RE = re.compile(r"^page\s+\d+\s+of\s+\d+$", re.IGNORECASE)
_NUMBERED_RE = re.compile(r"^\d{1,3}[.)]\s+")
_BULLET_RE = re.compile(r"^(?P<indent>\s*)[-\u2013\u2014\u2022]\s+(?P<text>.+)$")
_MULTI_NUMBER_RE = re.compile(r"(?<!\S)(?=\d{1,3}[.)]\s+)")
_SUBHEADINGS = {
    "antigen presentation",
    "cell types",
    "consequences of impaired clonal selection",
    "consequences of impaired negative selection",
    "consequences of impaired positive selection",
    "dysregulation consequences",
    "importance",
    "key players",
    "location",
    "mechanism",
    "mechanisms",
    "molecular mechanisms",
    "purpose",
    "steps",
    "therapeutic strategies",
    "timeline",
    "types",
}


def _ends_sentence(text: str) -> bool:
    stripped = text.rstrip().rstrip("\"')]}")
    return bool(stripped) and stripped[-1] in ".?!:;"


def _join_text(text: str, continuation: str) -> str:
    if text.endswith("-"):
        return text + continuation
    return f"{text} {continuation}"


def _is_page_marker(text: str) -> bool:
    return bool(_PAGE_MARKER_RE.match(text.strip()))


def _is_list_start(text: str) -> bool:
    stripped = text.strip()
    return bool(_BULLET_RE.match(text) or _NUMBERED_RE.match(stripped))


def _is_front_line(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    return (
        bool(letters)
        and all(char.isupper() for char in letters)
        and not _is_list_start(text)
        and len(text) <= 110
    )


def _heading_kind(text: str, following: str | None) -> str | None:
    stripped = text.strip()
    if not stripped or _is_list_start(stripped) or _is_page_marker(stripped):
        return None
    if len(stripped) > 72 or stripped[-1] in ".,;":
        return None
    if stripped.endswith(":") and not stripped.startswith("Step "):
        return None
    if following:
        next_text = following.strip()
        if next_text and (next_text[0].islower() or next_text[0] in "(["):
            return None

    words = stripped.split()
    if not words:
        return None
    letters = [char for char in stripped if char.isalpha()]
    if not letters:
        return None
    upper_ratio = sum(char.isupper() for char in letters) / len(letters)
    title_starts = sum(
        1 for word in words if word[0].isupper() or word[0].isdigit()
    )
    title_ratio = title_starts / len(words)

    is_heading = (
        len(words) == 1
        or (upper_ratio >= 0.85 and len(words) <= 12)
        or stripped.endswith("?")
        or stripped.startswith("Step ")
        or (title_ratio >= 0.5 and len(stripped) <= 72)
        or len(stripped) <= 60
    )
    if not is_heading:
        return None

    normalized = re.sub(r"[^a-z0-9 ]", "", stripped.lower()).strip()
    if (
        normalized in _SUBHEADINGS
        or stripped.startswith("Step ")
        or (len(words) <= 3 and not stripped.endswith("?"))
    ):
        return "h3"
    return "h2"


def _split_list_line(line: str) -> list[Block]:
    bullet = _BULLET_RE.match(line)
    if bullet:
        indent = len(bullet.group("indent"))
        kind = "subbullet" if indent >= 2 else "bullet"
        return [(kind, f"- {bullet.group('text').strip()}")]

    parts = [part.strip() for part in _MULTI_NUMBER_RE.split(line.strip())]
    items: list[Block] = []
    for part in parts:
        if part and _NUMBERED_RE.match(part):
            items.append(("number", part))
    return items or [("body", line.strip())]


def _text_blocks(text: str) -> list[Block]:
    """Conservatively rebuild headings, paragraphs and lists from page text."""
    lines = [line.rstrip() for line in text.splitlines()]
    blocks: list[Block] = []
    pending_blank = True
    seen_content = False

    def next_content(index: int) -> str | None:
        for candidate in lines[index + 1 :]:
            value = candidate.strip()
            if value and not _is_page_marker(value):
                return value
        return None

    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if not stripped or _is_page_marker(stripped):
            pending_blank = True
            continue

        if not seen_content and _is_front_line(stripped):
            blocks.append(("front", stripped))
            pending_blank = False
            continue

        if _is_list_start(stripped):
            blocks.extend(_split_list_line(raw))
            seen_content = True
            pending_blank = False
            continue

        numbered_parts = [
            part.strip() for part in _MULTI_NUMBER_RE.split(stripped) if part.strip()
        ]
        if (
            blocks
            and blocks[-1][0] in {"number", "bullet", "subbullet"}
            and len(numbered_parts) > 1
            and any(_NUMBERED_RE.match(part) for part in numbered_parts[1:])
        ):
            kind, previous = blocks[-1]
            blocks[-1] = (kind, _join_text(previous, numbered_parts[0]))
            for part in numbered_parts[1:]:
                if _NUMBERED_RE.match(part):
                    blocks.append(("number", part))
                else:
                    blocks.append(("body", part))
            seen_content = True
            pending_blank = False
            continue

        if (
            blocks
            and blocks[-1][0] in {"body", "number", "bullet", "subbullet"}
            and not pending_blank
            and (
                blocks[-1][1].endswith("-")
                or (
                    not _ends_sentence(blocks[-1][1])
                    and (stripped[0].islower() or stripped[0] in "([")
                )
            )
        ):
            kind, previous = blocks[-1]
            blocks[-1] = (kind, _join_text(previous, stripped))
            seen_content = True
            pending_blank = False
            continue

        heading = _heading_kind(stripped, next_content(index))
        if heading:
            blocks.append((heading, stripped))
            seen_content = True
            pending_blank = False
            continue

        if (
            blocks
            and blocks[-1][0] in {"body", "number", "bullet", "subbullet"}
            and not pending_blank
            and not _ends_sentence(blocks[-1][1])
        ):
            kind, previous = blocks[-1]
            blocks[-1] = (kind, _join_text(previous, stripped))
            seen_content = True
            pending_blank = False
            continue

        blocks.append(("body", stripped))
        seen_content = True
        pending_blank = False

    return blocks


def _source_text_blocks(text: str) -> list[Block]:
    """Source passages never get big centred headings: guessed headings become
    plain bold lines so the original text keeps a calm, readable layout."""
    return [
        ("subhead", value) if kind in {"h2", "h3"} else (kind, value)
        for kind, value in _text_blocks(text)
    ]


def _source_blocks(text: str, fmt: str | None) -> list[Block]:
    """Keep DOCX source paragraphs verbatim; only PDFs need layout guessing."""
    if fmt == "docx":
        lines = text.splitlines() or [text]
        return [("raw", line) for line in lines]
    return _source_text_blocks(text)


def _ref_key(ref: dict[str, Any]) -> tuple[Any, ...]:
    if ref.get("kind") == "pdf_page" and isinstance(ref.get("page"), int):
        return ("page", int(ref["page"]))
    paragraph = ref.get("paragraph_index")
    return (
        "section",
        str(ref.get("section_label") or ""),
        int(paragraph) if isinstance(paragraph, int) else None,
    )


def _unique_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for ref in refs:
        key = _ref_key(ref)
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique


def _covers_all_refs(
    refs: list[dict[str, Any]], all_refs: list[dict[str, Any]] | None
) -> bool:
    if not all_refs:
        return False
    selected = {_ref_key(ref) for ref in refs}
    complete = {_ref_key(ref) for ref in all_refs}
    return bool(complete) and selected == complete


def _number_runs(values: list[int]) -> str:
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(f"{start}-{previous}" if start != previous else str(start))
        start = previous = value
    ranges.append(f"{start}-{previous}" if start != previous else str(start))
    return ", ".join(ranges)


def _paragraphs_label(paragraphs: list[int]) -> str:
    values = sorted(set(paragraphs))
    label = _number_runs(values)
    return f"Paragraph {label}" if len(values) == 1 else f"Paragraphs {label}"


def _ref_label(ref: dict[str, Any]) -> str:
    if ref.get("kind") == "pdf_page" and isinstance(ref.get("page"), int):
        return f"Page {ref['page']}"
    parts: list[str] = []
    label = ref.get("section_label")
    paragraph = ref.get("paragraph_index")
    if label:
        parts.append(f"Section: {label}")
    if isinstance(paragraph, int):
        parts.append(f"Paragraph {paragraph + 1}")
    return ", ".join(parts) or "Document section"


def _refs_label(
    refs: list[dict[str, Any]],
    all_refs: list[dict[str, Any]] | None = None,
    *,
    whole_document: bool = False,
) -> str:
    unique = _unique_refs(refs)
    if not unique:
        return "Document"
    if whole_document or _covers_all_refs(unique, all_refs):
        return "Whole document"

    page_refs = [
        ref
        for ref in unique
        if ref.get("kind") == "pdf_page" and isinstance(ref.get("page"), int)
    ]
    paragraph_groups: dict[str, list[int]] = {}
    other: list[dict[str, Any]] = []
    for ref in unique:
        paragraph = ref.get("paragraph_index")
        if isinstance(paragraph, int):
            section = str(ref.get("section_label") or "")
            paragraph_groups.setdefault(section, []).append(paragraph + 1)
        elif ref not in page_refs:
            other.append(ref)

    parts: list[str] = []
    if page_refs:
        parts.append(_page_range_label(page_refs) or "Pages")
    for section, paragraphs in paragraph_groups.items():
        label = _paragraphs_label(paragraphs)
        parts.append(f"Section: {section}, {label}" if section else label)
    parts.extend(_ref_label(ref) for ref in other)
    return "; ".join(parts) or "Document section"


def _payload_refs_label(payload: dict[str, Any], refs: list[dict[str, Any]]) -> str:
    return _refs_label(
        refs,
        payload.get("_export_all_refs") or [],
        whole_document=payload.get("_export_whole_document") is True,
    )


def _payload_source_text(payload: dict[str, Any], refs: list[dict[str, Any]]) -> str:
    label = _payload_refs_label(payload, refs)
    return "Source document" if label == "Document" else f"Source: {label}"


def _page_range_label(refs: list[dict[str, Any]]) -> str | None:
    """Format a page-only selection as Page N or Pages N-M."""
    if not refs or any(
        ref.get("kind") != "pdf_page" or not isinstance(ref.get("page"), int)
        for ref in refs
    ):
        return None
    pages = sorted({int(ref["page"]) for ref in refs})
    if len(pages) == 1:
        return f"Page {pages[0]}"

    ranges: list[str] = []
    start = previous = pages[0]
    for page in pages[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(f"{start}-{previous}" if start != previous else str(start))
        start = previous = page
    ranges.append(f"{start}-{previous}" if start != previous else str(start))
    return "Pages " + ", ".join(ranges)


def _summary_units(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect summary text, source references and failed-part source text."""
    units: list[dict[str, Any]] = []
    topics = [
        topic
        for topic in payload.get("topics") or []
        if isinstance(topic, dict)
    ]
    if topics:
        for topic in topics:
            material = topic.get("study_material") or {}
            grounded = material.get("source_grounded") or {}
            refs = [
                ref
                for ref in grounded.get("basis_refs") or []
                if isinstance(ref, dict)
            ]
            source_parts: list[str] = []
            for passage in topic.get("source_passages") or []:
                if not isinstance(passage, dict):
                    continue
                anchor = passage.get("anchor")
                if isinstance(anchor, dict):
                    refs.append(anchor)
                text = str(passage.get("passage") or "").strip()
                if text and not passage.get("ocr_empty"):
                    source_parts.append(text)
            units.append(
                {
                    "summary": str(grounded.get("text") or "").strip(),
                    "refs": refs,
                    "source_text": "\n\n".join(source_parts),
                }
            )
    else:
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            units.append(
                {
                    "summary": str(item.get("summary") or "").strip(),
                    "refs": [
                        ref
                        for ref in item.get("source_refs") or []
                        if isinstance(ref, dict)
                    ],
                    "source_text": str(item.get("text") or "").strip(),
                }
            )
    return units


def _build_summary_only_blocks(payload: dict[str, Any]) -> list[Block]:
    """One summary section, one small reference line, no per-part headings."""
    blocks: list[Block] = [
        ("title", _display_title(payload.get("filename") or "Study results"))
    ]
    export_date = (
        payload.get("export_date")
        or payload.get("created_at")
        or payload.get("updated_at")
    )
    if isinstance(export_date, str) and export_date:
        export_date = export_date[:10]
    if not export_date:
        export_date = datetime.now(timezone.utc).date().isoformat()
    blocks.append(("meta", f"Date: {export_date}"))
    document = payload.get("document") or {}
    source_name = payload.get("filename")
    if isinstance(document, dict) and document.get("filename"):
        source_name = document["filename"]

    units = _summary_units(payload)
    summarized = sum(1 for unit in units if unit["summary"])

    error = payload.get("error")
    if isinstance(error, dict):
        blocks.append(("h2", "Generation error"))
        blocks.append(
            ("body", str(error.get("message") or error.get("code") or ""))
        )

    overall = str(payload.get("document_summary") or "").strip()
    if not overall and len(units) == 1:
        overall = units[0]["summary"]
    include_parts = payload.get("include_parts") is True
    blocks.append(("h2", SUMMARY_HEADING))
    if overall:
        blocks.append(("body", overall))
    if include_parts or not overall:
        for unit in units:
            summary = unit["summary"]
            if summary and summary != overall:
                blocks.append(("body", summary))
    if not overall and not any(unit["summary"] for unit in units):
        blocks.append(("body", "No summary was generated."))

    # Grey notes sit below the text, never above it.
    blocks.append(("meta", AI_SUMMARY_NOTE))
    if units and summarized < len(units):
        noun = "part" if len(units) == 1 else "parts"
        blocks.append(
            ("meta", f"Summary coverage: {summarized} of {len(units)} {noun} summarized.")
        )
    refs = [ref for unit in units for ref in unit["refs"]]
    reference_label = _payload_refs_label(payload, refs) if refs else ""
    if reference_label and reference_label not in {"Document", "Document section"}:
        blocks.append(("meta", f"References: {reference_label}"))
    if source_name:
        blocks.append(("meta", f"Source: {_display_filename(source_name)}"))

    fallback_source = [
        unit["source_text"]
        for unit in units
        if not unit["summary"] and unit["source_text"]
    ]
    if fallback_source:
        blocks.append(("h3", SOURCE_TEXT_HEADING))
        for text in fallback_source:
            blocks.extend(_source_blocks(text, payload.get("format")))

    warnings = payload.get("warnings") or []
    if warnings:
        blocks.append(("h2", "Notes"))
        for warning in warnings:
            message = warning.get("message") if isinstance(warning, dict) else warning
            if message:
                blocks.append(("body", f"- {message}"))
    return blocks


def _build_topic_blocks(payload: dict[str, Any]) -> list[Block]:
    """Build organized, page-aware blocks from stored topic result passages."""
    if payload.get("mode") == "summaries_only":
        return _build_summary_only_blocks(payload)
    blocks: list[Block] = []
    blocks.append(("title", _display_title(payload.get("filename") or "Topic study")))

    export_date = (
        payload.get("export_date")
        or payload.get("created_at")
        or payload.get("updated_at")
    )
    if isinstance(export_date, str) and export_date:
        export_date = export_date[:10]
    if not export_date:
        export_date = datetime.now(timezone.utc).date().isoformat()
    blocks.append(("meta", f"Date: {export_date}"))
    requested_topics = payload.get("requested_topics")
    if requested_topics is None:
        requested_topics = [
            str(topic.get("text") or "Topic")
            for topic in payload.get("topics") or []
            if isinstance(topic, dict)
        ]
    document = payload.get("document") or {}

    error = payload.get("error")
    if isinstance(error, dict):
        blocks.append(("h2", "Generation error"))
        blocks.append(
            ("body", str(error.get("message") or error.get("code") or ""))
        )

    image_state = {
        "included": set(),
        "processed": set(),
        "bytes": 0,
        "limit_omitted": set(),
        "unavailable": set(),
        "exhausted": False,
    }
    for topic in payload.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        blocks.append(("h2", str(topic.get("text") or "Topic")))

        passages = [
            passage
            for passage in (topic.get("source_passages") or [])
            if isinstance(passage, dict)
        ]
        if passages:
            blocks.append(("h3", SOURCE_TEXT_HEADING))

            def page_sort_key(passage: dict[str, Any]) -> tuple[int, int]:
                anchor = passage.get("anchor") or {}
                page = anchor.get("page")
                return (0, int(page)) if isinstance(page, int) else (1, 0)

            for passage in sorted(
                passages,
                key=page_sort_key,
            ):
                anchor = passage.get("anchor") or {}
                page = anchor.get("page") if anchor.get("kind") == "pdf_page" else None
                page_label = (
                    f"Page {page}" if isinstance(page, int) else _ref_label(anchor)
                )
                blocks.append(("h3", page_label))
                if passage.get("ocr_empty"):
                    blocks.append(("ocr", "No text could be read from this page"))
                elif (
                    isinstance(page, int)
                    and _ocr_source_for_page(payload, page, passage) == "ocr"
                ):
                    blocks.append(("ocr", OCR_TEXT_LABEL))
                text = passage.get("passage")
                if text and not passage.get("ocr_empty"):
                    blocks.extend(_source_text_blocks(str(text)))
                if isinstance(page, int) and passage.get("has_page_image"):
                    _append_page_image(blocks, payload, page, image_state)
        elif payload.get("mode") != "summaries_only":
            blocks.append(("body", "No matching passage found"))

        material = topic.get("study_material") or {}
        if isinstance(material, dict):
            grounded = material.get("source_grounded") or {}
            if isinstance(grounded, dict) and grounded.get("text"):
                blocks.append(("h3", AI_SUMMARY_HEADING))
                blocks.append(("body", str(grounded["text"])))
                refs = grounded.get("basis_refs") or []
                if refs:
                    blocks.append(
                        ("source", f"Source: {_payload_refs_label(payload, refs)}")
                    )
            beyond = material.get("beyond_source") or {}
            if isinstance(beyond, dict) and beyond.get("text"):
                blocks.append(
                    ("h3", "Optional background beyond the uploaded material")
                )
                blocks.append(
                    ("body", "This is not grounded in the uploaded source.")
                )
                blocks.append(("body", str(beyond["text"])))

        for warning in topic.get("warnings") or []:
            message = (
                warning.get("message") if isinstance(warning, dict) else warning
            )
            if message:
                blocks.append(("body", f"- {message}"))

    if payload.get("mode") == "summaries_only" and payload.get("document_summary"):
        overall = str(payload["document_summary"]).strip()
        already_shown = any(
            isinstance(topic, dict)
            and str(
                ((topic.get("study_material") or {}).get("source_grounded") or {}).get("text")
                or ""
            ).strip()
            == overall
            for topic in payload.get("topics") or []
        )
        if not already_shown:
            blocks.append(("h2", AI_SUMMARY_HEADING))
            blocks.append(("body", str(payload["document_summary"])))

    warnings = payload.get("warnings") or []
    if warnings:
        blocks.append(("h2", "Notes"))
        for warning in warnings:
            message = warning.get("message") if isinstance(warning, dict) else warning
            if message:
                blocks.append(("body", f"- {message}"))

    # Grey notes sit below the text, never above it.
    blocks.append(
        (
            "meta",
            "Topics requested: " + (", ".join(map(str, requested_topics)) or "None"),
        )
    )
    if isinstance(document, dict) and document.get("filename"):
        blocks.append(("meta", f"Source: {_display_filename(document['filename'])}"))
    if payload.get("document_available") is False:
        blocks.append(
            (
                "meta",
                "The original source document is no longer available; "
                "these results were persisted before it was removed.",
            )
        )

    page_info = payload.get("_export_page_info") or {}
    pending = sorted(
        int(page)
        for page, info in page_info.items()
        if info.get("ocr_status") == "pending"
    )
    failed = sorted(
        int(page)
        for page, info in page_info.items()
        if info.get("ocr_status") == "failed"
    )
    if pending:
        blocks.append(
            ("warning", "OCR is pending for page(s): " + ", ".join(map(str, pending)) + ".")
        )
    if failed:
        blocks.append(
            ("warning", "OCR failed for page(s): " + ", ".join(map(str, failed)) + ".")
        )
    if image_state["limit_omitted"]:
        pages = ", ".join(map(str, sorted(image_state["limit_omitted"])))
        blocks.append(
            (
                "warning",
                f"Slide images were omitted for page(s): {pages}. "
                "The export limit is 30 images and 25 MB of images.",
            )
        )
    if image_state["unavailable"]:
        pages = ", ".join(map(str, sorted(image_state["unavailable"])))
        blocks.append(("warning", f"Slide images could not be rendered for page(s): {pages}."))

    return blocks


def _ocr_source_for_page(
    payload: dict[str, Any], page: int, passage: dict[str, Any]
) -> str:
    info_map = payload.get("_export_page_info") or {}
    info = info_map.get(page) or info_map.get(str(page)) or {}
    return str(info.get("source") or passage.get("source") or "")


def _append_page_image(
    blocks: list[Block],
    payload: dict[str, Any],
    page: int,
    state: dict[str, Any],
) -> None:
    if payload.get("include_page_images", True) is False:
        return
    if page in state["processed"]:
        if page in state["included"]:
            blocks.append(("meta", f"See page {page} above"))
        return
    state["processed"].add(page)
    if state["exhausted"]:
        state["limit_omitted"].add(page)
        return
    context = payload.get("_export_page_image_context") or {}
    if not context:
        state["unavailable"].add(page)
        return
    if len(state["included"]) >= MAX_EXPORT_IMAGES:
        state["limit_omitted"].add(page)
        return
    try:
        image_path = render_export_page_image(
            document_id=str(context["document_id"]),
            source_path=context["source_path"],
            cache_root=context["cache_root"],
            page_number=page,
        )
        size = image_path.stat().st_size
    except (ApiError, OSError, KeyError, TypeError):
        state["unavailable"].add(page)
        return
    if state["bytes"] + size > MAX_EXPORT_IMAGE_BYTES:
        state["limit_omitted"].add(page)
        state["exhausted"] = True
        return
    state["bytes"] += size
    state["included"].add(page)
    blocks.append(("page_image", str(image_path)))


def add_page_export_context(
    payload: dict[str, Any],
    *,
    document: Any,
    page_records: list[dict[str, Any]],
    cache_root: str | Path,
    include_page_images: bool,
) -> None:
    """Attach ephemeral page metadata and image access for one export call."""
    payload["include_page_images"] = include_page_images
    info: dict[int, dict[str, Any]] = {}
    for page in page_records:
        number = page.get("page_number")
        if not isinstance(number, int):
            number = int(page.get("index", -1)) + 1
        if number > 0:
            info[number] = {
                "source": page.get("source"),
                "ocr_status": page.get("ocr_status"),
                "has_page_image": bool(page.get("has_page_image")),
                "ocr_empty": bool(page.get("ocr_empty")),
            }
    payload["_export_page_info"] = info
    all_refs: list[dict[str, Any]] = []
    seen_refs: set[tuple[Any, ...]] = set()
    for section in getattr(document, "sections", None) or []:
        anchor = section.get("anchor") if isinstance(section, dict) else None
        if not isinstance(anchor, dict):
            continue
        key = _ref_key(anchor)
        if key not in seen_refs:
            seen_refs.add(key)
            all_refs.append(anchor)
    if all_refs:
        payload["_export_all_refs"] = all_refs

    if (
        document is not None
        and getattr(document, "format", None) == "pdf"
        and getattr(document, "available", False)
    ):
        payload["_export_page_image_context"] = {
            "document_id": document.document_id,
            "source_path": document.path,
            "cache_root": cache_root,
        }

    sources: list[dict[str, Any]] = []
    for topic in payload.get("topics") or []:
        if isinstance(topic, dict):
            sources.extend(
                item
                for item in topic.get("source_passages") or []
                if isinstance(item, dict)
            )
    for topic in payload.get("_export_topics") or []:
        if isinstance(topic, dict):
            sources.extend(
                item
                for item in topic.get("source_passages") or []
                if isinstance(item, dict)
            )
    for passage in sources:
        anchor = passage.get("anchor") or {}
        number = anchor.get("page")
        if isinstance(number, int) and number in info:
            passage["has_page_image"] = bool(info[number]["has_page_image"])
            passage["source"] = info[number]["source"]
            passage["ocr_empty"] = bool(
                info[number]["ocr_empty"]
                and (
                    passage.get("ocr_empty")
                    or not str(passage.get("passage") or "").strip()
                )
            )

    selected: set[int] = set()
    for passage in sources:
        number = (passage.get("anchor") or {}).get("page")
        if isinstance(number, int):
            selected.add(number)
    for item in payload.get("items") or []:
        for ref in item.get("source_refs") or []:
            number = ref.get("page")
            if isinstance(number, int):
                selected.add(number)
    payload["_export_page_info"] = {
        number: info[number] for number in selected if number in info
    }


def organize_study_job_payload(payload: dict[str, Any]) -> None:
    """Expose each study unit as an export topic, preserving source text only."""
    topics: list[dict[str, Any]] = []
    page_info = payload.get("_export_page_info") or {}
    for index, item in enumerate(payload.get("items") or []):
        refs = item.get("source_refs") or []
        passages: list[dict[str, Any]] = []
        page_refs = sorted(
            (
                ref
                for ref in refs
                if ref.get("kind") == "pdf_page"
                and isinstance(ref.get("page"), int)
            ),
            key=lambda ref: int(ref["page"]),
        )
        include_source = (
            payload.get("mode") != "summaries_only" or not item.get("summary")
        )
        if (
            page_refs
            and item.get("text")
            and include_source
            and item.get("_export_page_passages")
        ):
            # The route builds these per page from stored Section.raw_text.
            provided = item.get("_export_page_passages") or []
            for page_index, ref in enumerate(page_refs):
                page = int(ref["page"])
                info = page_info.get(page) or {}
                text = (
                    str(provided[page_index].get("text") or "")
                    if page_index < len(provided)
                    else str(item["text"])
                )
                passages.append({
                    "passage": text,
                    "anchor": ref,
                    "has_page_image": bool(info.get("has_page_image")),
                    "source": info.get("source"),
                })
        elif item.get("text") and include_source:
            passages.append({
                "passage": str(item["text"]),
                "anchor": refs[0] if refs else {},
            })
        summary = item.get("summary")
        material = (
            {"source_grounded": {"text": str(summary), "basis_refs": refs}}
            if summary
            else {}
        )
        topics.append({
            "text": (
                _page_range_label(refs)
                or (_payload_refs_label(payload, refs) if refs else f"Part {index + 1}")
            ),
            "match_status": "matched" if passages or summary else "unmatched",
            "material_status": "generated" if summary else "not_requested",
            "source_passages": passages,
            "study_material": material,
            "warnings": item.get("warnings") or [],
        })
    payload["_export_topics"] = topics


def build_blocks(payload: dict[str, Any]) -> list[Block]:
    """Mode-aware content blocks derived only from the persisted payload.

    ``summaries_only`` includes source text only for sections whose summaries
    failed, matching the partial result shown in chat.
    """
    if payload.get("_export_topics") is not None:
        normalized = dict(payload)
        normalized["kind"] = "topic_study"
        normalized["topics"] = payload["_export_topics"]
        return _build_topic_blocks(normalized)
    if payload.get("kind") == "topic_study":
        return _build_topic_blocks(payload)
    mode = payload.get("mode") or ""
    blocks: list[Block] = []
    blocks.append(("title", _display_title(payload.get("filename") or "Study results")))

    error = payload.get("error")
    if error:
        blocks.append(("h2", "Processing error"))
        blocks.append(("body", str(error.get("message") or error.get("code") or "")))

    items = payload.get("items") or []

    if mode == "summaries_only":
        return _build_summary_only_blocks(payload)
    if mode == "text_and_summaries":
        for item in items:
            text = item.get("text")
            summary = item.get("summary")
            if not text and not summary:
                continue
            blocks.append(
                (
                    "source",
                    _payload_source_text(payload, item.get("source_refs") or []),
                )
            )
            if summary:
                blocks.append(("h3", AI_SUMMARY_HEADING))
                blocks.append(("body", summary))
            elif text:
                blocks.append(("meta", "AI summary unavailable for this part."))
            if text:
                blocks.append(("h3", SOURCE_TEXT_HEADING))
                blocks.extend(_source_blocks(text, payload.get("format")))
    else:  # extracted_text_only and any unknown mode: source text only
        for item in items:
            text = item.get("text")
            if text:
                blocks.append(
                    (
                        "source",
                        _payload_source_text(payload, item.get("source_refs") or []),
                    )
                )
                blocks.extend(_source_blocks(text, payload.get("format")))

    if mode == "text_and_summaries" and items:
        summarized = sum(1 for item in items if item.get("summary"))
        part = "part" if len(items) == 1 else "parts"
        blocks.append(
            (
                "meta",
                f"Summary coverage: {summarized} of {len(items)} {part} summarized.",
            )
        )

    warnings = payload.get("warnings") or []
    if warnings:
        blocks.append(("h2", "Notes"))
        for warning in warnings:
            message = warning.get("message") if isinstance(warning, dict) else warning
            if message:
                blocks.append(("body", f"- {message}"))

    return blocks


def _break_long_word(
    word: str, font: pymupdf.Font, size: float, width: float
) -> list[str]:
    """Split a word that is wider than a line without re-measuring every prefix."""
    pieces: list[str] = []
    remaining = word
    while remaining:
        if font.text_length(remaining, fontsize=size) <= width:
            pieces.append(remaining)
            break
        low, high = 1, len(remaining)
        while low < high:
            mid = (low + high + 1) // 2
            if font.text_length(remaining[:mid], fontsize=size) <= width:
                low = mid
            else:
                high = mid - 1
        pieces.append(remaining[:low])
        remaining = remaining[low:]
    return pieces


class _PdfWriter:
    def __init__(
        self,
        *,
        text_align: str = "left",
        heading_align: str = "center",
    ) -> None:
        self._text_align = (
            text_align
            if text_align in _PDF_TEXT_ALIGN or text_align == "left"
            else "left"
        )
        self._heading_align = (
            heading_align if heading_align in {"left", "center"} else "center"
        )
        self._doc = pymupdf.open()
        self._page: pymupdf.Page | None = None
        self._shape: pymupdf.Shape | None = None
        self._y = MARGIN
        self._fonts: dict[str, pymupdf.Font] = {}
        self._font_xrefs: dict[str, int] = {}
        self._page_fonts: set[str] = set()
        self._width_cache: dict[tuple[str, float], float] = {}
        self._new_page()

    def _flush_shape(self) -> None:
        if self._shape is not None:
            self._shape.commit()
            self._shape = None

    def _new_page(self) -> None:
        self._flush_shape()
        self._page = self._doc.new_page(width=PAGE_W, height=PAGE_H)
        self._shape = self._page.new_shape()
        self._page_fonts = set()
        self._y = MARGIN

    def _ensure(self, height: float) -> None:
        if self._y + height > PAGE_H - MARGIN:
            self._new_page()

    def _font_path(self, name: str) -> Path:
        path = _PDF_FONT_FILES.get(name)
        if path is None or not path.is_file():
            raise RuntimeError(f"The PDF font asset is missing: {path}")
        return path

    def _font(self, name: str) -> pymupdf.Font:
        font = self._fonts.get(name)
        if font is None:
            font = pymupdf.Font(fontfile=str(self._font_path(name)))
            self._fonts[name] = font
        return font

    def _resource_xref(self) -> int:
        assert self._page is not None
        kind, value = self._doc.xref_get_key(self._page.xref, "Resources")
        if kind == "xref":
            return int(value.split()[0])
        resource_xref = self._doc.get_new_xref()
        self._doc.update_object(resource_xref, value if kind == "dict" else "<<>>")
        self._doc.xref_set_key(self._page.xref, "Resources", f"{resource_xref} 0 R")
        return resource_xref

    def _attach_font(self, name: str) -> None:
        assert self._page is not None
        names = [*sorted(self._page_fonts), name]
        fonts = " ".join(
            f"/{_PDF_FONT_ALIASES[item]} {self._font_xrefs[item]} 0 R"
            for item in names
        )
        self._doc.xref_set_key(
            self._resource_xref(),
            "Font",
            f"<< {fonts} >>",
        )

    def _register_font(self, name: str) -> None:
        if name in self._page_fonts:
            return
        assert self._page is not None
        if name in self._font_xrefs:
            self._attach_font(name)
        else:
            self._font_xrefs[name] = self._page.insert_font(
                fontname=_PDF_FONT_ALIASES[name],
                fontfile=str(self._font_path(name)),
            )
        self._page_fonts.add(name)

    def _text_width(self, text: str, size: float, font: str = BODY_FONT) -> float:
        key = (font, text, size)
        cached = self._width_cache.get(key)
        if cached is not None:
            return cached
        width = self._font(font).text_length(text, fontsize=size)
        if len(self._width_cache) < 50000:
            self._width_cache[key] = width
        return width

    def _wrap_lines(
        self,
        text: str,
        size: float,
        font: str,
        width: float,
    ) -> list[str]:
        lines: list[str] = []
        space_width = self._text_width(" ", size, font)
        for raw in _sanitize(text).split("\n"):
            current: list[str] = []
            current_width = 0.0
            for word in raw.split(" "):
                if not word:
                    continue
                word_width = self._text_width(word, size, font)
                if word_width > width:
                    if current:
                        lines.append(" ".join(current))
                        current = []
                        current_width = 0.0
                    pieces = _break_long_word(
                        word, self._font(font), size, width
                    )
                    for piece in pieces[:-1]:
                        lines.append(piece)
                    current = [pieces[-1]]
                    current_width = self._text_width(pieces[-1], size, font)
                    continue
                if current and current_width + space_width + word_width > width:
                    lines.append(" ".join(current))
                    current = [word]
                    current_width = word_width
                else:
                    if current:
                        current_width += space_width
                    current.append(word)
                    current_width += word_width
            lines.append(" ".join(current))
        return lines or [""]

    def _aligned_block(
        self,
        lines: list[str],
        size: float,
        font: str,
        *,
        color: tuple[float, float, float] | None = None,
        indent: float = 0.0,
        align: str = "left",
    ) -> None:
        if not lines:
            return
        if align == "left":
            for line in lines:
                self._line(line, size, font, color=color, indent=indent)
            return
        leading = size * 1.45
        remaining = list(lines)
        while remaining:
            self._ensure(leading)
            assert self._shape is not None
            available = PAGE_H - MARGIN - self._y
            count = max(1, int(available // leading))
            chunk = remaining[:count]
            self._register_font(font)
            kwargs: dict[str, Any] = {}
            if color is not None:
                kwargs["color"] = color
            height = leading * len(chunk)
            rect = pymupdf.Rect(
                MARGIN + indent,
                self._y,
                PAGE_W - MARGIN,
                self._y + height,
            )
            self._shape.insert_textbox(
                rect,
                "\n".join(_sanitize(line) for line in chunk),
                fontsize=size,
                fontname=_PDF_FONT_ALIASES[font],
                fontfile=str(self._font_path(font)),
                align=_PDF_TEXT_ALIGN.get(align, 0),
                **kwargs,
            )
            self._y += height
            remaining = remaining[count:]

    def _line(
        self,
        text: str,
        size: float,
        font: str,
        *,
        color: tuple[float, float, float] | None = None,
        indent: float = 0.0,
        center: bool = False,
    ) -> None:
        leading = size * 1.45
        self._ensure(leading)
        assert self._shape is not None
        if text:
            self._register_font(font)
            clean = _sanitize(text)
            x = MARGIN + indent
            if center:
                width = PAGE_W - 2 * MARGIN
                text_width = self._font(font).text_length(clean, fontsize=size)
                x = MARGIN + max(0.0, (width - text_width) / 2)
            kwargs: dict[str, Any] = {}
            if color is not None:
                kwargs["color"] = color
            self._shape.insert_text(
                (x, self._y + size),
                clean,
                fontsize=size,
                fontname=_PDF_FONT_ALIASES[font],
                **kwargs,
            )
        self._y += leading

    def heading(
        self,
        text: str,
        size: float,
        *,
        before: float = 10.0,
        after: float = 2.0,
    ) -> None:
        width = PAGE_W - 2 * MARGIN
        lines = self._wrap_lines(text, size, BOLD_FONT, width)
        leading = size * 1.45
        self._y += before
        self._ensure(leading * (len(lines) + 0.7))
        self._aligned_block(
            lines,
            size,
            BOLD_FONT,
            color=_HEADING_COLOR,
            align=self._heading_align,
        )
        self._y += after

    def paragraph(
        self,
        text: str,
        size: float = PDF_BODY_SIZE,
        *,
        indent: float = 0.0,
        after: float = 4.0,
    ) -> None:
        width = PAGE_W - 2 * MARGIN - indent
        self._aligned_block(
            self._wrap_lines(text, size, BODY_FONT, width),
            size,
            BODY_FONT,
            indent=indent,
            align=self._text_align,
        )
        self._y += after

    def subhead(self, text: str) -> None:
        width = PAGE_W - 2 * MARGIN
        lines = self._wrap_lines(text, PDF_SUBHEAD_SIZE, BOLD_FONT, width)
        self._y += 4.0
        self._ensure(PDF_SUBHEAD_SIZE * 1.45 * (len(lines) + 0.7))
        self._aligned_block(
            lines, PDF_SUBHEAD_SIZE, BOLD_FONT, color=_HEADING_COLOR, align="left"
        )
        self._y += 1.0

    def front(self, text: str) -> None:
        width = PAGE_W - 2 * MARGIN
        for line in self._wrap_lines(text, PDF_FRONT_SIZE, BOLD_FONT, width):
            self._line(
                line,
                PDF_FRONT_SIZE,
                BOLD_FONT,
                color=_HEADING_COLOR,
                center=True,
            )

    def source(self, text: str) -> None:
        self._y += 8
        width = PAGE_W - 2 * MARGIN
        self._aligned_block(
            self._wrap_lines(text, PDF_SOURCE_SIZE, BODY_FONT, width),
            PDF_SOURCE_SIZE,
            BODY_FONT,
            color=_MUTED_COLOR,
        )
        self._y += 2

    def meta(self, text: str, size: float = PDF_META_SIZE) -> None:
        width = PAGE_W - 2 * MARGIN
        self._aligned_block(
            self._wrap_lines(text, size, BODY_FONT, width),
            size,
            BODY_FONT,
            color=_MUTED_COLOR,
        )

    def image(self, path: str) -> None:
        pixmap = pymupdf.Pixmap(path)
        content_width = PAGE_W - 2 * MARGIN
        self._y += 4.0
        full_width = content_width
        full_height = full_width * pixmap.height / max(pixmap.width, 1)
        max_height = PAGE_H - 2 * MARGIN - 8.0
        if full_height > max_height:
            full_height = max_height
            full_width = full_height * pixmap.width / max(pixmap.height, 1)
        remaining_height = PAGE_H - MARGIN - 8.0 - self._y
        if full_height <= remaining_height:
            width = full_width
            height = full_height
        else:
            fit_height = max(0.0, remaining_height)
            fit_width = fit_height * pixmap.width / max(pixmap.height, 1)
            if fit_width >= full_width * 0.55:
                width = fit_width
                height = fit_height
            else:
                self._new_page()
                width = full_width
                height = full_height
        self._flush_shape()
        assert self._page is not None
        x = MARGIN + (content_width - width) / 2
        self._page.insert_image(
            pymupdf.Rect(x, self._y, x + width, self._y + height),
            filename=path,
        )
        self._y += height + 8.0
        self._shape = self._page.new_shape()

    def spacer(self, height: float = 6.0) -> None:
        self._y += height

    def _add_page_numbers(self) -> None:
        total = self._doc.page_count
        for index, page in enumerate(self._doc):
            label = f"Page {index + 1} of {total}"
            width = self._font(BODY_FONT).text_length(label, fontsize=8.5)
            page.insert_text(
                (PAGE_W / 2 - width / 2, PAGE_H - 28),
                label,
                fontsize=8.5,
                fontname="helv",
                color=_MUTED_COLOR,
            )

    def save(self, title: str = "") -> bytes:
        self._flush_shape()
        self._add_page_numbers()
        self._doc.set_metadata(
            {
                "title": title,
                "author": "AI Study Assistant",
                "subject": "Study material",
                "creator": "AI Study Assistant",
                "producer": "AI Study Assistant",
            }
        )
        self._doc.subset_fonts()
        data = self._doc.tobytes()
        self._doc.close()
        return data


def render_pdf(payload: dict[str, Any]) -> bytes:
    writer = _PdfWriter(
        text_align=str(payload.get("text_align") or "left"),
        heading_align=str(payload.get("heading_align") or "center"),
    )
    title = ""
    for kind, text in build_blocks(payload):
        if kind == "title":
            title = text
            writer.heading(text, PDF_TITLE_SIZE, before=0.0, after=8.0)
        elif kind == "front":
            writer.front(text)
        elif kind == "source":
            writer.source(text)
        elif kind == "meta":
            writer.meta(text)
        elif kind == "ocr":
            writer.meta(text, size=PDF_OCR_SIZE)
        elif kind == "warning":
            writer.paragraph(text, size=PDF_WARNING_SIZE)
        elif kind == "page_image":
            writer.image(text)
        elif kind == "h2":
            writer.heading(text, PDF_H2_SIZE, before=12.0, after=2.0)
        elif kind == "h3":
            writer.heading(text, PDF_H3_SIZE, before=8.0, after=1.0)
        elif kind == "subhead":
            writer.subhead(text)
        elif kind in {"bullet", "subbullet", "number"}:
            indent = 34.0 if kind == "subbullet" else 20.0
            writer.paragraph(text, indent=indent, after=1.5)
        elif kind == "raw":
            writer.paragraph(text, after=2.0)
        else:
            writer.paragraph(text)
    writer.spacer(6.0)
    return writer.save(title)


def _set_style_font(
    style: Any,
    name: str,
    size: float,
    *,
    bold: bool | None = None,
    color: RGBColor | None = None,
) -> None:
    style.font.name = name
    style.font.size = Pt(size)
    if bold is not None:
        style.font.bold = bold
    if color is not None:
        style.font.color.rgb = color

    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.rFonts
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for attribute in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rfonts.set(qn(attribute), name)


def _set_run_font(
    run: Any,
    size: float,
    *,
    bold: bool | None = None,
    color: RGBColor | None = None,
) -> None:
    run.font.name = _DOCX_BODY_FONT
    run.font.size = Pt(size)
    if bold is not None:
        run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def _append_field(paragraph: Any, instruction: str) -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    field = OxmlElement("w:instrText")
    field.set(qn("xml:space"), "preserve")
    field.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = "1"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.extend([begin, field, separate, placeholder, end])
    _set_run_font(run, 9.0, color=_DOCX_MUTED_COLOR)


def _configure_docx(document: Any, title: str) -> None:
    section = document.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    normal = document.styles["Normal"]
    _set_style_font(normal, _DOCX_BODY_FONT, DOCX_BODY_SIZE)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15

    title_style = document.styles["Title"]
    _set_style_font(
        title_style,
        _DOCX_BODY_FONT,
        DOCX_TITLE_SIZE,
        bold=True,
        color=_DOCX_HEADING_COLOR,
    )
    title_style.paragraph_format.space_before = Pt(0)
    title_style.paragraph_format.space_after = Pt(10)
    title_style.paragraph_format.keep_with_next = True
    title_p_pr = title_style.element.get_or_add_pPr()
    for child in list(title_p_pr):
        if child.tag == qn("w:pBdr"):
            title_p_pr.remove(child)

    heading_two = document.styles["Heading 2"]
    _set_style_font(
        heading_two,
        _DOCX_BODY_FONT,
        DOCX_H2_SIZE,
        bold=True,
        color=_DOCX_HEADING_COLOR,
    )
    heading_two.paragraph_format.space_before = Pt(14)
    heading_two.paragraph_format.space_after = Pt(4)
    heading_two.paragraph_format.keep_with_next = True

    heading_three = document.styles["Heading 3"]
    _set_style_font(
        heading_three,
        _DOCX_BODY_FONT,
        DOCX_H3_SIZE,
        bold=True,
        color=_DOCX_HEADING_COLOR,
    )
    heading_three.paragraph_format.space_before = Pt(9)
    heading_three.paragraph_format.space_after = Pt(3)
    heading_three.paragraph_format.keep_with_next = True

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.space_before = Pt(0)
    footer.paragraph_format.space_after = Pt(0)
    prefix = footer.add_run("Page ")
    _set_run_font(prefix, 9.0, color=_DOCX_MUTED_COLOR)
    _append_field(footer, "PAGE")
    middle = footer.add_run(" of ")
    _set_run_font(middle, 9.0, color=_DOCX_MUTED_COLOR)
    _append_field(footer, "NUMPAGES")

    properties = document.core_properties
    properties.title = title
    properties.subject = "Study material"
    properties.author = "AI Study Assistant"
    properties.last_modified_by = "AI Study Assistant"
    properties.comments = ""


def render_docx(payload: dict[str, Any]) -> bytes:
    blocks = build_blocks(payload)
    text_alignment = _DOCX_TEXT_ALIGN.get(
        str(payload.get("text_align") or "left"), WD_ALIGN_PARAGRAPH.LEFT
    )
    heading_alignment = _DOCX_TEXT_ALIGN.get(
        str(payload.get("heading_align") or "center"), WD_ALIGN_PARAGRAPH.CENTER
    )
    title = next((text for kind, text in blocks if kind == "title"), "Study results")
    document = DocxDocument()
    _configure_docx(document, title)

    for kind, text in blocks:
        if kind == "title":
            paragraph = document.add_paragraph(text, style="Title")
            paragraph.alignment = heading_alignment
        elif kind == "front":
            paragraph = document.add_paragraph(text)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_after = Pt(0)
            for run in paragraph.runs:
                _set_run_font(
                    run,
                    DOCX_FRONT_SIZE,
                    bold=True,
                    color=_DOCX_HEADING_COLOR,
                )
        elif kind == "source":
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.space_before = Pt(10)
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.keep_with_next = True
            _set_run_font(
                paragraph.add_run(text),
                DOCX_SOURCE_SIZE,
                bold=True,
                color=_DOCX_MUTED_COLOR,
            )
        elif kind == "meta":
            paragraph = document.add_paragraph(text)
            paragraph.paragraph_format.space_after = Pt(2)
            for run in paragraph.runs:
                _set_run_font(run, DOCX_META_SIZE, color=_DOCX_MUTED_COLOR)
        elif kind == "ocr":
            paragraph = document.add_paragraph(text)
            paragraph.paragraph_format.space_after = Pt(1)
            for run in paragraph.runs:
                _set_run_font(run, DOCX_OCR_SIZE, color=_DOCX_MUTED_COLOR)
        elif kind == "warning":
            paragraph = document.add_paragraph(text)
            paragraph.alignment = text_alignment
            for run in paragraph.runs:
                _set_run_font(
                    run,
                    DOCX_WARNING_SIZE,
                    bold=True,
                    color=RGBColor(0x9C, 0x3B, 0x30),
                )
        elif kind == "page_image":
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.add_run().add_picture(text, width=Cm(16.6))
        elif kind == "h2":
            paragraph = document.add_heading(text, level=2)
            paragraph.alignment = heading_alignment
        elif kind == "h3":
            paragraph = document.add_heading(text, level=3)
            paragraph.alignment = heading_alignment
        elif kind == "subhead":
            paragraph = document.add_paragraph()
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            paragraph.paragraph_format.space_before = Pt(6)
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.keep_with_next = True
            _set_run_font(
                paragraph.add_run(text),
                DOCX_SUBHEAD_SIZE,
                bold=True,
                color=_DOCX_HEADING_COLOR,
            )
        elif kind == "raw":
            paragraph = document.add_paragraph(text)
            paragraph.alignment = text_alignment
            paragraph.paragraph_format.space_after = Pt(2)
        elif kind in {"bullet", "number", "subbullet"}:
            paragraph = document.add_paragraph(text)
            paragraph.alignment = text_alignment
            paragraph.paragraph_format.left_indent = Cm(
                1.25 if kind == "subbullet" else 0.75
            )
            paragraph.paragraph_format.first_line_indent = Cm(-0.4)
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.line_spacing = 1.1
            paragraph.paragraph_format.keep_together = True
        else:
            paragraph = document.add_paragraph(text)
            paragraph.alignment = text_alignment
            paragraph.paragraph_format.space_after = Pt(6)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


_MAX_EXPORT_STEM = 120


def _sanitize_part(value: object, default: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or ""))
    return text.strip("-._") or default


def export_filename(payload: dict[str, Any], fmt: str) -> str:
    safe = _sanitize_part(_display_title(payload.get("filename")), "study")
    mode = _sanitize_part(payload.get("mode"), "study")
    max_safe = _MAX_EXPORT_STEM - len(mode) - 1
    if max_safe < 1:
        max_safe = 1
    safe = safe[:max_safe].rstrip("-._") or "study"
    return f"{safe}-{mode}.{fmt}"


def render(payload: dict[str, Any], fmt: str) -> bytes:
    if fmt == "pdf":
        return render_pdf(payload)
    return render_docx(payload)


EXPORT_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
