from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf
from docx import Document as DocxDocument
from docx.shared import Pt

PAGE_W = 595.0
PAGE_H = 842.0
MARGIN = 56.0
BODY_FONT = "body"
BOLD_FONT = "bold"
_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_PDF_FONT_FILES = {
    BODY_FONT: _FONT_DIR / "DejaVuSans.ttf",
    BOLD_FONT: _FONT_DIR / "DejaVuSans-Bold.ttf",
}
_PDF_FONT_ALIASES = {BODY_FONT: "F0", BOLD_FONT: "F1"}

MODE_LABELS = {
    "extracted_text_only": "Extracted text only",
    "text_and_summaries": "Text + AI summaries",
    "summaries_only": "AI summaries only",
    "source_passages": "Original passages only",
    "ai_study_material": "AI study material",
    "both": "Original passages + AI study material",
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
    mode = payload.get("mode") or ""
    blocks.append(("title", payload.get("filename") or "Topic study"))
    blocks.append(
        (
            "meta",
            f"{MODE_LABELS.get(mode, mode)} · status: "
            f"{payload.get('status') or 'unknown'}",
        )
    )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks.append(("meta", f"Exported {generated}"))

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
    blocks.append(("title", payload.get("filename") or "Study results"))
    blocks.append(
        (
            "meta",
            f"{MODE_LABELS.get(mode, mode)} · status: {payload.get('status') or 'unknown'}",
        )
    )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks.append(("meta", f"Exported {generated}"))

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
            blocks.append(("h2", _refs_label(item.get("source_refs") or [])))
            item_summary = item.get("summary")
            if item_summary:
                blocks.append(("body", item_summary))
    elif mode == "text_and_summaries":
        for item in items:
            blocks.append(("h2", _refs_label(item.get("source_refs") or [])))
            text = item.get("text")
            if text:
                blocks.append(("h3", "Source text"))
                blocks.append(("body", text))
            summary = item.get("summary")
            if summary:
                blocks.append(("h3", "AI summary"))
                blocks.append(("body", summary))
    else:  # extracted_text_only and any unknown mode: source text only
        for item in items:
            blocks.append(("h2", _refs_label(item.get("source_refs") or [])))
            text = item.get("text")
            if text:
                blocks.append(("body", text))

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

    def _text_width(self, text: str, size: float) -> float:
        key = (text, size)
        cached = self._width_cache.get(key)
        if cached is not None:
            return cached
        width = self._font(BODY_FONT).text_length(text, fontsize=size)
        if len(self._width_cache) < 50000:
            self._width_cache[key] = width
        return width

    def _line(self, text: str, size: float, font: str) -> None:
        leading = size * 1.45
        self._ensure(leading)
        assert self._shape is not None
        if text:
            self._register_font(font)
            self._shape.insert_text(
                (MARGIN, self._y + size),
                _sanitize(text),
                fontsize=size,
                fontname=_PDF_FONT_ALIASES[font],
            )
        self._y += leading

    def heading(self, text: str, size: float) -> None:
        self._y += 6
        self._line(text, size, BOLD_FONT)

    def paragraph(self, text: str, size: float = 11.0) -> None:
        width = PAGE_W - 2 * MARGIN
        font = self._font(BODY_FONT)
        space_width = self._text_width(" ", size)
        for raw in _sanitize(text).split("\n"):
            current: list[str] = []
            current_width = 0.0
            for word in raw.split(" "):
                if not word:
                    continue
                word_width = self._text_width(word, size)
                if word_width > width:
                    if current:
                        self._line(" ".join(current), size, BODY_FONT)
                        current = []
                        current_width = 0.0
                    pieces = _break_long_word(word, font, size, width)
                    for piece in pieces[:-1]:
                        self._line(piece, size, BODY_FONT)
                    current = [pieces[-1]]
                    current_width = self._text_width(pieces[-1], size)
                    continue
                if current and current_width + space_width + word_width > width:
                    self._line(" ".join(current), size, BODY_FONT)
                    current = [word]
                    current_width = word_width
                else:
                    if current:
                        current_width += space_width
                    current.append(word)
                    current_width += word_width
            self._line(" ".join(current), size, BODY_FONT)

    def spacer(self, height: float = 6.0) -> None:
        self._y += height

    def save(self) -> bytes:
        self._flush_shape()
        self._doc.subset_fonts()
        data = self._doc.tobytes()
        self._doc.close()
        return data


def render_pdf(payload: dict[str, Any]) -> bytes:
    writer = _PdfWriter()
    for kind, text in build_blocks(payload):
        if kind == "title":
            writer.heading(text, 20.0)
        elif kind == "meta":
            writer._line(text, 10.0, BODY_FONT)
        elif kind == "h2":
            writer.heading(text, 14.0)
        elif kind == "h3":
            writer.heading(text, 12.0)
        else:
            writer.paragraph(text)
    writer.spacer(10.0)
    return writer.save()


def render_docx(payload: dict[str, Any]) -> bytes:
    document = DocxDocument()
    for kind, text in build_blocks(payload):
        if kind == "title":
            document.add_heading(text, level=0)
        elif kind == "meta":
            paragraph = document.add_paragraph(text)
            paragraph.runs[0].font.size = Pt(10)
        elif kind == "h2":
            document.add_heading(text, level=2)
        elif kind == "h3":
            document.add_heading(text, level=3)
        else:
            document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


_MAX_EXPORT_STEM = 120


def _sanitize_part(value: object, default: str) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or ""))
    return text.strip("-._") or default


def export_filename(payload: dict[str, Any], fmt: str) -> str:
    stem = Path(payload.get("filename") or "study").stem
    safe = _sanitize_part(stem, "study")
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
