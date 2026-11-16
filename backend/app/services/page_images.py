"""Bounded, cached rendering of source PDF pages for routes and exports."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import pymupdf

from app.errors import ApiError

logger = logging.getLogger(__name__)

PAGE_IMAGE_MAX_BYTES = 12 * 1024 * 1024
PAGE_IMAGE_MAX_PIXELS = 16_000_000
EXPORT_PAGE_IMAGE_DPI = 110
EXPORT_PAGE_IMAGE_QUALITY = 70
_EXPORT_FALLBACK_DPI = 90
_PAGE_IMAGE_LOCK = threading.Lock()


def render_page_image(
    *, document_id: str, source_path: str | Path, cache_root: str | Path, page_number: int
) -> Path:
    """Return the retained PNG for a page, rendering it once when uncached.

    The caller owns document/page validation. Limits, cache path, and API error
    classifications match the original page-image route implementation.
    """
    image_path = Path(cache_root) / f"{document_id}.page-{page_number}.png"
    with _PAGE_IMAGE_LOCK:
        image_path.parent.mkdir(parents=True, exist_ok=True)
        if image_path.exists() and image_path.stat().st_size <= PAGE_IMAGE_MAX_BYTES:
            return image_path
        image_path.unlink(missing_ok=True)
        try:
            pdf = pymupdf.open(source_path)
            try:
                page = pdf.load_page(page_number - 1)
                width_points = float(page.rect.width)
                height_points = float(page.rect.height)
                scale = 2.0  # 144 dpi
                pixels = width_points * scale * height_points * scale
                if pixels > PAGE_IMAGE_MAX_PIXELS:
                    scale = (
                        PAGE_IMAGE_MAX_PIXELS
                        / max(width_points * height_points, 1)
                    ) ** 0.5
                if scale < (120 / 72):
                    raise ApiError(
                        "page_image_too_large",
                        "This page is too large to render within the image size limit.",
                        413,
                    )
                pixmap = page.get_pixmap(
                    matrix=pymupdf.Matrix(scale, scale),
                    colorspace=pymupdf.csRGB,
                    alpha=False,
                )
                image = pixmap.tobytes("png")
                if len(image) > PAGE_IMAGE_MAX_BYTES:
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(120 / 72, 120 / 72),
                        colorspace=pymupdf.csRGB,
                        alpha=False,
                    )
                    image = pixmap.tobytes("png")
                if len(image) > PAGE_IMAGE_MAX_BYTES:
                    raise ApiError(
                        "page_image_too_large",
                        "This page image exceeds the response size limit.",
                        413,
                    )
            finally:
                pdf.close()
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - preserve route classification
            logger.info(
                "page image render failed document_id=%s page=%s",
                document_id,
                page_number,
            )
            raise ApiError(
                "page_not_found",
                "The requested PDF page could not be rendered.",
                404,
            ) from exc

        temporary_path = image_path.with_suffix(".png.tmp")
        temporary_path.write_bytes(image)
        temporary_path.replace(image_path)
    return image_path


def render_export_page_image(
    *, document_id: str, source_path: str | Path, cache_root: str | Path, page_number: int
) -> Path:
    """Return a compact JPEG rendition for exports, cached beside the route PNG."""
    image_path = Path(cache_root) / f"{document_id}.page-{page_number}.export.jpg"
    with _PAGE_IMAGE_LOCK:
        image_path.parent.mkdir(parents=True, exist_ok=True)
        if image_path.exists() and image_path.stat().st_size <= PAGE_IMAGE_MAX_BYTES:
            return image_path
        image_path.unlink(missing_ok=True)
        try:
            pdf = pymupdf.open(source_path)
            try:
                page = pdf.load_page(page_number - 1)
                width_points = float(page.rect.width)
                height_points = float(page.rect.height)
                area = max(width_points * height_points, 1.0)

                def render_jpeg(dpi: int) -> bytes:
                    scale = dpi / 72
                    if area * scale * scale > PAGE_IMAGE_MAX_PIXELS:
                        scale = (PAGE_IMAGE_MAX_PIXELS / area) ** 0.5
                    pixmap = page.get_pixmap(
                        matrix=pymupdf.Matrix(scale, scale),
                        colorspace=pymupdf.csRGB,
                        alpha=False,
                    )
                    return pixmap.tobytes(
                        "jpeg", jpg_quality=EXPORT_PAGE_IMAGE_QUALITY
                    )

                image = render_jpeg(EXPORT_PAGE_IMAGE_DPI)
                if len(image) > PAGE_IMAGE_MAX_BYTES:
                    image = render_jpeg(_EXPORT_FALLBACK_DPI)
                if len(image) > PAGE_IMAGE_MAX_BYTES:
                    raise ApiError(
                        "page_image_too_large",
                        "This page image exceeds the export image size limit.",
                        413,
                    )
            finally:
                pdf.close()
        except ApiError:
            raise
        except Exception as exc:  # noqa: BLE001 - exports report an image warning
            logger.info(
                "export page image render failed document_id=%s page=%s",
                document_id,
                page_number,
            )
            raise ApiError(
                "page_not_found",
                "The requested PDF page could not be rendered.",
                404,
            ) from exc

        temporary_path = image_path.with_suffix(".jpg.tmp")
        temporary_path.write_bytes(image)
        temporary_path.replace(image_path)
    return image_path
