from __future__ import annotations

import hashlib
import io
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

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


@dataclass
class Section:
    index: int
    anchor: dict[str, Any]
    raw_text: str
    normalized_text: str


@dataclass
class ParsedDocument:
    format: str
    page_count: int | None
    sections: list[Section]
    warnings: list[dict[str, str]] = field(default_factory=list)
    extraction_status: str = "ok"
    text_extractable: bool = True
    total_chars: int = 0
    diagnostics: dict[str, Any] = field(default_factory=dict)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


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
) -> ParsedDocument:
    started = time.perf_counter()
    fmt = _detect_format(filename, data)
    parsed = (
        _parse_pdf(data, settings, ocr_runner)
        if fmt == "pdf"
        else _parse_docx(data, settings, ocr_runner)
    )
    parsed.diagnostics["size_bytes"] = len(data)
    parsed.diagnostics["parse_duration_ms"] = round(
        (time.perf_counter() - started) * 1000, 1
    )
    return parsed


def _parse_pdf(
    data: bytes,
    settings: Settings,
    ocr_runner: OcrRunner | None = None,
) -> ParsedDocument:
    try:
        document = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:
        raise ApiError("corrupt_document", "The PDF file could not be read.", 400) from exc

    ocr_enabled = settings.ocr_page_limit > 0
    runner = ocr_runner or run_tesseract

    try:
        if document.needs_pass or document.is_encrypted:
            raise ApiError(
                "encrypted_document",
                "This PDF is password-protected or encrypted and cannot be read.",
                400,
            )

        repaired = bool(getattr(document, "is_repaired", False))
        page_count = document.page_count
        page_texts: list[str] = []
        ocr_text_by_page: dict[int, str] = {}
        ocr_timeout_pages: list[int] = []
        ocr_failed_pages: list[int] = []
        ocr_empty_pages = 0
        ocr_attempts = 0
        ocr_candidates = 0
        ocr_remaining = 0
        native_text_pages = 0
        ocr_duration_ms = 0.0
        ocr_unavailable = False
        ocr_limit_reached = False

        for page_index in range(page_count):
            page = document.load_page(page_index)
            raw_text = (page.get_text("text") or "").replace("\r\n", "\n").replace("\r", "\n")
            page_texts.append(raw_text)

            compact_native = _compact_text(raw_text)
            if len(compact_native) >= settings.min_pdf_chars_per_page:
                native_text_pages += 1
                continue
            if not ocr_enabled:
                continue
            ocr_candidates += 1
            if ocr_unavailable:
                ocr_remaining += 1
                continue
            if ocr_attempts >= settings.ocr_page_limit:
                ocr_limit_reached = True
                ocr_remaining += 1
                continue

            ocr_attempts += 1
            try:
                pixmap = page.get_pixmap(
                    dpi=settings.ocr_dpi,
                    colorspace=pymupdf.csGRAY,
                    alpha=False,
                )
                image_bytes = pixmap.tobytes("png")
            except Exception:
                ocr_failed_pages.append(page_index + 1)
                continue

            ocr_started = time.perf_counter()
            try:
                ocr_text = runner(image_bytes, ".png", settings)
            except OcrUnavailable:
                ocr_unavailable = True
                ocr_remaining += 1
                continue
            except OcrTimeout:
                ocr_timeout_pages.append(page_index + 1)
                continue
            except Exception:
                ocr_failed_pages.append(page_index + 1)
                continue
            finally:
                ocr_duration_ms += (time.perf_counter() - ocr_started) * 1000

            if len(_compact_text(ocr_text)) < settings.min_pdf_chars_per_page:
                ocr_empty_pages += 1
                continue
            ocr_text_by_page[page_index] = ocr_text
    finally:
        document.close()

    sections: list[Section] = []
    total_chars = 0
    text_pages = 0

    for page_index, native_text in enumerate(page_texts):
        raw_text = ocr_text_by_page.get(page_index, native_text)
        compact = _compact_text(raw_text)
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
                raw_text=raw_text,
                normalized_text=normalize_text(raw_text),
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

    if ocr_unavailable:
        warnings.append(
            {
                "code": "ocr_unavailable",
                "message": (
                    "OCR is not available on the server; "
                    f"{ocr_remaining} scanned page(s) could not be read."
                ),
            }
        )
    elif ocr_limit_reached:
        warnings.append(
            {
                "code": "ocr_page_limit_reached",
                "message": (
                    f"OCR is limited to {settings.ocr_page_limit} page(s) per document; "
                    f"{ocr_remaining} page(s) were not processed."
                ),
            }
        )

    if ocr_timeout_pages:
        warnings.append(
            {
                "code": "ocr_timeout",
                "message": (
                    f"OCR timed out on {len(ocr_timeout_pages)} page(s): "
                    f"{_format_pages(ocr_timeout_pages)}."
                ),
            }
        )

    if ocr_failed_pages:
        warnings.append(
            {
                "code": "ocr_failed",
                "message": (
                    f"OCR failed on {len(ocr_failed_pages)} page(s): "
                    f"{_format_pages(ocr_failed_pages)}."
                ),
            }
        )

    if ocr_empty_pages:
        warnings.append(
            {
                "code": "ocr_no_text",
                "message": f"OCR found no usable text on {ocr_empty_pages} page(s).",
            }
        )

    return ParsedDocument(
        format="pdf",
        page_count=page_count,
        sections=sections,
        warnings=warnings,
        extraction_status=status,
        text_extractable=text_extractable,
        total_chars=total_chars,
        diagnostics={
            "format": "pdf",
            "page_count": page_count,
            "native_text_pages": native_text_pages,
            "ocr_candidates": ocr_candidates,
            "ocr_attempts": ocr_attempts,
            "ocr_succeeded_pages": len(ocr_text_by_page),
            "ocr_empty_pages": ocr_empty_pages,
            "ocr_timeout_pages": len(ocr_timeout_pages),
            "ocr_failed_pages": len(ocr_failed_pages),
            "ocr_skipped_pages": ocr_remaining,
            "ocr_duration_ms": round(ocr_duration_ms, 1),
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


def _parse_docx(
    data: bytes,
    settings: Settings,
    ocr_runner: OcrRunner | None = None,
) -> ParsedDocument:
    _check_docx_archive(data, settings)

    try:
        document = DocxDocument(io.BytesIO(data))
    except Exception as exc:
        raise ApiError("corrupt_document", "The DOCX file could not be read.", 400) from exc

    ocr_enabled = settings.ocr_page_limit > 0
    runner = ocr_runner or run_tesseract

    sections: list[Section] = []
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
        for block in _iter_blocks(document):
            if isinstance(block, Paragraph):
                text = block.text or ""
                style_name = (block.style.name if block.style is not None else "") or ""
                if text.strip() and style_name.lower().startswith("heading"):
                    current_section = text.strip()
                raw_text = text
            else:
                rows = [
                    " | ".join(cell.text or "" for cell in row.cells)
                    for row in block.rows
                ]
                raw_text = "\n".join(rows)

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

            block_index += 1
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
        warnings=warnings,
        extraction_status=status,
        text_extractable=text_extractable,
        total_chars=total_chars,
        diagnostics={
            "format": "docx",
            "page_count": None,
            "images_seen": len(seen_images),
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
