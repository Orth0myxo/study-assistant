from __future__ import annotations

import struct
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from app.config import Settings
from app.errors import ApiError
from app.services.ocr import (
    OcrFailed,
    OcrRunner,
    OcrTimeout,
    OcrUnavailable,
    run_tesseract,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"

IMAGE_CONTENT_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
}
IMAGE_SUFFIXES = {
    "png": ".png",
    "jpeg": ".jpg",
}

_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}

_ocr_slots = threading.BoundedSemaphore(4)
_ocr_slots_lock = threading.Lock()


def configure_ocr_concurrency(limit: int) -> None:
    """Bound concurrent topic-image OCR work within this process."""
    global _ocr_slots
    slots = max(int(limit), 1)
    with _ocr_slots_lock:
        _ocr_slots = threading.BoundedSemaphore(slots)


@dataclass
class ValidatedImage:
    filename: str
    content_type: str
    suffix: str
    data: bytes
    width: int
    height: int


@dataclass
class OcrResult:
    status: str
    text: str
    warnings: list[dict[str, str]] = field(default_factory=list)


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def detect_image_type(data: bytes) -> str | None:
    """Return ``png`` or ``jpeg`` from actual file bytes, never the filename."""
    if data.startswith(PNG_MAGIC):
        return "png"
    if data.startswith(JPEG_MAGIC):
        return "jpeg"
    return None


def _png_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 24:
        raise ApiError("corrupt_image", "The image file could not be read.", 400)
    length = int.from_bytes(data[8:12], "big")
    if length < 13 or data[12:16] != b"IHDR":
        raise ApiError("corrupt_image", "The image file could not be read.", 400)
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    offset = 2
    size = len(data)
    while offset + 4 <= size:
        if data[offset] != 0xFF:
            raise ApiError("corrupt_image", "The image file could not be read.", 400)
        marker = data[offset + 1]
        if marker == 0xFF:
            offset += 1
            continue
        if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            offset += 2
            continue
        if offset + 4 > size:
            break
        segment_length = int.from_bytes(data[offset + 2 : offset + 4], "big")
        if segment_length < 2 or offset + 2 + segment_length > size:
            break
        if marker in _SOF_MARKERS:
            if segment_length < 7 or offset + 9 > size:
                break
            height = int.from_bytes(data[offset + 5 : offset + 7], "big")
            width = int.from_bytes(data[offset + 7 : offset + 9], "big")
            return width, height
        offset += 2 + segment_length
    raise ApiError("corrupt_image", "The image file could not be read.", 400)


def _header_dimensions(kind: str, data: bytes) -> tuple[int, int]:
    return _png_dimensions(data) if kind == "png" else _jpeg_dimensions(data)


def _safe_filename(filename: str | None) -> str:
    raw = (filename or "note").replace("\x00", "").replace("\\", "/")
    name = Path(raw).name.strip() or "note"
    return name[:200]


def validate_image(
    filename: str | None,
    data: bytes,
    settings: Settings,
) -> ValidatedImage:
    """Validate an uploaded note image using its bytes and decoded dimensions.

    The filename and client-provided content type are ignored for type
    detection. Only PNG and JPEG are accepted. Dimensions are checked before
    decoding so a decompression bomb cannot force a large allocation.
    """
    if not data:
        raise ApiError("empty_image", "The uploaded image is empty.", 400)

    max_bytes = int(settings.topic_max_image_mb) * 1024 * 1024
    if len(data) > max_bytes:
        raise ApiError(
            "image_too_large",
            f"A note image exceeds the {settings.topic_max_image_mb} MB limit.",
            413,
            {"limit_mb": settings.topic_max_image_mb},
        )

    kind = detect_image_type(data)
    if kind is None:
        raise ApiError(
            "unsupported_image_type",
            "Only PNG and JPEG note images are supported.",
            415,
        )

    width, height = _header_dimensions(kind, data)
    if width <= 0 or height <= 0:
        raise ApiError("corrupt_image", "The image file could not be read.", 400)

    max_pixels = max(int(settings.topic_max_image_pixels), 1)
    if width * height > max_pixels:
        raise ApiError(
            "image_too_large",
            "The image dimensions exceed the server limit.",
            413,
            {"max_pixels": max_pixels},
        )

    try:
        pixmap = pymupdf.Pixmap(data)
    except Exception as exc:  # noqa: BLE001 - malformed image bytes
        raise ApiError(
            "corrupt_image", "The image file could not be read.", 400
        ) from exc

    try:
        if pixmap.width <= 0 or pixmap.height <= 0:
            raise ApiError("corrupt_image", "The image file could not be read.", 400)
        if pixmap.width * pixmap.height > max_pixels:
            raise ApiError(
                "image_too_large",
                "The image dimensions exceed the server limit.",
                413,
                {"max_pixels": max_pixels},
            )
    finally:
        pixmap = None

    return ValidatedImage(
        filename=_safe_filename(filename),
        content_type=IMAGE_CONTENT_TYPES[kind],
        suffix=IMAGE_SUFFIXES[kind],
        data=data,
        width=width,
        height=height,
    )


def ocr_image(
    image: ValidatedImage,
    settings: Settings,
    *,
    runner: OcrRunner | None = None,
) -> OcrResult:
    """Run the shared Tesseract runner and return editable text with warnings.

    This is best-effort OCR, not a handwriting-recognition service. The text is
    always returned as editable transcript content, and callers are warned that
    it may be wrong.
    """
    if settings.ocr_page_limit <= 0:
        return OcrResult(
            status="unavailable",
            text="",
            warnings=[
                _warning(
                    "ocr_disabled",
                    "OCR is disabled on the server; enter the note text manually.",
                )
            ],
        )

    runner = runner or run_tesseract
    with _ocr_slots:
        try:
            raw = runner(image.data, image.suffix, settings)
        except OcrUnavailable:
            return OcrResult(
                status="unavailable",
                text="",
                warnings=[
                    _warning(
                        "ocr_unavailable",
                        "OCR is not available on the server; enter the note text manually.",
                    )
                ],
            )
        except OcrTimeout:
            return OcrResult(
                status="timeout",
                text="",
                warnings=[
                    _warning(
                        "ocr_timeout",
                        "OCR timed out for this image, so no text was recognized. "
                        "Enter the note text manually.",
                    )
                ],
            )
        except OcrFailed:
            return OcrResult(
                status="failed",
                text="",
                warnings=[
                    _warning(
                        "ocr_failed",
                        "OCR failed for this image, so no text was recognized. "
                        "Enter the note text manually.",
                    )
                ],
            )
        except Exception:  # noqa: BLE001 - do not leak runner details or content
            return OcrResult(
                status="failed",
                text="",
                warnings=[
                    _warning(
                        "ocr_failed",
                        "OCR failed for this image, so no text was recognized. "
                        "Enter the note text manually.",
                    )
                ],
            )
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return OcrResult(
            status="empty",
            text="",
            warnings=[
                _warning(
                    "ocr_empty",
                    "OCR found no usable text. Handwriting recognition is unreliable; "
                    "enter or correct the text manually.",
                )
            ],
        )

    warnings = [
        _warning(
            "ocr_review_recommended",
            "OCR text is a best-effort transcription and can be wrong, especially "
            "for handwriting. Review and correct it.",
        )
    ]
    limit = int(settings.topic_max_transcript_chars)
    if limit > 0 and len(text) > limit:
        text = text[:limit]
        warnings.append(
            _warning(
                "ocr_text_truncated",
                f"Recognized text was truncated to {limit} characters.",
            )
        )

    return OcrResult(status="ok", text=text, warnings=warnings)
