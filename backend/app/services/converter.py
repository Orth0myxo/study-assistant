"""Bounded file conversions and AI summary tables for the File Converter."""

from __future__ import annotations

import csv
import io
import logging
import re
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pymupdf
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.config import Settings
from app.errors import ApiError
from app.services.ai_chain import build_ai_client
from app.services.ai_client import AiError
from app.services.convert_store import ConversionJob, conversion_jobs
from app.services.document_parser import detect_pdf_page_tables, parse_document
from app.services.exporter import (
    render,
    source_blocks_for_conversion,
    text_blocks_for_conversion,
)
from app.services.ocr import OcrError, run_tesseract

logger = logging.getLogger(__name__)

_CONVERSION_SLOTS = threading.BoundedSemaphore(1)

CONVERT_MAX_ROWS = 5000
CONVERT_MAX_COLUMNS = 200
CONVERT_MAX_CELL_CHARS = 5000
CONVERT_MAX_TABLES = 20
OCR_TEXT_WARNING = "Some text was read by OCR and may contain errors."
LAYOUT_NOTICE = (
    "Converted with a simplified layout; the original layout and styling are not preserved."
)
AI_TABLE_NOTICE = "AI-generated summary table, not source text. Review every cell."


class ConversionError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class ConversionDefinition:
    conversion_id: str
    input_kind: Literal["csv", "xlsx", "docx", "pdf", "text"]
    output_kind: Literal["xlsx", "docx", "pdf", "csv"]
    label: str
    description: str
    handler: str
    input_mode: Literal["file", "text"] = "file"
    needs_ai: bool = False


_INPUT_META: dict[str, tuple[str, list[str], bool]] = {
    "text": ("Pasted text", [], True),
    "csv": ("CSV", [".csv", ".tsv"], False),
    "xlsx": ("Excel workbook", [".xlsx"], False),
    "docx": ("Word document", [".docx"], False),
    "pdf": ("PDF", [".pdf"], False),
}

_OUTPUT_LABELS = {
    "xlsx": "Excel",
    "docx": "Word",
    "pdf": "PDF",
    "csv": "CSV",
}

_CONVERSIONS: tuple[ConversionDefinition, ...] = (
    ConversionDefinition(
        "csv-to-xlsx",
        "csv",
        "xlsx",
        "Excel workbook",
        "Keep the rows and columns as a spreadsheet.",
        "csv_to_xlsx",
    ),
    ConversionDefinition(
        "xlsx-to-csv",
        "xlsx",
        "csv",
        "CSV",
        "Export one sheet as comma-separated text.",
        "xlsx_to_csv",
    ),
    ConversionDefinition(
        "csv-to-docx",
        "csv",
        "docx",
        "Word table",
        "Keep the rows and columns in a Word table.",
        "csv_to_docx",
    ),
    ConversionDefinition(
        "xlsx-to-docx",
        "xlsx",
        "docx",
        "Word table",
        "Keep one sheet's rows and columns in a Word table.",
        "xlsx_to_docx",
    ),
    ConversionDefinition(
        "docx-tables-to-xlsx",
        "docx",
        "xlsx",
        "Excel workbook",
        "Put each Word table on its own sheet.",
        "docx_tables_to_xlsx",
    ),
    ConversionDefinition(
        "text-to-pdf",
        "text",
        "pdf",
        "PDF",
        "Typeset pasted text as a PDF.",
        "text_to_pdf",
        input_mode="text",
    ),
    ConversionDefinition(
        "docx-to-pdf",
        "docx",
        "pdf",
        "PDF",
        "Keep text, headings and tables; layout is simplified.",
        "docx_to_pdf",
    ),
    ConversionDefinition(
        "pdf-to-docx",
        "pdf",
        "docx",
        "Word document",
        "Keep text, headings and detected tables; layout is simplified.",
        "pdf_to_docx",
    ),
) + tuple(
    ConversionDefinition(
        f"summary-{source}-to-{output}",
        source,
        output,
        f"Summary table ({_OUTPUT_LABELS[output]})",
        "Ask AI for a short table, then review and edit every cell before export.",
        "summary_table",
        input_mode="text" if source == "text" else "file",
        needs_ai=True,
    )
    for source in ("text", "pdf", "docx")
    for output in ("xlsx", "docx")
)


def conversion_formats() -> dict[str, Any]:
    inputs: list[dict[str, Any]] = []
    for kind, (label, accept, supports_text) in _INPUT_META.items():
        options = []
        for definition in _CONVERSIONS:
            if definition.input_kind != kind:
                continue
            options.append(
                {
                    "conversion_id": definition.conversion_id,
                    "input_kind": definition.input_kind,
                    "output_kind": definition.output_kind,
                    "label": definition.label,
                    "description": definition.description,
                    "output_label": _OUTPUT_LABELS[definition.output_kind],
                    "needs_ai": definition.needs_ai,
                    "input_mode": definition.input_mode,
                }
            )
        if options:
            inputs.append(
                {
                    "kind": kind,
                    "label": label,
                    "accept": accept,
                    "supports_text": supports_text,
                    "conversions": options,
                }
            )
    return {"inputs": inputs}


def get_conversion(conversion_id: str) -> ConversionDefinition | None:
    return next(
        (item for item in _CONVERSIONS if item.conversion_id == conversion_id),
        None,
    )


def start_conversion_job(job_id: str, owner_id: str, settings: Settings) -> None:
    thread = threading.Thread(
        target=run_conversion_job,
        args=(job_id, owner_id, settings),
        name=f"convert-{job_id[:8]}",
        daemon=True,
    )
    thread.start()


def run_conversion_job(job_id: str, owner_id: str, settings: Settings) -> None:
    with _CONVERSION_SLOTS:
        _run_conversion_job(job_id, owner_id, settings)


def _run_conversion_job(job_id: str, owner_id: str, settings: Settings) -> None:
    job = conversion_jobs.get_for_owner(job_id, owner_id)
    if job is None:
        return
    started = time.perf_counter()
    input_bytes = _path_size(job.input_path)
    job.status = "running"
    conversion_jobs.save(job)
    try:
        definition = get_conversion(job.conversion_id)
        if definition is None:
            raise ConversionError(
                "conversion_not_found", "That conversion is not available."
            )
        if definition.handler == "summary_table":
            table, warnings = _summary_table(job, definition, settings)
            conversion_jobs.finish_table(
                job.job_id,
                job.owner_id,
                table=table,
                warnings=warnings,
            )
            _log_conversion(job, "done", started, input_bytes, 0)
            return
        data, name, file_format, warnings = _convert_file(job, definition, settings)
        conversion_jobs.finish(
            job.job_id,
            job.owner_id,
            data=data,
            file_name=name,
            file_format=file_format,
            warnings=warnings,
        )
        _log_conversion(job, "done", started, input_bytes, len(data))
    except ConversionError as exc:
        conversion_jobs.fail(
            job.job_id,
            job.owner_id,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
        _log_conversion(job, "failed", started, input_bytes, 0, exc.code)
    except ApiError as exc:
        conversion_jobs.fail(
            job.job_id,
            job.owner_id,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )
        _log_conversion(job, "failed", started, input_bytes, 0, exc.code)
    except AiError as exc:
        conversion_jobs.fail(
            job.job_id,
            job.owner_id,
            code=exc.code,
            message=exc.message,
            details={"retry_after_s": exc.retry_after} if exc.retry_after else {},
        )
        _log_conversion(job, "failed", started, input_bytes, 0, exc.code)
    except Exception as exc:
        logger.warning(
            "conversion failed job_id=%s error_type=%s",
            job.job_id,
            type(exc).__name__,
        )
        conversion_jobs.fail(
            job.job_id,
            job.owner_id,
            code="conversion_failed",
            message="The file could not be converted.",
        )
        _log_conversion(job, "failed", started, input_bytes, 0, "conversion_failed")


def _path_size(path: Path | None) -> int:
    try:
        return path.stat().st_size if path is not None and path.is_file() else 0
    except OSError:
        return 0


def _log_conversion(
    job: ConversionJob,
    outcome: str,
    started: float,
    input_bytes: int,
    output_bytes: int,
    code: str | None = None,
) -> None:
    logger.info(
        "conversion %s job_id=%s conversion=%s input_kind=%s output_kind=%s "
        "input_bytes=%d output_bytes=%d seconds=%.3f%s",
        outcome,
        job.job_id,
        job.conversion_id,
        job.input_kind,
        job.output_kind,
        input_bytes,
        output_bytes,
        time.perf_counter() - started,
        f" code={code}" if code else "",
    )


def export_summary_table(
    owner_id: str,
    job_id: str,
    file_format: Literal["xlsx", "docx"],
    columns: list[str],
    rows: list[list[str]],
    title: str | None,
    settings: Settings,
) -> ConversionJob:
    job = conversion_jobs.get_for_owner(job_id, owner_id)
    if job is None:
        raise ConversionError(
            "conversion_not_found",
            "The conversion was not found or has expired.",
        )
    if job.status != "completed" or not job.table:
        raise ConversionError(
            "conversion_not_ready",
            "The summary table is not ready to export.",
        )
    if not job.conversion_id.startswith("summary-"):
        raise ConversionError(
            "conversion_export_invalid",
            "That conversion does not support an edited table export.",
        )
    clean = _validate_edited_table(columns, rows, settings)
    table_title = str(title or job.table.get("title") or "").strip()
    stem = _output_stem(job, "summary-table")
    file_name = f"{stem}-summary-table.{file_format}"
    if file_format == "xlsx":
        data = _write_xlsx(clean, notice=AI_TABLE_NOTICE)
    else:
        data = _write_table_docx(table_title, clean, AI_TABLE_NOTICE)
    finished = conversion_jobs.finish(
        job.job_id,
        owner_id,
        data=data,
        file_name=file_name,
        file_format=file_format,
    )
    if finished is None:
        raise ConversionError(
            "conversion_not_found",
            "The conversion was not found or has expired.",
        )
    finished.output_kind = file_format
    finished.table = {
        "title": table_title,
        "columns": list(columns),
        "rows": [list(row) for row in rows],
    }
    conversion_jobs.save(finished)
    return finished


def _convert_file(
    job: ConversionJob,
    definition: ConversionDefinition,
    settings: Settings,
) -> tuple[bytes, str, str, list[str]]:
    data = _read_input(job, settings)
    warnings: list[str] = []
    if definition.handler == "csv_to_xlsx":
        rows = _read_csv(data, settings)
        return _write_xlsx(rows), _output_name(job, "xlsx"), "xlsx", warnings
    if definition.handler == "xlsx_to_csv":
        rows = _read_xlsx(data, job.sheet, settings)
        return _write_csv(rows), _output_name(job, "csv"), "csv", warnings
    if definition.handler == "csv_to_docx":
        rows = _read_csv(data, settings)
        name = _output_name(job, "docx")
        return (
            _write_table_docx(_source_title(job), rows, LAYOUT_NOTICE),
            name,
            "docx",
            warnings,
        )
    if definition.handler == "xlsx_to_docx":
        rows = _read_xlsx(data, job.sheet, settings)
        name = _output_name(job, "docx")
        return (
            _write_table_docx(_source_title(job), rows, LAYOUT_NOTICE),
            name,
            "docx",
            warnings,
        )
    if definition.handler == "docx_tables_to_xlsx":
        tables, table_warnings = _docx_tables(data, settings)
        warnings.extend(table_warnings)
        return (
            _write_xlsx_tables(tables),
            _output_name(job, "xlsx", "-tables"),
            "xlsx",
            warnings,
        )
    if definition.handler == "text_to_pdf":
        text = _decode_text(data)
        if not text.strip():
            raise ConversionError("empty_text", "There is no text to convert.")
        blocks = text_blocks_for_conversion(text)
        payload = {
            "kind": "converted_document",
            "filename": _source_title(job),
            "notice": "Converted from pasted text.",
            "blocks": blocks,
        }
        return (
            render(payload, "pdf"),
            _output_name(job, "pdf"),
            "pdf",
            warnings,
        )
    if definition.handler == "docx_to_pdf":
        blocks, doc_warnings = _docx_blocks(data, settings)
        warnings.extend(doc_warnings)
        payload = {
            "kind": "converted_document",
            "filename": _source_title(job),
            "notice": LAYOUT_NOTICE,
            "blocks": blocks,
        }
        return (
            render(payload, "pdf"),
            _output_name(job, "pdf"),
            "pdf",
            warnings,
        )
    if definition.handler == "pdf_to_docx":
        blocks, doc_warnings = _pdf_blocks(data, settings)
        warnings.extend(doc_warnings)
        payload = {
            "kind": "converted_document",
            "filename": _source_title(job),
            "notice": LAYOUT_NOTICE,
            "blocks": blocks,
        }
        return (
            render(payload, "docx"),
            _output_name(job, "docx"),
            "docx",
            warnings,
        )
    raise ConversionError(
        "conversion_not_found", "That conversion is not available."
    )


def _summary_table(
    job: ConversionJob,
    definition: ConversionDefinition,
    settings: Settings,
) -> tuple[dict[str, Any], list[str]]:
    data = _read_input(job, settings)
    warnings: list[str] = []
    if definition.input_kind == "text":
        source_text = _decode_text(data)
    elif definition.input_kind == "pdf":
        source_text, doc_warnings = _pdf_text(data, settings)
        warnings.extend(doc_warnings)
    else:
        source_text, doc_warnings = _docx_text(data, settings)
        warnings.extend(doc_warnings)
    source_text = source_text.strip()
    if not source_text:
        raise ConversionError(
            "no_extractable_text",
            "No readable text was found in that source.",
        )
    limit = max(int(settings.agent_document_sample_chars), 1000)
    if len(source_text) > limit:
        source_text = source_text[:limit]
        warnings.append(
            f"The AI table used the first {limit} characters of the source."
        )
    client = build_ai_client(settings, owner_id=job.owner_id)
    try:
        table = client.summarize_table(
            source_text=source_text,
            max_rows=min(max(int(settings.table_max_rows), 1), 20),
            max_columns=min(max(int(settings.table_max_columns), 1), 6),
            max_cell_chars=min(max(int(settings.table_max_cell_chars), 1), 160),
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    title = str(table.get("title") or "").strip() or f"{_source_title(job)} summary"
    return {
        "title": title,
        "columns": table["columns"],
        "rows": table["rows"],
    }, warnings


def _read_input(job: ConversionJob, settings: Settings) -> bytes:
    if job.input_path is None or not job.input_path.is_file():
        raise ConversionError(
            "conversion_input_missing",
            "The uploaded file is no longer available.",
        )
    limit = max(settings.max_upload_mb, 1) * 1024 * 1024
    try:
        data = job.input_path.read_bytes()
    except OSError as exc:
        raise ConversionError(
            "conversion_input_missing",
            "The uploaded file is no longer available.",
        ) from exc
    if len(data) > limit:
        raise ConversionError(
            "file_too_large",
            f"The file is larger than {settings.max_upload_mb} MB.",
            {"limit_mb": settings.max_upload_mb},
        )
    return data


def validate_input(
    definition: ConversionDefinition,
    filename: str,
    data: bytes,
    settings: Settings,
) -> None:
    limit = max(settings.max_upload_mb, 1) * 1024 * 1024
    if len(data) > limit:
        raise ConversionError(
            "file_too_large",
            f"The file is larger than {settings.max_upload_mb} MB.",
            {"limit_mb": settings.max_upload_mb},
        )
    name = filename.lower()
    suffix = Path(name).suffix
    allowed = _INPUT_META[definition.input_kind][1]
    if allowed and suffix not in allowed:
        raise ConversionError(
            "file_format_mismatch",
            f"Choose a {definition.input_kind.upper()} file for this conversion.",
            {"expected": allowed},
        )
    if definition.input_kind == "pdf":
        if b"%PDF-" not in data[:1024]:
            raise ConversionError(
                "file_format_mismatch",
                "That file is not a readable PDF.",
            )
        try:
            with pymupdf.open(stream=data, filetype="pdf") as document:
                page_count = document.page_count
        except Exception as exc:
            raise ConversionError(
                "corrupt_file", "That PDF could not be read."
            ) from exc
        if page_count > settings.max_pages:
            raise ConversionError(
                "document_too_long",
                f"This PDF has {page_count} pages. The limit is "
                f"{settings.max_pages} pages.",
                {"page_count": page_count, "max_pages": settings.max_pages},
            )
        return
    if definition.input_kind in {"docx", "xlsx"}:
        if not data.startswith(b"PK\x03\x04"):
            raise ConversionError(
                "file_format_mismatch",
                f"That file is not a readable {definition.input_kind.upper()}.",
            )
        marker = "word/document.xml" if definition.input_kind == "docx" else "xl/workbook.xml"
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                uncompressed = sum(info.file_size for info in archive.infolist())
        except zipfile.BadZipFile as exc:
            raise ConversionError(
                "corrupt_file",
                f"That {definition.input_kind.upper()} file could not be read.",
            ) from exc
        if marker not in names:
            raise ConversionError(
                "file_format_mismatch",
                f"That file is not a readable {definition.input_kind.upper()}.",
            )
        if any(name.endswith("vbaProject.bin") for name in names):
            raise ConversionError(
                "macro_file_unsupported",
                "Files with macros are not supported.",
            )
        if uncompressed > settings.max_docx_uncompressed_mb * 1024 * 1024:
            raise ConversionError(
                "file_too_large",
                "The file expands beyond the supported size when opened.",
            )
        return
    if definition.input_kind == "csv":
        if (
            b"\x00" in data
            or data.startswith(b"%PDF-")
            or data.startswith(b"PK\x03\x04")
        ):
            raise ConversionError(
                "file_format_mismatch",
                "That file is not readable text.",
            )


def _source_title(job: ConversionJob) -> str:
    name = Path(job.input_name).stem or job.conversion_id.replace("-", " ")
    name = re.sub(r"(?i)[\s_-]+compressed$", "", name)
    name = re.sub(r"[_]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" ._-")
    return (name or "Converted file")[:160]


def _output_stem(job: ConversionJob, fallback: str = "converted-file") -> str:
    stem = _source_title(job)
    stem = re.sub(r"[^\w.\- ]+", "-", stem, flags=re.UNICODE).strip(" ._-")
    return (stem[:100].rstrip(" ._-") or fallback)


def _output_name(job: ConversionJob, suffix: str, label: str = "") -> str:
    return f"{_output_stem(job)}{label}.{suffix}"


def _decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise ConversionError("file_format_mismatch", "That file is not readable text.")
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        sample = text[:20000]
        if sample and sum(
            1 for char in sample if char.isprintable() or char in "\n\r\t"
        ) < len(sample) * 0.9:
            continue
        return text.replace("\r\n", "\n").replace("\r", "\n")
    raise ConversionError("file_format_mismatch", "That file is not readable text.")


def _read_csv(data: bytes, settings: Settings) -> list[list[str]]:
    text = _decode_text(data)
    if not text.strip():
        raise ConversionError("empty_table", "The CSV has no rows.")
    sample = text[:4096]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = _likely_delimiter(sample)
    rows: list[list[str]] = []
    try:
        for row in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
            if not row:
                continue
            if len(row) > CONVERT_MAX_COLUMNS:
                raise ConversionError(
                    "table_too_large",
                    "The CSV has too many columns.",
                )
            rows.append(row)
            if len(rows) > CONVERT_MAX_ROWS:
                raise ConversionError("table_too_large", "The CSV has too many rows.")
    except csv.Error as exc:
        raise ConversionError("corrupt_file", "The CSV could not be read.") from exc
    return _normalize_rows(rows, settings)


def _likely_delimiter(sample: str) -> str:
    counts = {delimiter: sample.count(delimiter) for delimiter in (",", ";", "\t")}
    return max(counts, key=counts.get)


def _read_xlsx(
    data: bytes,
    sheet: str | None,
    settings: Settings,
) -> list[list[str]]:
    try:
        workbook = load_workbook(
            io.BytesIO(data), read_only=True, data_only=True, keep_links=False
        )
    except Exception as exc:
        raise ConversionError(
            "corrupt_file", "That Excel workbook could not be read."
        ) from exc
    try:
        name = _choose_sheet(workbook.sheetnames, sheet)
        worksheet = workbook[name]
        max_rows = CONVERT_MAX_ROWS
        max_columns = CONVERT_MAX_COLUMNS
        if worksheet.max_column > max_columns:
            raise ConversionError(
                "table_too_large",
                f"The sheet has more than {max_columns} columns.",
            )
        if worksheet.max_row > max_rows:
            raise ConversionError(
                "table_too_large",
                f"The sheet has more than {max_rows} rows.",
            )
        rows: list[list[str]] = []
        for values in worksheet.iter_rows(values_only=True):
            row = [_cell_text(value) for value in values]
            if any(cell.strip() for cell in row):
                rows.append(row)
            if len(rows) > max_rows:
                raise ConversionError(
                    "table_too_large",
                    f"The sheet has more than {max_rows} rows.",
                )
        return _normalize_rows(rows, settings)
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(
            "corrupt_file", "That Excel workbook could not be read."
        ) from exc
    finally:
        workbook.close()


def _choose_sheet(sheet_names: list[str], requested: str | None) -> str:
    if not sheet_names:
        raise ConversionError("empty_table", "The workbook has no sheets.")
    if requested:
        if requested not in sheet_names:
            raise ConversionError(
                "sheet_not_found",
                "The selected sheet is not in this workbook.",
                {"sheets": sheet_names},
            )
        return requested
    if len(sheet_names) > 1:
        raise ConversionError(
            "sheet_choice_required",
            "This workbook has more than one sheet. Choose one to convert.",
            {"sheets": sheet_names},
        )
    return sheet_names[0]


def _cell_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _normalize_optional_rows(
    rows: list[list[str]], settings: Settings
) -> list[list[str]] | None:
    try:
        return _normalize_rows(rows, settings)
    except ConversionError as exc:
        if exc.code == "empty_table":
            return None
        raise


def _normalize_rows(rows: list[list[str]], settings: Settings) -> list[list[str]]:
    while rows and not any(cell.strip() for cell in rows[-1]):
        rows.pop()
    while rows and not any(cell.strip() for cell in rows[0]):
        rows.pop(0)
    if not rows:
        raise ConversionError("empty_table", "The table has no readable rows.")
    if len(rows) > CONVERT_MAX_ROWS:
        raise ConversionError(
            "table_too_large",
            f"The table has more than {CONVERT_MAX_ROWS} rows.",
        )
    width = max(len(row) for row in rows)
    max_columns = CONVERT_MAX_COLUMNS
    if width > max_columns:
        raise ConversionError(
            "table_too_large",
            f"The table has more than {max_columns} columns.",
        )
    cell_limit = CONVERT_MAX_CELL_CHARS
    normalized: list[list[str]] = []
    for row in rows:
        padded = list(row) + [""] * (width - len(row))
        if any(len(cell) > cell_limit for cell in padded):
            raise ConversionError(
                "table_too_large",
                "A table cell is longer than the supported limit.",
            )
        normalized.append(padded)
    return normalized


def _docx_tables(
    data: bytes, settings: Settings
) -> tuple[list[list[list[str]]], list[str]]:
    parsed = parse_document("converted.docx", data, settings)
    found: list[list[list[str]]] = []
    truncated = False
    for page in parsed.pages:
        for table in page.tables:
            rows = table.get("rows")
            if not isinstance(rows, list) or not rows:
                continue
            if len(found) >= CONVERT_MAX_TABLES:
                truncated = True
                continue
            normalized = _normalize_optional_rows(rows, settings)
            if normalized is not None:
                found.append(normalized)
    if not found:
        raise ConversionError("no_tables", "No tables were found in that Word file.")
    warnings = (
        [f"Only the first {CONVERT_MAX_TABLES} tables were converted."]
        if truncated
        else []
    )
    return found, warnings


def _write_xlsx(rows: list[list[str]], notice: str | None = None) -> bytes:
    return _write_xlsx_tables([rows], "Table", notice=notice)


def _write_xlsx_tables(
    tables: list[list[list[str]]],
    first_name: str = "Table",
    notice: str | None = None,
) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for index, rows in enumerate(tables, start=1):
        name = _sheet_name(first_name if index == 1 else f"Table {index}")
        worksheet = workbook.create_sheet(name)
        for row_index, row in enumerate(rows, start=1):
            for column_index, value in enumerate(row, start=1):
                worksheet.cell(
                    row=row_index,
                    column=column_index,
                    value=_neutralize_cell(value),
                )
        if rows:
            for cell in worksheet[1]:
                cell.font = Font(bold=True)
            worksheet.freeze_panes = "A2"
        for column_index, width in enumerate(
            _column_widths(rows), start=1
        ):
            worksheet.column_dimensions[get_column_letter(column_index)].width = width
    if notice:
        note = workbook.create_sheet("Note")
        note["A1"] = "Notice"
        note["A2"] = notice
        note.column_dimensions["A"].width = 100
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _write_csv(rows: list[list[str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    for row in rows:
        writer.writerow([_neutralize_cell(value) for value in row])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def _write_table_docx(title: str, rows: list[list[str]], notice: str) -> bytes:
    payload = {
        "kind": "converted_document",
        "filename": title or "Converted table",
        "notice": notice,
        "blocks": [("table", rows)],
    }
    return render(payload, "docx")


def _neutralize_cell(value: str) -> str:
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def _sheet_name(value: str) -> str:
    name = re.sub(r"[\[\]:*?/\\]", "-", value).strip()[:31]
    return name or "Sheet"


def _column_widths(rows: list[list[str]]) -> list[int]:
    width = max((len(row) for row in rows), default=0)
    widths: list[int] = []
    for index in range(width):
        values = [row[index] if index < len(row) else "" for row in rows]
        longest = max((len(value) for value in values), default=0)
        widths.append(min(max(longest + 2, 10), 42))
    return widths


def _validate_edited_table(
    columns: list[str], rows: list[list[str]], settings: Settings
) -> list[list[str]]:
    if not columns:
        raise ConversionError("table_edit_invalid", "A table needs at least one column.")
    if len(columns) > CONVERT_MAX_COLUMNS:
        raise ConversionError("table_too_large", "The table has too many columns.")
    if len(rows) > CONVERT_MAX_ROWS:
        raise ConversionError("table_too_large", "The table has too many rows.")
    if any(not row or len(row) != len(columns) for row in rows):
        raise ConversionError(
            "table_edit_invalid",
            "Every row must have the same number of cells as the columns.",
        )
    cell_limit = CONVERT_MAX_CELL_CHARS
    if any(len(cell) > cell_limit for cell in columns):
        raise ConversionError("table_edit_invalid", "A column heading is too long.")
    if any(len(cell) > cell_limit for row in rows for cell in row):
        raise ConversionError("table_edit_invalid", "A table cell is too long.")
    return [list(columns)] + [list(row) for row in rows]


def _docx_text(data: bytes, settings: Settings) -> tuple[str, list[str]]:
    parsed = parse_document("converted.docx", data, settings)
    texts = [page.text for page in parsed.pages if page.text.strip()]
    warnings = [OCR_TEXT_WARNING] if any(page.source == "ocr" for page in parsed.pages) else []
    return "\n\n".join(texts), warnings


def _pdf_text(data: bytes, settings: Settings) -> tuple[str, list[str]]:
    parsed = parse_document("converted.pdf", data, settings)
    warnings = _ocr_pdf_pages(data, parsed, settings)
    texts = [page.text for page in parsed.pages if page.text.strip()]
    return "\n\n".join(texts), warnings


def _docx_blocks(
    data: bytes, settings: Settings
) -> tuple[list[tuple[str, Any]], list[str]]:
    parsed = parse_document("converted.docx", data, settings)
    blocks: list[tuple[str, Any]] = []
    warnings: list[str] = []
    ocr_noted = False
    for page in parsed.pages:
        text = page.text.strip()
        if not text:
            continue
        if page.tables:
            for table in page.tables:
                rows = table.get("rows")
                if isinstance(rows, list) and rows:
                    normalized = _normalize_optional_rows(rows, settings)
                    if normalized is not None:
                        blocks.append(("table", normalized))
            continue
        if page.source == "ocr" and not ocr_noted:
            blocks.append(("ocr", OCR_TEXT_WARNING))
            warnings.append(OCR_TEXT_WARNING)
            ocr_noted = True
        role = page.structure[0].role if page.structure else "normal"
        kind = {
            "title": "h2",
            "heading": "h3",
            "bullet": "bullet",
            "normal": "body",
        }.get(role, "body")
        blocks.append((kind, text))
    if not blocks:
        raise ConversionError(
            "no_extractable_text",
            "No readable text or tables were found in that Word file.",
        )
    return blocks, warnings


def _pdf_blocks(
    data: bytes, settings: Settings
) -> tuple[list[tuple[str, Any]], list[str]]:
    parsed = parse_document("converted.pdf", data, settings)
    warnings = _ocr_pdf_pages(data, parsed, settings)
    page_payloads = [
        {
            "kind": page.kind,
            "index": page.index,
            "text": page.text,
            "tables": [],
            "tables_checked": False,
        }
        for page in parsed.pages
    ]
    with pymupdf.open(stream=data, filetype="pdf") as document:
        detect_pdf_page_tables(document, page_payloads)
    blocks: list[tuple[str, Any]] = []
    ocr_noted = False
    for page, payload in zip(parsed.pages, page_payloads):
        text = page.text.strip()
        tables = payload.get("tables") or []
        if not text and not tables:
            continue
        if page.source == "ocr" and not ocr_noted:
            blocks.append(("ocr", OCR_TEXT_WARNING))
            ocr_noted = True
        if tables and _tables_map_to_text(text, tables):
            blocks.extend(source_blocks_for_conversion(text, "pdf", tables))
        else:
            if text:
                blocks.extend(source_blocks_for_conversion(text, "pdf", []))
            for table in tables:
                rows = table.get("rows")
                if isinstance(rows, list) and rows:
                    normalized = _normalize_optional_rows(rows, settings)
                    if normalized is not None:
                        blocks.append(("table", normalized))
    if not blocks:
        raise ConversionError(
            "no_extractable_text",
            "No readable text or tables were found in that PDF.",
        )
    return blocks, warnings


def _ocr_pdf_pages(
    data: bytes, parsed: Any, settings: Settings
) -> list[str]:
    pages = [
        page
        for page in parsed.pages
        if page.kind == "pdf_page"
        and page.needs_ocr
        and not page.text.strip()
    ]
    if not pages:
        return []
    warnings: list[str] = []
    limit = max(int(settings.ocr_page_limit), 0)
    attempted = 0
    with pymupdf.open(stream=data, filetype="pdf") as document:
        for page in pages:
            if attempted >= limit:
                warnings.append(
                    "Some scanned pages were not converted because the OCR page limit was reached."
                )
                break
            attempted += 1
            try:
                pixmap = document.load_page(page.index).get_pixmap(
                    dpi=settings.ocr_dpi
                )
                page.text = run_tesseract(
                    pixmap.tobytes("png"), ".png", settings
                )
                page.source = "ocr"
            except OcrError:
                warnings.append(
                    f"OCR could not read page {page.index + 1}."
                )
    if attempted and any(page.source == "ocr" for page in pages):
        warnings.append(OCR_TEXT_WARNING)
    return warnings


def _tables_map_to_text(text: str, tables: list[dict[str, Any]]) -> bool:
    cursor = 0
    for table in tables:
        rows = table.get("rows") if isinstance(table, dict) else None
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, list):
                return False
            for cell in row:
                if not isinstance(cell, str) or not cell:
                    continue
                start = text.find(cell, cursor)
                if start < 0:
                    return False
                cursor = start + len(cell)
    return True
