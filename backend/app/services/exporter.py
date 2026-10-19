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
BODY_FONT = "helv"
BOLD_FONT = "hebo"

MODE_LABELS = {
    "extracted_text_only": "Extracted text only",
    "text_and_summaries": "Text + AI summaries",
    "summaries_only": "AI summaries only",
}

# Block kinds understood by every renderer.
Block = tuple[str, str]


def _sanitize(text: str) -> str:
    return text.encode("latin-1", "replace").decode("latin-1")


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


def build_blocks(payload: dict[str, Any]) -> list[Block]:
    """Mode-aware content blocks derived only from the persisted job payload.

    ``summaries_only`` never includes ``item["text"]`` (which is ``None`` in that
    mode), so the full extracted source is never written to an export.
    """
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


class _PdfWriter:
    def __init__(self) -> None:
        self._doc = pymupdf.open()
        self._page: pymupdf.Page | None = None
        self._y = MARGIN
        self._new_page()

    def _new_page(self) -> None:
        self._page = self._doc.new_page(width=PAGE_W, height=PAGE_H)
        self._y = MARGIN

    def _ensure(self, height: float) -> None:
        if self._y + height > PAGE_H - MARGIN:
            self._new_page()

    def _line(self, text: str, size: float, font: str) -> None:
        leading = size * 1.45
        self._ensure(leading)
        assert self._page is not None
        if text:
            self._page.insert_text(
                (MARGIN, self._y + size),
                _sanitize(text),
                fontsize=size,
                fontname=font,
            )
        self._y += leading

    def heading(self, text: str, size: float) -> None:
        self._y += 6
        self._line(text, size, BOLD_FONT)

    def paragraph(self, text: str, size: float = 11.0) -> None:
        width = PAGE_W - 2 * MARGIN
        font = pymupdf.Font(BODY_FONT)
        for raw in _sanitize(text).split("\n"):
            words = raw.split(" ")
            current = ""
            for word in words:
                candidate = word if not current else f"{current} {word}"
                if font.text_length(candidate, fontsize=size) <= width:
                    current = candidate
                else:
                    self._line(current, size, BODY_FONT)
                    current = word
            self._line(current, size, BODY_FONT)

    def spacer(self, height: float = 6.0) -> None:
        self._y += height

    def save(self) -> bytes:
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


def export_filename(payload: dict[str, Any], fmt: str) -> str:
    stem = Path(payload.get("filename") or "study").stem or "study"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._") or "study"
    mode = re.sub(r"[^A-Za-z0-9._-]+", "-", str(payload.get("mode") or "study"))
    return f"{safe}-{mode}.{fmt}"


def render(payload: dict[str, Any], fmt: str) -> bytes:
    if fmt == "pdf":
        return render_pdf(payload)
    return render_docx(payload)


EXPORT_MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
