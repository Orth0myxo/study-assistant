from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pymupdf
from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

PAGE_W = 595.0
PAGE_H = 842.0
MARGIN = 56.0
BODY_FONT = "body"
BOLD_FONT = "bold"
_HEADING_COLOR = (0.12, 0.18, 0.25)
_MUTED_COLOR = (0.42, 0.45, 0.50)
_DOCX_HEADING_COLOR = RGBColor(0x1F, 0x3A, 0x5F)
_DOCX_MUTED_COLOR = RGBColor(0x62, 0x6B, 0x78)
_DOCX_BODY_FONT = "Calibri"
_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_PDF_FONT_FILES = {
    BODY_FONT: _FONT_DIR / "DejaVuSans.ttf",
    BOLD_FONT: _FONT_DIR / "DejaVuSans-Bold.ttf",
}
_PDF_FONT_ALIASES = {BODY_FONT: "F0", BOLD_FONT: "F1"}

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

    letters = [char for char in stem if char.isalpha()]
    if letters and all(char.isupper() for char in letters):
        words: list[str] = []
        for token in stem.split(" "):
            core = token.strip(".,:;()[]{}")
            if core.isupper() and 1 < len(core) <= 3:
                words.append(token)
            else:
                words.append(token.capitalize())
        stem = " ".join(words)

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


def _source_text(refs: list[dict[str, Any]]) -> str:
    label = _refs_label(refs)
    return "Source document" if label == "Document" else f"Source: {label}"


def _ref_label(ref: dict[str, Any]) -> str:
    if ref.get("kind") == "pdf_page" and isinstance(ref.get("page"), int):
        return f"Page {ref['page']}"
    parts: list[str] = []
    label = ref.get("section_label")
    paragraph = ref.get("paragraph_index")
    if label:
        parts.append(f"Section: {label}")
    if isinstance(paragraph, int):
        parts.append(f"paragraph {paragraph}")
    return ", ".join(parts) or "Document section"


def _refs_label(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "Document"
    return " · ".join(_ref_label(ref) for ref in refs)


_MATERIAL_LABELS = {
    "not_requested": "source passages only",
    "generated": "AI material generated",
    "skipped_ambiguous": "skipped: ambiguous match",
    "skipped_unmatched": "skipped: no match",
    "skipped_no_evidence": "skipped: no evidence",
    "ai_failed": "AI generation failed",
}

_MATCH_LABELS = {
    "matched": "matched",
    "ambiguous": "needs review (ambiguous)",
    "unmatched": "unmatched",
}


def _build_topic_blocks(payload: dict[str, Any]) -> list[Block]:
    """Mode-aware blocks for a persisted topic study result."""
    blocks: list[Block] = []
    blocks.append(("title", _display_title(payload.get("filename") or "Topic study")))

    document = payload.get("document") or {}
    if isinstance(document, dict) and document.get("filename"):
        blocks.append(("meta", f"Source: {document['filename']}"))
    if payload.get("document_available") is False:
        blocks.append(
            (
                "meta",
                "The original source document is no longer available; "
                "these results were persisted before it was removed.",
            )
        )

    error = payload.get("error")
    if isinstance(error, dict):
        blocks.append(("h2", "Generation error"))
        blocks.append(
            ("body", str(error.get("message") or error.get("code") or ""))
        )

    for topic in payload.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        blocks.append(("h2", str(topic.get("text") or "Topic")))
        blocks.append(
            (
                "meta",
                f"{_MATCH_LABELS.get(str(topic.get('match_status')), str(topic.get('match_status') or 'unknown'))}"
                " · "
                f"{_MATERIAL_LABELS.get(str(topic.get('material_status')), str(topic.get('material_status') or 'unknown'))}",
            )
        )

        passages = topic.get("source_passages") or []
        if passages:
            blocks.append(("h3", "Original source passages"))
            for passage in passages:
                if not isinstance(passage, dict):
                    continue
                blocks.append(
                    ("meta", _refs_label([passage.get("anchor") or {}]))
                )
                text = passage.get("excerpt") or passage.get("passage")
                if text:
                    blocks.append(("body", str(text)))

        material = topic.get("study_material") or {}
        if isinstance(material, dict):
            grounded = material.get("source_grounded") or {}
            if isinstance(grounded, dict) and grounded.get("text"):
                blocks.append(("h3", "AI study material (source-grounded)"))
                blocks.append(("body", str(grounded["text"])))
                refs = grounded.get("basis_refs") or []
                if refs:
                    blocks.append(("meta", f"Based on: {_refs_label(refs)}"))
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

    warnings = payload.get("warnings") or []
    if warnings:
        blocks.append(("h2", "Notes"))
        for warning in warnings:
            message = warning.get("message") if isinstance(warning, dict) else warning
            if message:
                blocks.append(("body", f"- {message}"))

    return blocks


def build_blocks(payload: dict[str, Any]) -> list[Block]:
    """Mode-aware content blocks derived only from the persisted payload.

    ``summaries_only`` never includes ``item["text"]`` (which is ``None`` in that
    mode), so the full extracted source is never written to an export.
    """
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
        summary = payload.get("document_summary")
        if summary:
            blocks.append(("h2", "Overall summary"))
            blocks.append(("body", summary))
        for item in items:
            item_summary = item.get("summary")
            if item_summary:
                blocks.append(("source", _source_text(item.get("source_refs") or [])))
                blocks.append(("body", item_summary))
    elif mode == "text_and_summaries":
        for item in items:
            text = item.get("text")
            summary = item.get("summary")
            if not text and not summary:
                continue
            blocks.append(("source", _source_text(item.get("source_refs") or [])))
            if text:
                blocks.append(("h3", "Source text"))
                blocks.extend(_text_blocks(text))
            if summary:
                blocks.append(("h3", "AI summary"))
                blocks.append(("body", summary))
    else:  # extracted_text_only and any unknown mode: source text only
        for item in items:
            text = item.get("text")
            if text:
                blocks.append(("source", _source_text(item.get("source_refs") or [])))
                blocks.extend(_text_blocks(text))

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
    def __init__(self) -> None:
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
        for line in lines:
            self._line(line, size, BOLD_FONT, color=_HEADING_COLOR)
        self._y += after

    def paragraph(
        self,
        text: str,
        size: float = 11.0,
        *,
        indent: float = 0.0,
        after: float = 4.0,
    ) -> None:
        width = PAGE_W - 2 * MARGIN - indent
        for line in self._wrap_lines(text, size, BODY_FONT, width):
            self._line(line, size, BODY_FONT, indent=indent)
        self._y += after

    def front(self, text: str) -> None:
        width = PAGE_W - 2 * MARGIN
        for line in self._wrap_lines(text, 10.0, BOLD_FONT, width):
            self._line(
                line,
                10.0,
                BOLD_FONT,
                color=_HEADING_COLOR,
                center=True,
            )

    def source(self, text: str) -> None:
        self._y += 8
        self._line(text, 8.5, BODY_FONT, color=_MUTED_COLOR)
        self._y += 2

    def meta(self, text: str) -> None:
        self._line(text, 9.0, BODY_FONT, color=_MUTED_COLOR)

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
    writer = _PdfWriter()
    title = ""
    for kind, text in build_blocks(payload):
        if kind == "title":
            title = text
            writer.heading(text, 22.0, before=0.0, after=8.0)
        elif kind == "front":
            writer.front(text)
        elif kind == "source":
            writer.source(text)
        elif kind == "meta":
            writer.meta(text)
        elif kind == "h2":
            writer.heading(text, 15.0, before=12.0, after=2.0)
        elif kind == "h3":
            writer.heading(text, 12.5, before=8.0, after=1.0)
        elif kind in {"bullet", "subbullet", "number"}:
            indent = 34.0 if kind == "subbullet" else 20.0
            writer.paragraph(text, indent=indent, after=1.5)
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
    _set_style_font(normal, _DOCX_BODY_FONT, 11.0)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.15

    title_style = document.styles["Title"]
    _set_style_font(
        title_style,
        _DOCX_BODY_FONT,
        22.0,
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
        15.0,
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
        12.5,
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
    title = next((text for kind, text in blocks if kind == "title"), "Study results")
    document = DocxDocument()
    _configure_docx(document, title)

    for kind, text in blocks:
        if kind == "title":
            document.add_paragraph(text, style="Title")
        elif kind == "front":
            paragraph = document.add_paragraph(text)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.space_after = Pt(0)
            for run in paragraph.runs:
                _set_run_font(
                    run,
                    10.0,
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
                8.5,
                bold=True,
                color=_DOCX_MUTED_COLOR,
            )
        elif kind == "meta":
            paragraph = document.add_paragraph(text)
            paragraph.paragraph_format.space_after = Pt(2)
            for run in paragraph.runs:
                _set_run_font(run, 9.0, color=_DOCX_MUTED_COLOR)
        elif kind == "h2":
            document.add_heading(text, level=2)
        elif kind == "h3":
            document.add_heading(text, level=3)
        elif kind in {"bullet", "number", "subbullet"}:
            paragraph = document.add_paragraph(text)
            paragraph.paragraph_format.left_indent = Cm(
                1.25 if kind == "subbullet" else 0.75
            )
            paragraph.paragraph_format.first_line_indent = Cm(-0.4)
            paragraph.paragraph_format.space_after = Pt(2)
            paragraph.paragraph_format.line_spacing = 1.1
            paragraph.paragraph_format.keep_together = True
        else:
            paragraph = document.add_paragraph(text)
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
