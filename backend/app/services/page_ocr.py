from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

import pymupdf

from app.config import Settings
from app.errors import ApiError
from app.services.ocr import OcrFailed, OcrTimeout, OcrUnavailable, OcrRunner, run_tesseract
from app.services.storage import StoredDocument, storage

logger = logging.getLogger(__name__)

OCR_MAX_RETRIES = 2
OCR_MAX_ATTEMPTS = OCR_MAX_RETRIES + 1


@dataclass
class _LockEntry:
    lock: threading.Lock
    users: int = 0


_registry_lock = threading.Lock()
_document_locks: dict[str, _LockEntry] = {}


@contextmanager
def _document_ocr_lock(document_id: str) -> Iterator[None]:
    with _registry_lock:
        entry = _document_locks.get(document_id)
        if entry is None:
            entry = _LockEntry(threading.Lock())
            _document_locks[document_id] = entry
        entry.users += 1
    entry.lock.acquire()
    try:
        yield
    finally:
        entry.lock.release()
        with _registry_lock:
            entry.users -= 1
            if entry.users == 0:
                _document_locks.pop(document_id, None)


@dataclass(frozen=True)
class OcrProgress:
    status: str
    total_pages: int
    needs_ocr_pages: int
    completed_pages: int
    pending_pages: list[int]
    failed_pages: list[int]
    empty_pages: list[int]


@dataclass(frozen=True)
class OcrReport:
    warnings: list[dict[str, str]]
    attempted_pages: list[int]
    completed_pages: list[int]
    failed_pages: list[int]
    pending_pages: list[int]


def _warning(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _merge_text(native_text: str, ocr_text: str) -> str:
    """Keep native wording and add OCR lines without exact duplicate lines."""
    native_lines = native_text.strip("\n").splitlines()
    seen = {" ".join(line.split()).casefold() for line in native_lines if line.strip()}
    additions: list[str] = []
    for line in ocr_text.strip("\n").splitlines():
        key = " ".join(line.split()).casefold()
        if line.strip() and key not in seen:
            additions.append(line)
            seen.add(key)
    parts = []
    if native_text.strip():
        parts.append(native_text.rstrip())
    if additions:
        parts.append("\n".join(additions))
    return "\n".join(parts)


def _page_attempts(page: dict) -> int:
    return int(page.get("ocr_attempts") or 0)


def _stderr_excerpt(exc: BaseException) -> str:
    """Return a bounded one-line diagnostic; never log OCR output or filenames."""
    stderr = getattr(exc, "stderr", "")
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return (
        str(stderr or "")
        .replace("\r", " ")
        .replace("\n", " ")
        .replace("\t", " ")[:200]
    )


def _page_statuses(pages: list[dict], requested: set[int]) -> tuple[list[int], list[int]]:
    pending: list[int] = []
    failed: list[int] = []
    for page in pages:
        number = int(page.get("page_number") or int(page.get("index", 0)) + 1)
        if number not in requested:
            continue
        status = str(page.get("ocr_status") or "none")
        if status == "pending" and _page_attempts(page) >= OCR_MAX_ATTEMPTS:
            failed.append(number)
        elif status == "pending":
            pending.append(number)
        elif status == "failed":
            failed.append(number)
    return sorted(pending), sorted(failed)


def _attemptable_page_numbers(pages: list[dict], requested: set[int]) -> list[int]:
    """Pending pages plus failed pages whose retry allowance is not exhausted."""
    attemptable: list[int] = []
    for page in pages:
        number = int(page.get("page_number") or int(page.get("index", 0)) + 1)
        if number not in requested:
            continue
        status = str(page.get("ocr_status") or "none")
        if status in {"pending", "failed"} and _page_attempts(page) < OCR_MAX_ATTEMPTS:
            attemptable.append(number)
    return sorted(attemptable)


def _ocr_page_numbers(pages: list[dict], requested: set[int] | None = None) -> list[int]:
    numbers: list[int] = []
    for page in pages:
        number = int(page.get("page_number") or int(page.get("index", 0)) + 1)
        if requested is not None and number not in requested:
            continue
        if page.get("needs_ocr"):
            numbers.append(number)
    return sorted(numbers)


def ocr_progress(
    record: StoredDocument, page_numbers: list[int] | set[int] | None = None
) -> OcrProgress:
    """Report OCR state from persisted pages without starting any OCR work."""
    requested = (
        {int(number) for number in page_numbers if int(number) > 0}
        if page_numbers is not None
        else None
    )
    if record.format != "pdf" or not record.page_count:
        return OcrProgress("not_needed", 0, 0, 0, [], [], [])
    pages = storage.pages_for(record.document_id)
    if not pages:
        pages = list(record.pages)
    needed = _ocr_page_numbers(pages, requested)
    total_pages = record.page_count or len(pages)
    if not needed:
        return OcrProgress("not_needed", total_pages, 0, 0, [], [], [])
    by_number = {
        int(page.get("page_number") or int(page.get("index", 0)) + 1): page
        for page in pages
        if int(page.get("page_number") or int(page.get("index", 0)) + 1) in needed
    }
    completed: list[int] = []
    pending: list[int] = []
    failed: list[int] = []
    empty: list[int] = []
    for number in needed:
        page = by_number.get(number, {})
        status = str(page.get("ocr_status") or "none")
        attempts = _page_attempts(page)
        if status == "done":
            completed.append(number)
            if page.get("ocr_empty"):
                empty.append(number)
        elif status in {"failed", "pending"} and attempts >= OCR_MAX_ATTEMPTS:
            failed.append(number)
        elif status == "failed":
            pending.append(number)
        else:
            pending.append(number)
    if pending:
        status = "pending"
    elif failed:
        status = "incomplete"
    else:
        status = "complete"
    return OcrProgress(
        status=status,
        total_pages=total_pages,
        needs_ocr_pages=len(needed),
        completed_pages=len(completed),
        pending_pages=pending,
        failed_pages=failed,
        empty_pages=empty,
    )


def raise_if_ocr_incomplete(
    record: StoredDocument,
    page_numbers: list[int] | set[int] | None,
    *,
    action: str,
) -> None:
    """Block study/export when requested pages still have unread OCR content."""
    if not page_numbers or record.format != "pdf":
        return
    progress = ocr_progress(record, page_numbers)
    pending, failed = progress.pending_pages, progress.failed_pages
    if not pending and not failed:
        return
    labels: list[str] = []
    if pending:
        labels.append("page(s) " + ", ".join(map(str, pending)) + " still need OCR")
    if failed:
        labels.append(
            "OCR failed for page(s) "
            + ", ".join(map(str, failed))
            + f" after {OCR_MAX_ATTEMPTS} attempts"
        )
    next_step = (
        "Prepare the pending pages and re-upload a clearer scan for failed pages"
        if pending and failed
        else "Prepare the pending pages"
        if pending
        else "Re-upload a clearer scan"
    )
    raise ApiError(
        "ocr_incomplete",
        "The document is not fully readable yet: "
        + "; ".join(labels)
        + f". {next_step} before you {action}.",
        409,
        {
            "document_id": record.document_id,
            "action": action,
            "pending_pages": pending,
            "failed_pages": failed,
            "progress": {
                "status": progress.status,
                "total_pages": progress.total_pages,
                "needs_ocr_pages": progress.needs_ocr_pages,
                "completed_pages": progress.completed_pages,
                "pending_pages": progress.pending_pages,
                "failed_pages": progress.failed_pages,
            },
        },
    )


def requested_pdf_pages(
    record: StoredDocument,
    *,
    selector_type: str | None = None,
    spec: str | None = None,
    legacy_selector: str | None = None,
    selected_sections: list[dict] | None = None,
) -> list[int]:
    """Resolve page selectors before extraction; otherwise use native matches."""
    if record.format != "pdf" or not record.page_count:
        return []
    page_numbers: set[int] = set()
    ranges: list[tuple[int, int]] = []
    try:
        if selector_type == "pages":
            from app.services.range_extraction import parse_number_ranges

            ranges = parse_number_ranges(spec or "")
        elif legacy_selector is not None:
            from app.services.range_extraction import _legacy_ranges

            ranges = _legacy_ranges(legacy_selector)
    except Exception:
        ranges = []  # The normal selector path returns the established validation error.
    for first, last in ranges:
        low, high = sorted((first, last))
        if low < 1 or high > record.page_count:
            continue  # Let extraction return its normal out-of-range error.
        page_numbers.update(range(low, high + 1))

    if selector_type == "whole" or (
        legacy_selector is not None
        and legacy_selector.strip().casefold() in {"whole", "whole document", "all pages"}
    ):
        page_numbers.update(range(1, record.page_count + 1))
    for section in selected_sections or []:
        anchor = section.get("anchor") or {}
        page = anchor.get("page")
        if anchor.get("kind") == "pdf_page" and isinstance(page, int):
            page_numbers.add(page)
    return sorted(page_numbers)


def ensure_pdf_pages_ocr(
    record: StoredDocument,
    page_numbers: list[int] | set[int],
    settings: Settings,
    *,
    ocr_runner: OcrRunner | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> OcrReport:
    """OCR requested pending PDF pages, persist results, and return limits/errors."""
    requested = {int(number) for number in page_numbers if int(number) > 0}
    if record.format != "pdf" or not requested:
        return OcrReport([], [], [], [], [])

    attempted: list[int] = []
    completed: list[int] = []
    failed_now: list[int] = []
    runner = ocr_runner or run_tesseract
    page_limit = max(
        0,
        min(
            int(settings.ocr_page_limit),
            int(settings.ocr_request_page_limit),
        ),
    )
    budget_s = max(float(settings.ocr_request_time_budget_s), 0.0)
    started = clock()

    with _document_ocr_lock(record.document_id):
        current = storage.get(record.document_id)
        if current is None or not current.available:
            return OcrReport(
                [
                    _warning(
                        "ocr_source_unavailable",
                        "The source PDF is unavailable; OCR could not read the requested pages.",
                    )
                ],
                [],
                [],
                [],
                [],
            )
        pages = storage.pages_for(record.document_id)
        attemptable = _attemptable_page_numbers(pages, requested)
        attemptable_indexes = {
            int(page.get("index", 0)): int(page.get("page_number") or int(page.get("index", 0)) + 1)
            for page in pages
            if int(page.get("page_number") or int(page.get("index", 0)) + 1) in attemptable
        }
        pages_by_index = {int(page.get("index", 0)): page for page in pages}
        page_count = 0
        pdf: pymupdf.Document | None = None
        for page_index, page_number in attemptable_indexes.items():
            page = pages_by_index[page_index]
            elapsed = clock() - started
            remaining = budget_s - elapsed
            if page_count >= page_limit or remaining < 1:
                break
            page_count += 1
            attempted.append(page_number)
            page_started = clock()
            image_width = 0
            image_height = 0
            ocr_characters = 0
            exception_type = "none"
            return_code: int | str = "none"
            stderr_excerpt = ""
            stop_after_page = False
            storage.record_pdf_page_ocr_attempt(current.document_id, page_index)
            if pdf is None:
                try:
                    pdf = pymupdf.open(current.path)
                except Exception as exc:
                    failed_now.append(page_number)
                    storage.update_pdf_page_ocr(
                        current.document_id,
                        page_index,
                        text=str(page.get("text") or ""),
                        source=str(page.get("source") or "none"),
                        ocr_status="failed",
                    )
                    duration_ms = (clock() - page_started) * 1000
                    logger.info(
                        "pdf page OCR document_id=%s page=%s status=failed ms=%.1f "
                        "image_width=0 image_height=0 dpi=%s ocr_characters=0 "
                        "exception_type=%s return_code=none stderr_excerpt=",
                        record.document_id,
                        page_number,
                        settings.ocr_dpi,
                        type(exc).__name__,
                    )
                    break
            try:
                source_page = pdf.load_page(page_index)
                pixmap = source_page.get_pixmap(
                    dpi=settings.ocr_dpi,
                    colorspace=pymupdf.csGRAY,
                    alpha=False,
                )
                image_width = int(pixmap.width)
                image_height = int(pixmap.height)
                image_bytes = pixmap.tobytes("png")
                remaining = budget_s - (clock() - started)
                if remaining < 1:
                    duration_ms = (clock() - page_started) * 1000
                    logger.info(
                        "pdf page OCR document_id=%s page=%s status=pending ms=%.1f "
                        "image_width=%s image_height=%s dpi=%s ocr_characters=0 "
                        "exception_type=none return_code=none stderr_excerpt=",
                        record.document_id,
                        page_number,
                        duration_ms,
                        image_width,
                        image_height,
                        settings.ocr_dpi,
                    )
                    break
                timeout_s = max(1, min(int(settings.ocr_timeout_s), int(remaining)))
                runner_settings = settings.model_copy(update={"ocr_timeout_s": timeout_s})
                ocr_text = runner(image_bytes, ".png", runner_settings)
                ocr_characters = len(ocr_text)
                if not "".join(ocr_text.split()):
                    native_text = str(page.get("text") or "")
                    storage.update_pdf_page_ocr(
                        current.document_id,
                        page_index,
                        text=native_text,
                        source=str(page.get("source") or "none"),
                        ocr_status="done",
                        ocr_empty=True,
                    )
                    completed.append(page_number)
                    outcome = "empty"
                else:
                    native_text = str(page.get("text") or "")
                    merged = _merge_text(native_text, ocr_text)
                    storage.update_pdf_page_ocr(
                        current.document_id,
                        page_index,
                        text=merged,
                        source="ocr",
                        ocr_status="done",
                    )
                    completed.append(page_number)
                    outcome = "done"
            except OcrUnavailable as exc:
                storage.update_pdf_page_ocr(
                    current.document_id,
                    page_index,
                    text=str(page.get("text") or ""),
                    source=str(page.get("source") or "none"),
                    ocr_status="failed",
                )
                failed_now.append(page_number)
                outcome = "unavailable"
                exception_type = type(exc).__name__
                stop_after_page = True
            except (OcrTimeout, OcrFailed) as exc:
                storage.update_pdf_page_ocr(
                    current.document_id,
                    page_index,
                    text=str(page.get("text") or ""),
                    source=str(page.get("source") or "none"),
                    ocr_status="failed",
                )
                failed_now.append(page_number)
                outcome = "failed"
                exception_type = type(exc).__name__
                if isinstance(exc, OcrFailed):
                    return_code_value = getattr(exc, "return_code", None)
                    return_code = (
                        return_code_value
                        if return_code_value is not None
                        else "none"
                    )
                    stderr_excerpt = _stderr_excerpt(exc)
            except Exception as exc:  # noqa: BLE001 - failed page falls back to native text
                storage.update_pdf_page_ocr(
                    current.document_id,
                    page_index,
                    text=str(page.get("text") or ""),
                    source=str(page.get("source") or "none"),
                    ocr_status="failed",
                )
                failed_now.append(page_number)
                outcome = type(exc).__name__
                exception_type = type(exc).__name__
            duration_ms = (clock() - page_started) * 1000
            logger.info(
                "pdf page OCR document_id=%s page=%s status=%s ms=%.1f "
                "image_width=%s image_height=%s dpi=%s ocr_characters=%s "
                "exception_type=%s return_code=%s stderr_excerpt=%s",
                record.document_id,
                page_number,
                outcome,
                duration_ms,
                image_width,
                image_height,
                settings.ocr_dpi,
                ocr_characters,
                exception_type,
                return_code,
                stderr_excerpt,
            )
            if stop_after_page:
                break
        if pdf is not None:
            pdf.close()

    pages = storage.pages_for(record.document_id)
    pending, failed = _page_statuses(pages, requested)
    attempts_by_page = {
        int(page.get("page_number") or int(page.get("index", 0)) + 1): _page_attempts(page)
        for page in pages
    }
    retryable_failed = [
        number for number in failed if attempts_by_page.get(number, 0) < OCR_MAX_ATTEMPTS
    ]
    exhausted_failed = [
        number for number in failed if attempts_by_page.get(number, 0) >= OCR_MAX_ATTEMPTS
    ]
    empty_pages = sorted(
        int(page.get("page_number") or int(page.get("index", 0)) + 1)
        for page in pages
        if int(page.get("page_number") or int(page.get("index", 0)) + 1) in requested
        and page.get("ocr_empty")
    )
    warnings: list[dict[str, str]] = []
    if retryable_failed:
        warnings.append(
            _warning(
                "ocr_failed",
                "OCR failed on page(s) "
                + ", ".join(map(str, retryable_failed))
                + "; a retry will run on the next request. Native text was returned "
                "where available.",
            )
        )
    if exhausted_failed:
        warnings.append(
            _warning(
                "ocr_failed",
                "OCR failed on page(s) "
                + ", ".join(map(str, exhausted_failed))
                + f" after {OCR_MAX_ATTEMPTS} attempts; native text was returned "
                "where available.",
            )
        )
    if pending:
        warnings.append(
            _warning(
                "ocr_pending_pages",
                "The request page cap or time budget was reached. OCR remains pending "
                "for page(s): "
                + ", ".join(map(str, pending))
                + ".",
            )
        )
    if empty_pages:
        warnings.append(
            _warning(
                "ocr_empty_pages",
                "Picture page(s), no text could be read: "
                + ", ".join(map(str, empty_pages))
                + ".",
            )
        )
    return OcrReport(warnings, attempted, completed, failed_now, pending)
