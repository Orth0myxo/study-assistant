from __future__ import annotations

import hashlib
import io
import re
import statistics
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

import pymupdf
from docx import Document as DocxDocument
from docx.oxml.ns import qn
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.parts.image import ImagePart
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.config import Settings
from app.errors import ApiError
from app.services.ocr import (
    OcrRunner,
    OcrTimeout,
    OcrUnavailable,
    run_tesseract,
)

PDF_MAGIC = b"%PDF-"
PDF_HEADER_SEARCH_BYTES = 1024
ZIP_MAGIC = b"PK\x03\x04"
ALLOWED_EXTENSIONS = {".pdf": "pdf", ".docx": "docx"}
BLIP_TAG = qn("a:blip")
EMBED_ATTR = qn("r:embed")
_PDF_IMAGE_OCR_MAX_NATIVE_CHARS = 120
_PDF_IMAGE_OCR_MIN_COVERAGE = 0.20


@dataclass
class Block:
    """Best-effort presentation metadata for a span or paragraph.

    ``start`` and ``end`` are offsets into the owning unit's exact text. The
    text itself is never changed; structure is metadata only.
    """

    index: int
    role: str
    text: str
    start: int
    end: int
    bold: bool = False
    alignment: str | None = None
    size: float | None = None


@dataclass
class Section:
    index: int
    anchor: dict[str, Any]
    raw_text: str
    normalized_text: str
    has_page_image: bool = False


@dataclass
class Page:
    index: int
    page_number: int | None
    kind: str
    text: str
    source: str
    structure: list[Block] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    native_char_count: int = 0
    image_coverage: float = 0.0
    needs_ocr: bool = False
    ocr_status: str = "none"
    ocr_attempts: int = 0
    ocr_empty: bool = False
    has_page_image: bool = False
    tables_checked: bool = True


@dataclass
class ParsedDocument:
    format: str
    page_count: int | None
    sections: list[Section]
    pages: list[Page] = field(default_factory=list)
    warnings: list[dict[str, str]] = field(default_factory=list)
    extraction_status: str = "ok"
    text_extractable: bool = True
    total_chars: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)


ProgressCallback = Callable[[int, int, str], None]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _pdf_structure(page: pymupdf.Page, raw_text: str) -> list[Block]:
    """Derive bold/size hints from PDF spans without altering page text."""
    try:
        payload = page.get_text("dict")
    except Exception:
        return []
    spans: list[tuple[str, float, bool]] = []
    for block in payload.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                text = span.get("text") or ""
                if not text.strip():
                    continue
                size = float(span.get("size") or 0.0)
                flags = int(span.get("flags") or 0)
                spans.append((text, size, bool(flags & 16)))
    sizes = [size for _, size, _ in spans if size > 0]
    median = statistics.median(sizes) if sizes else 0.0
    blocks: list[Block] = []
    cursor = 0
    for text, size, bold in spans:
        start = raw_text.find(text, cursor)
        if start < 0:
            start = raw_text.find(text)
        if start < 0:
            continue
        end = start + len(text)
        cursor = end
        role = "normal"
        if median and size >= median * 1.6:
            role = "title"
        elif bold and size >= median * 1.05:
            role = "heading"
        elif median and size >= median * 1.25:
            role = "heading"
        blocks.append(
            Block(
                index=len(blocks),
                role=role,
                text=text,
                start=start,
                end=end,
                bold=bold,
                size=round(size, 2) if size else None,
            )
        )
    return blocks


def _pdf_tables(page: pymupdf.Page, raw_text: str) -> list[dict[str, Any]]:
    """Extract table cell text as separate structure without changing page text.

    PyMuPDF versions or damaged layouts that cannot detect tables fall back to
    the existing page-text layout. Cell strings are kept exactly as returned.
    """
    find_tables = getattr(page, "find_tables", None)
    if not callable(find_tables):
        return []
    try:
        found = find_tables()
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for table in getattr(found, "tables", []) or []:
        try:
            raw_rows = table.extract()
            if not raw_rows:
                continue
            rows = [
                [cell if isinstance(cell, str) else "" for cell in row]
                for row in raw_rows
            ]
            column_count = max((len(row) for row in rows), default=0)
            if len(rows) < 2 or column_count < 2:
                continue
            bbox = [round(float(value), 2) for value in table.bbox]
            first_cell = next((cell for row in rows for cell in row if cell), "")
            first_cell_start = raw_text.find(first_cell) if first_cell else -1
            cell_starts: list[int] = []
            cell_ends: list[int] = []
            cursor = 0
            for row in rows:
                for cell in row:
                    if not cell:
                        continue
                    start = raw_text.find(cell, cursor)
                    if start < 0:
                        cell_starts = []
                        cell_ends = []
                        break
                    cell_starts.append(start)
                    cell_ends.append(start + len(cell))
                    cursor = start + len(cell)
                if not cell_starts:
                    break
            results.append(
                {
                    "index": len(results),
                    "bbox": bbox,
                    "top_ratio": round(bbox[1] / max(float(page.rect.height), 1), 4),
                    "source_start": (
                        min(cell_starts)
                        if cell_starts
                        else first_cell_start if first_cell_start >= 0 else None
                    ),
                    "source_end": max(cell_ends) if cell_ends else None,
                    "rows": rows,
                    "row_count": len(rows),
                    "column_count": column_count,
                    "header_row_indices": [],
                }
            )
        except Exception:
            continue
    return results


def detect_pdf_page_tables(
    document: pymupdf.Document,
    pages: list[dict[str, Any]],
    page_indexes: set[int] | None = None,
) -> int:
    """Detect table rows for selected pages and mark only those pages checked."""
    detected = 0
    for page in pages:
        if page.get("kind") != "pdf_page" or page.get("tables_checked"):
            continue
        index = int(page.get("index", -1))
        if index < 0 or (page_indexes is not None and index not in page_indexes):
            continue
        source = document.load_page(index)
        page["tables"] = _pdf_tables(source, str(page.get("text") or ""))
        page["tables_checked"] = True
        detected += 1
    _mark_repeated_table_headers(
        {
            int(page.get("index", -1)): page.get("tables") or []
            for page in pages
        }
    )
    return detected


def detect_stored_pdf_page_tables(
    path: Path,
    pages: list[dict[str, Any]],
    page_indexes: set[int] | None = None,
) -> int:
    """Open a stored PDF and detect tables only for the requested pages."""
    with pymupdf.open(path) as document:
        return detect_pdf_page_tables(document, pages, page_indexes)


def _mark_repeated_table_headers(
    pages: dict[int, list[dict[str, Any]]]
) -> None:
    """Mark repeated leading rows by adjacent table shape and row position."""
    for page_index in range(max(len(pages) - 1, 0)):
        left_tables = pages.get(page_index, [])
        right_tables = pages.get(page_index + 1, [])
        for left in left_tables:
            for right in right_tables:
                if (
                    left.get("column_count") == right.get("column_count")
                    and left.get("rows")
                    and right.get("rows")
                    and left["rows"][0] == right["rows"][0]
                ):
                    left["header_row_indices"] = [0]
                    right["header_row_indices"] = [0]


def validate_document_limits(
    filename: str | None, data: bytes, settings: Settings
) -> str:
    """Reject configured limits before accepting asynchronous ingestion."""
    fmt = _detect_format(filename, data)
    if fmt == "pdf":
        try:
            document = pymupdf.open(stream=data, filetype="pdf")
        except Exception as exc:
            raise ApiError("corrupt_document", "The PDF file could not be opened.", 400) from exc
        try:
            page_count = document.page_count
        finally:
            document.close()
        if page_count > settings.max_pages:
            raise ApiError(
                "document_too_long",
                f"This PDF has {page_count} pages. The limit is "
                f"{settings.max_pages} pages.",
                413,
                {"page_count": page_count, "max_pages": settings.max_pages},
            )
        return fmt

    _check_docx_archive(data, settings)
    try:
        document = DocxDocument(io.BytesIO(data))
        blocks = list(_iter_blocks(document))
        word_count = sum(
            len(re.findall(r"\b\w+\b", _block_text(block))) for block in blocks
        )
    except Exception as exc:
        raise ApiError("corrupt_document", "The DOCX file could not be read.", 400) from exc
    if word_count > settings.max_docx_words:
        raise ApiError(
            "document_too_long",
            f"This DOCX has about {word_count} words. The limit is "
            f"{settings.max_docx_words} words.",
            413,
            {"word_count": word_count, "max_words": settings.max_docx_words},
        )
    return fmt


def _format_pages(pages: list[int], max_items: int = 6) -> str:
    shown = ", ".join(str(page) for page in pages[:max_items])
    if len(pages) > max_items:
        shown += f", +{len(pages) - max_items} more"
    return shown


def _detect_format(filename: str | None, data: bytes) -> str:
    name = filename or "upload"
    suffix = Path(name).suffix.lower()
    if suffix == ".doc":
        raise ApiError(
            "unsupported_file_type",
            "Legacy .doc files are not supported. Please save the file as .docx.",
            415,
            {"received": suffix},
        )
    if suffix not in ALLOWED_EXTENSIONS:
        raise ApiError(
            "unsupported_file_type",
            "Only PDF and DOCX files are supported.",
            415,
            {"received": name},
        )
    if len(data) == 0:
        raise ApiError("empty_file", "The uploaded file is empty.", 400)
    if suffix == ".pdf" and PDF_MAGIC not in data[:PDF_HEADER_SEARCH_BYTES]:
        raise ApiError("corrupt_document", "The PDF file could not be read.", 400)
    if suffix == ".docx" and not data.startswith(ZIP_MAGIC):
        raise ApiError("corrupt_document", "The DOCX file could not be read.", 400)
    return ALLOWED_EXTENSIONS[suffix]


def parse_document(
    filename: str | None,
    data: bytes,
    settings: Settings,
    ocr_runner: OcrRunner | None = None,
    progress: ProgressCallback | None = None,
) -> ParsedDocument:
    started = time.perf_counter()
    fmt = _detect_format(filename, data)
    parsed = (
        _parse_pdf(data, settings, ocr_runner, progress)
        if fmt == "pdf"
        else _parse_docx(data, settings, ocr_runner, progress)
    )
    parsed.diagnostics["size_bytes"] = len(data)
    parsed.diagnostics["parse_duration_ms"] = round(
        (time.perf_counter() - started) * 1000, 1
    )
    return parsed


def _rect_union_coverage(rects: list[pymupdf.Rect], page_rect: pymupdf.Rect) -> float:
    """Estimate the fraction of a page covered by the union of rectangles."""
    page_area = page_rect.width * page_rect.height
    if page_area <= 0 or not rects:
        return 0.0
    x_edges = sorted({edge for rect in rects for edge in (rect.x0, rect.x1)})
    covered_area = 0.0
    for left, right in zip(x_edges, x_edges[1:]):
        if right <= left:
            continue
        middle = (left + right) / 2
        intervals = sorted(
            (rect.y0, rect.y1)
            for rect in rects
            if rect.x0 < middle < rect.x1
        )
        covered_height = 0.0
        if intervals:
            start, end = intervals[0]
            for next_start, next_end in intervals[1:]:
                if next_start > end:
                    covered_height += end - start
                    start, end = next_start, next_end
                else:
                    end = max(end, next_end)
            covered_height += end - start
        covered_area += (right - left) * covered_height
    return min(covered_area / page_area, 1.0)


def _page_image_coverage(page: pymupdf.Page) -> float:
    """Estimate the fraction of a PDF page covered by visible image blocks.

    The rectangle union avoids counting overlapping image layers more than
    once. Fail closed when image metadata is unavailable: ordinary native-text
    extraction should not fail because a PDF has unusual image metadata.
    """
    try:
        page_rect = page.rect
        rects: list[pymupdf.Rect] = []
        for info in page.get_image_info():
            bbox = info.get("bbox")
            if not bbox:
                continue
            rect = pymupdf.Rect(bbox) & page_rect
            if not rect.is_empty and rect.width > 0 and rect.height > 0:
                rects.append(rect)
        return _rect_union_coverage(rects, page_rect)
    except Exception:
        return 0.0


def _page_drawing_coverage(page: pymupdf.Page) -> float:
    """Estimate the fraction of a page covered by vector drawing paths.

    Some slide decks render their body as vector paths rather than a raster
    image, so image metadata alone cannot tell that the page still needs OCR.
    """
    try:
        page_rect = page.rect
        rects: list[pymupdf.Rect] = []
        for drawing in page.get_drawings():
            raw_rect = drawing.get("rect")
            if raw_rect is None:
                continue
            rect = pymupdf.Rect(raw_rect) & page_rect
            if not rect.is_empty and rect.width > 0 and rect.height > 0:
                rects.append(rect)
        return _rect_union_coverage(rects, page_rect)
    except Exception:
        return 0.0


def _parse_pdf(
    data: bytes,
    settings: Settings,
    ocr_runner: OcrRunner | None = None,
    progress: ProgressCallback | None = None,
) -> ParsedDocument:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ApiError("corrupt_document", "The PDF file could not be read.", 400) from exc

    try:
        if document.needs_pass or document.is_encrypted:
            raise ApiError(
                "encrypted_document",
                "This PDF is password-protected or encrypted and cannot be read.",
                400,
            )

        repaired = bool(getattr(document, "is_repaired", False))
        page_count = document.page_count
        if page_count > settings.max_pages:
            raise ApiError(
                "document_too_long",
                f"This PDF has {page_count} pages. The limit is "
                f"{settings.max_pages} pages.",
                413,
                {"page_count": page_count, "max_pages": settings.max_pages},
            )
        page_texts: list[str] = []
        page_structures: dict[int, list[Block]] = {}
        page_metrics: list[tuple[int, float, bool, bool]] = []
        native_text_pages = 0
        image_ocr_candidates = 0
        graphic_ocr_candidates = 0
        vector_ocr_candidates = 0

        for page_index in range(page_count):
            page = document.load_page(page_index)
            raw_text = (page.get_text("text") or "").replace("\r\n", "\n").replace("\r", "\n")
            page_texts.append(raw_text)
            page_structures[page_index] = _pdf_structure(page, raw_text)

            compact_native = _compact_text(raw_text)
            native_chars = len(compact_native)
            image_coverage = _page_image_coverage(page)
            has_page_image = image_coverage >= _PDF_IMAGE_OCR_MIN_COVERAGE
            image_content_candidate = (
                settings.min_pdf_chars_per_page <= native_chars
                < _PDF_IMAGE_OCR_MAX_NATIVE_CHARS
                and has_page_image
            )
            if image_content_candidate:
                image_ocr_candidates += 1
            graphic_coverage = image_coverage
            if (
                settings.min_pdf_chars_per_page <= native_chars
                < _PDF_IMAGE_OCR_MAX_NATIVE_CHARS
                and not has_page_image
            ):
                graphic_coverage = max(image_coverage, _page_drawing_coverage(page))
            graphic_content_candidate = (
                settings.min_pdf_chars_per_page <= native_chars
                < _PDF_IMAGE_OCR_MAX_NATIVE_CHARS
                and graphic_coverage >= _PDF_IMAGE_OCR_MIN_COVERAGE
            )
            if graphic_content_candidate:
                graphic_ocr_candidates += 1
                if not has_page_image:
                    vector_ocr_candidates += 1
            needs_ocr = (
                native_chars < settings.min_pdf_chars_per_page
                or graphic_content_candidate
            )
            page_metrics.append((native_chars, image_coverage, needs_ocr, has_page_image))
            if native_chars >= settings.min_pdf_chars_per_page:
                native_text_pages += 1
            if progress is not None:
                progress(page_index + 1, page_count, "page")
    finally:
        document.close()

    sections: list[Section] = []
    pages: list[Page] = []
    total_chars = 0
    text_pages = 0
    pending_pages: list[int] = []

    for page_index, native_text in enumerate(page_texts):
        native_chars, image_coverage, needs_ocr, has_page_image = page_metrics[page_index]
        compact = _compact_text(native_text)
        source = "native" if native_chars >= settings.min_pdf_chars_per_page else "none"
        if needs_ocr:
            pending_pages.append(page_index + 1)
        pages.append(
            Page(
                index=page_index,
                page_number=page_index + 1,
                kind="pdf_page",
                text=native_text,
                source=source,
                structure=page_structures.get(page_index, []),
                native_char_count=native_chars,
                image_coverage=round(image_coverage, 4),
                needs_ocr=needs_ocr,
                ocr_status="pending" if needs_ocr else "none",
                has_page_image=has_page_image,
                tables_checked=False,
            )
        )
        if len(compact) < settings.min_pdf_chars_per_page:
            continue
        text_pages += 1
        total_chars += len(compact)
        sections.append(
            Section(
                index=len(sections),
                anchor={
                    "kind": "pdf_page",
                    "page": page_index + 1,
                    "page_index": page_index,
                },
                raw_text=native_text,
                normalized_text=normalize_text(native_text),
                has_page_image=has_page_image,
            )
        )

    text_extractable = text_pages > 0
    pages_without_text = page_count - text_pages

    warnings: list[dict[str, str]] = []
    if repaired:
        status = "partial"
        warnings.append(
            {
                "code": "document_repaired",
                "message": (
                    "This PDF was damaged or incomplete and was partially reconstructed; "
                    "some text may be missing."
                ),
            }
        )
    elif not text_extractable and pending_pages:
        status = "partial"
        warnings.append(
            {
                "code": "ocr_deferred",
                "message": (
                    "Text OCR is deferred until a request needs these pages: "
                    f"{_format_pages(pending_pages)}."
                ),
            }
        )
    elif not text_extractable:
        status = "no_text"
        warnings.append(
            {
                "code": "scanned_pdf_no_text",
                "message": (
                    "This PDF appears to be scanned; no usable text could be extracted."
                ),
            }
        )
    elif pages_without_text > 0:
        status = "partial"
        warnings.append(
            {
                "code": "partial_extraction",
                "message": (
                    f"{pages_without_text} of {page_count} pages contained no or too "
                    "little extractable text."
                ),
            }
        )
    else:
        status = "ok"

    if text_extractable and pending_pages:
        warnings.append(
            {
                "code": "ocr_deferred",
                "message": (
                    "Image-heavy or scanned pages will be read when a request needs them: "
                    f"{_format_pages(pending_pages)}."
                ),
            }
        )

    return ParsedDocument(
        format="pdf",
        page_count=page_count,
        sections=sections,
        pages=pages,
        warnings=warnings,
        extraction_status=status,
        text_extractable=text_extractable,
        total_chars=total_chars,
        diagnostics={
            "format": "pdf",
            "page_count": page_count,
            "native_text_pages": native_text_pages,
            "ocr_candidates": len(pending_pages),
            "image_ocr_candidates": image_ocr_candidates,
            "graphic_ocr_candidates": graphic_ocr_candidates,
            "vector_ocr_candidates": vector_ocr_candidates,
            "ocr_attempts": 0,
            "ocr_succeeded_pages": 0,
            "ocr_pending_pages": pending_pages,
            "ocr_duration_ms": 0.0,
            "table_ms": 0.0,
            "tables_found": 0,
            "section_count": len(sections),
            "total_chars": total_chars,
        },
    )


def _iter_blocks(document: DocxDocument) -> Iterator[Paragraph | Table]:
    for child in document.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, document)
        elif isinstance(child, CT_Tbl):
            yield Table(child, document)


def _check_docx_archive(data: bytes, settings: Settings) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            uncompressed_bytes = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ApiError("corrupt_document", "The DOCX file could not be read.", 400) from exc

    limit_bytes = settings.max_docx_uncompressed_mb * 1024 * 1024
    if uncompressed_bytes > limit_bytes:
        raise ApiError(
            "file_too_large",
            (
                "The document expands to more than "
                f"{settings.max_docx_uncompressed_mb} MB when opened."
            ),
            413,
            {
                "limit_mb": settings.max_docx_uncompressed_mb,
                "uncompressed_mb": round(uncompressed_bytes / (1024 * 1024), 1),
            },
        )


def _iter_block_images(
    block: Paragraph | Table, document: DocxDocument
) -> Iterator[tuple[bytes, str]]:
    for blip in block._element.iter(BLIP_TAG):
        relationship_id = blip.get(EMBED_ATTR)
        if not relationship_id:
            continue
        try:
            part = document.part.related_parts[relationship_id]
        except KeyError:
            continue
        if not isinstance(part, ImagePart):
            continue
        suffix = Path(str(part.partname)).suffix.lower() or ".png"
        yield part.blob, suffix


def _docx_structure(block: Paragraph | Table, raw_text: str) -> list[Block]:
    if not raw_text.strip():
        return []
    if isinstance(block, Paragraph):
        style_name = ((block.style.name if block.style is not None else "") or "").lower()
        if style_name == "title":
            role = "title"
        elif style_name.startswith("heading") or style_name.startswith("subtitle"):
            role = "heading"
        elif "list" in style_name or "bullet" in style_name:
            role = "bullet"
        else:
            role = "normal"
        bold = any(run.bold for run in block.runs if run.bold is not None)
        size = None
        for run in block.runs:
            if run.font.size is not None:
                size = run.font.size.pt
                break
        if size is None and block.style is not None and block.style.font.size is not None:
            size = block.style.font.size.pt
        alignment = (
            str(block.alignment).lower() if block.alignment is not None else None
        )
        return [
            Block(
                index=0,
                role=role,
                text=raw_text,
                start=0,
                end=len(raw_text),
                bold=bold,
                alignment=alignment,
                size=round(size, 2) if size is not None else None,
            )
        ]
    return [
        Block(
            index=0,
            role="normal",
            text=raw_text,
            start=0,
            end=len(raw_text),
            bold=False,
            size=None,
        )
    ]


def _docx_table_rows(block: Table) -> list[list[str]]:
    return [
        [cell.text or "" for cell in row.cells]
        for row in block.rows
    ]


def _block_text(block: Paragraph | Table) -> str:
    if isinstance(block, Paragraph):
        return block.text or ""
    rows = [" | ".join(cell.text or "" for cell in row.cells) for row in block.rows]
    return "\n".join(rows)


def _parse_docx(
    data: bytes,
    settings: Settings,
    ocr_runner: OcrRunner | None = None,
    progress: ProgressCallback | None = None,
) -> ParsedDocument:
    _check_docx_archive(data, settings)

    try:
        document = DocxDocument(io.BytesIO(data))
    except Exception as exc:
        raise ApiError("corrupt_document", "The DOCX file could not be read.", 400) from exc

    ocr_enabled = settings.ocr_page_limit > 0
    runner = ocr_runner or run_tesseract

    try:
        blocks = list(_iter_blocks(document))
    except Exception as exc:
        raise ApiError("corrupt_document", "The DOCX file could not be read.", 400) from exc
    word_count = sum(len(re.findall(r"\b\w+\b", _block_text(block))) for block in blocks)
    if word_count > settings.max_docx_words:
        raise ApiError(
            "document_too_long",
            f"This DOCX has about {word_count} words. The limit is "
            f"{settings.max_docx_words} words.",
            413,
            {"word_count": word_count, "max_words": settings.max_docx_words},
        )

    sections: list[Section] = []
    pages: list[Page] = []
    total_chars = 0
    current_section: str | None = None
    block_index = 0
    ocr_attempts = 0
    ocr_remaining = 0
    ocr_unavailable = False
    ocr_limit_reached = False
    ocr_timeout_images = 0
    ocr_failed_images = 0
    ocr_succeeded_images = 0
    ocr_duration_ms = 0.0
    seen_images: set[bytes] = set()

    try:
        for block in blocks:
            if isinstance(block, Paragraph):
                text = block.text or ""
                style_name = (block.style.name if block.style is not None else "") or ""
                if text.strip() and style_name.lower().startswith("heading"):
                    current_section = text.strip()
                raw_text = text
            else:
                raw_text = _block_text(block)

            if raw_text.strip():
                total_chars += len(_compact_text(raw_text))
                sections.append(
                    Section(
                        index=len(sections),
                        anchor={
                            "kind": "docx_section",
                            "section_label": current_section,
                            "paragraph_index": block_index,
                        },
                        raw_text=raw_text,
                        normalized_text=normalize_text(raw_text),
                    )
                )
                pages.append(
                    Page(
                        index=len(pages),
                        page_number=None,
                        kind="docx_block",
                        text=raw_text,
                        source="native",
                        structure=_docx_structure(block, raw_text),
                        tables=(
                            [{"rows": _docx_table_rows(block)}]
                            if isinstance(block, Table)
                            else []
                        ),
                    )
                )

            if ocr_enabled:
                for image_bytes, suffix in _iter_block_images(block, document):
                    digest = hashlib.sha256(image_bytes).digest()
                    if digest in seen_images:
                        continue
                    seen_images.add(digest)

                    if ocr_unavailable:
                        ocr_remaining += 1
                        continue
                    if ocr_attempts >= settings.ocr_page_limit:
                        ocr_limit_reached = True
                        ocr_remaining += 1
                        continue

                    ocr_attempts += 1
                    ocr_started = time.perf_counter()
                    try:
                        ocr_text = runner(image_bytes, suffix, settings)
                    except OcrUnavailable:
                        ocr_unavailable = True
                        ocr_remaining += 1
                        continue
                    except OcrTimeout:
                        ocr_timeout_images += 1
                        continue
                    except Exception:
                        ocr_failed_images += 1
                        continue
                    finally:
                        ocr_duration_ms += (time.perf_counter() - ocr_started) * 1000

                    compact = _compact_text(ocr_text)
                    if not compact:
                        continue
                    ocr_succeeded_images += 1
                    total_chars += len(compact)
                    sections.append(
                        Section(
                            index=len(sections),
                            anchor={
                                "kind": "docx_section",
                                "section_label": current_section,
                                "paragraph_index": block_index,
                            },
                            raw_text=ocr_text,
                            normalized_text=normalize_text(ocr_text),
                        )
                    )
                    pages.append(
                        Page(
                            index=len(pages),
                            page_number=None,
                            kind="docx_block",
                            text=ocr_text,
                            source="ocr",
                        )
                    )

            block_index += 1
            if progress is not None:
                progress(block_index, len(blocks), "paragraph")
    except Exception as exc:
        raise ApiError("corrupt_document", "The DOCX file could not be read.", 400) from exc

    text_extractable = total_chars > 0
    ocr_issues = (
        ocr_unavailable
        or ocr_limit_reached
        or ocr_timeout_images > 0
        or ocr_failed_images > 0
    )

    warnings: list[dict[str, str]] = []
    if not text_extractable:
        status = "no_text"
        warnings.append(
            {
                "code": "no_extractable_text",
                "message": "No extractable text was found in this document.",
            }
        )
    elif ocr_issues:
        status = "partial"
    else:
        status = "ok"

    if ocr_unavailable:
        warnings.append(
            {
                "code": "ocr_unavailable",
                "message": (
                    "OCR is not available on the server; "
                    f"{ocr_remaining} embedded image(s) could not be read."
                ),
            }
        )
    elif ocr_limit_reached:
        warnings.append(
            {
                "code": "ocr_image_limit_reached",
                "message": (
                    f"OCR is limited to {settings.ocr_page_limit} image(s) per document; "
                    f"{ocr_remaining} image(s) were not processed."
                ),
            }
        )

    if ocr_timeout_images:
        warnings.append(
            {
                "code": "ocr_timeout",
                "message": f"OCR timed out on {ocr_timeout_images} embedded image(s).",
            }
        )

    if ocr_failed_images:
        warnings.append(
            {
                "code": "ocr_failed",
                "message": f"OCR failed on {ocr_failed_images} embedded image(s).",
            }
        )

    return ParsedDocument(
        format="docx",
        page_count=None,
        sections=sections,
        pages=pages,
        warnings=warnings,
        extraction_status=status,
        text_extractable=text_extractable,
        total_chars=total_chars,
        diagnostics={
            "format": "docx",
            "page_count": None,
            "images_seen": len(seen_images),
            "word_count": word_count,
            "ocr_attempts": ocr_attempts,
            "ocr_succeeded_images": ocr_succeeded_images,
            "ocr_timeout_images": ocr_timeout_images,
            "ocr_failed_images": ocr_failed_images,
            "ocr_skipped_images": ocr_remaining,
            "ocr_duration_ms": round(ocr_duration_ms, 1),
            "section_count": len(sections),
            "total_chars": total_chars,
        },
    )
