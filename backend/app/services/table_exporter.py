"""Render edited table results as CSV or a small string-only XLSX workbook."""

from __future__ import annotations

import csv
import io
import re
from docx import Document as DocxDocument
from docx.enum.section import WD_ORIENT
from docx.shared import Cm, Pt
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from app.services.table_results import TableResultRecord

_FORMAT_NAME = {"csv": "csv", "xlsx": "xlsx", "docx": "docx"}


def _normalized(record: TableResultRecord) -> tuple[list[str], list[list[str]]]:
    payload = record.payload
    columns = [str(value) for value in payload.get("columns") or []]
    rows = payload.get("rows") or []
    if not columns:
        raise ValueError("the result has no readable content to export")
    width = len(columns)
    normalized: list[list[str]] = []
    for row in rows:
        cells = [str(value) if value is not None else "" for value in row]
        normalized.append((cells + [""] * width)[:width])
    return columns, normalized


def build_csv(record: TableResultRecord) -> bytes:
    columns, rows = _normalized(record)
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(columns)
    writer.writerows(rows)
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def build_xlsx(record: TableResultRecord) -> bytes:
    columns, rows = _normalized(record)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Table"
    sheet.append(columns)
    for row in rows:
        sheet.append(row)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    sheet.freeze_panes = "A2"
    for index, heading in enumerate(columns, start=1):
        width = max(
            [len(heading)] + [len(row[index - 1]) for row in rows] + [8]
        )
        sheet.column_dimensions[get_column_letter(index)].width = min(
            max(width + 2, 10), 42
        )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_docx(record: TableResultRecord) -> bytes:
    columns, rows = _normalized(record)
    document = DocxDocument()
    section = document.sections[0]
    section.left_margin = Cm(1.6)
    section.right_margin = Cm(1.6)
    section.top_margin = Cm(1.6)
    section.bottom_margin = Cm(1.6)
    if len(columns) > 5:
        section.orientation = WD_ORIENT.LANDSCAPE
        width, height = section.page_width, section.page_height
        if width < height:
            section.page_width, section.page_height = height, width

    table = document.add_table(rows=1, cols=len(columns))
    table.style = "Table Grid"
    table.autofit = True
    for index, heading in enumerate(columns):
        paragraph = table.rows[0].cells[index].paragraphs[0]
        run = paragraph.add_run(heading)
        run.bold = True
        run.font.size = Pt(10.5)
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            run = cells[index].paragraphs[0].add_run(value)
            run.font.size = Pt(10.5)

    note = document.add_paragraph()
    run = note.add_run(
        "Table read from an image, not copied source text. Review every cell."
    )
    run.italic = True
    run.font.size = Pt(9)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_table_file(
    record: TableResultRecord, file_format: str
) -> tuple[bytes, str, str]:
    if file_format not in _FORMAT_NAME:
        raise ValueError("unsupported format")
    safe_source = re.sub(
        r"[^A-Za-z0-9._-]+", "-", record.payload.get("name") or ""
    ).strip("-_.")
    base = safe_source or f"table-{record.result_id[:8]}"
    if file_format == "csv":
        return build_csv(record), f"{base}.csv", "csv"
    if file_format == "xlsx":
        return build_xlsx(record), f"{base}.xlsx", "xlsx"
    return build_docx(record), f"{base}.docx", "docx"
