from __future__ import annotations

import io
import logging
import time

import pymupdf
from docx import Document as DocxDocument
from openpyxl import Workbook, load_workbook

from app.config import get_settings
from app.services import converter


def _start(client, conversion_id: str, *, filename=None, content=None, text=None, sheet=None, use_ai=False):
    data = {
        "conversion_id": conversion_id,
        "use_ai": "true" if use_ai else "false",
    }
    if text is not None:
        data["text"] = text
    if sheet is not None:
        data["sheet"] = sheet
    files = None
    if filename is not None and content is not None:
        files = {"file": (filename, content, "application/octet-stream")}
    response = client.post("/api/v1/convert/jobs", data=data, files=files)
    assert response.status_code == 202, response.text
    return response.json()


def _wait(client, job_id: str):
    for _ in range(600):
        response = client.get(f"/api/v1/convert/jobs/{job_id}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"completed", "failed"}:
            return job
        time.sleep(0.02)
    raise AssertionError("conversion did not finish")


def _download(client, job: dict) -> bytes:
    response = client.get(f"/api/v1/convert/jobs/{job['job_id']}/file")
    assert response.status_code == 200, response.text
    return response.content


def _workbook_bytes(rows_by_sheet: dict[str, list[list[str]]]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in rows_by_sheet.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_conversion_registry_lists_all_in_scope_paths(client):
    response = client.get("/api/v1/convert/formats")
    assert response.status_code == 200, response.text
    ids = {
        option["conversion_id"]
        for item in response.json()["inputs"]
        for option in item["conversions"]
    }
    assert {
        "csv-to-xlsx",
        "xlsx-to-csv",
        "csv-to-docx",
        "docx-tables-to-xlsx",
        "text-to-pdf",
        "docx-to-pdf",
        "pdf-to-docx",
        "summary-text-to-xlsx",
        "summary-pdf-to-docx",
    } <= ids
    limits = response.json()["limits"]
    assert limits["max_rows"] == 5000
    assert limits["max_columns"] == 200


def test_csv_to_xlsx_preserves_cells_and_neutralizes_formulas(client):
    job = _start(
        client,
        "csv-to-xlsx",
        filename="grades.csv",
        content=b"name,score\nAda,10\nGrader,=1+1\n",
    )
    done = _wait(client, job["job_id"])
    assert done["status"] == "completed", done
    assert done["file"]["name"].endswith(".xlsx")

    workbook = load_workbook(io.BytesIO(_download(client, done)))
    sheet = workbook.active
    assert sheet["A1"].value == "name"
    assert sheet["B2"].value == "10"
    assert sheet["B3"].value == "'=1+1"


def test_formula_neutralization_covers_spreadsheet_prefixes():
    for value in ("=1+1", "+1", "-1", "@cmd", "\t=1+1", "\r=1+1"):
        assert converter._neutralize_cell(value) == f"'{value}"
    assert converter._neutralize_cell("001") == "001"


def test_csv_handles_semicolons_quoted_newlines_and_leading_zeros(client):
    job = _start(
        client,
        "csv-to-xlsx",
        filename="contacts.txt.csv",
        content=(
            b"id;name;note\n"
            b"001;Ada;\"line one\nline two\"\n"
            b"002;Zo\xc3\xab;caf\xc3\xa9\n"
        ),
    )
    done = _wait(client, job["job_id"])
    assert done["status"] == "completed", done
    workbook = load_workbook(io.BytesIO(_download(client, done)))
    sheet = workbook.active
    assert sheet["A2"].value == "001"
    assert sheet["C2"].value == "line one\nline two"
    assert sheet["A3"].value == "002"
    assert sheet["B3"].value == "Zo\u00eb"
    assert sheet["C3"].value == "caf\u00e9"


def test_xlsx_to_csv_requires_a_sheet_choice_then_converts_it(client):
    content = _workbook_bytes(
        {
            "First": [["a", "b"], ["1", "2"]],
            "Second": [["x", "y"], ["3", "4"]],
        }
    )
    job = _start(client, "xlsx-to-csv", filename="book.xlsx", content=content)
    failed = _wait(client, job["job_id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "sheet_choice_required"
    assert failed["error"]["details"]["sheets"] == ["First", "Second"]

    retry = _start(
        client,
        "xlsx-to-csv",
        filename="book.xlsx",
        content=content,
        sheet="Second",
    )
    done = _wait(client, retry["job_id"])
    assert done["status"] == "completed", done
    csv_text = _download(client, done).decode("utf-8-sig")
    assert csv_text.splitlines() == ["x,y", "3,4"]


def test_word_tables_to_excel_keeps_each_table_on_its_own_sheet(client):
    document = DocxDocument()
    document.add_paragraph("Tables")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Topic"
    table.cell(0, 1).text = "Detail"
    table.cell(1, 0).text = "Calvin cycle"
    table.cell(1, 1).text = "Carbon fixation"
    blank = document.add_table(rows=1, cols=2)
    blank.cell(0, 0).text = "\n"
    blank.cell(0, 1).text = "\n"
    buffer = io.BytesIO()
    document.save(buffer)

    job = _start(
        client,
        "docx-tables-to-xlsx",
        filename="notes.docx",
        content=buffer.getvalue(),
    )
    done = _wait(client, job["job_id"])
    assert done["status"] == "completed", done
    workbook = load_workbook(io.BytesIO(_download(client, done)))
    assert workbook.sheetnames == ["Table"]
    sheet = workbook["Table"]
    assert sheet["A1"].value == "Topic"
    assert sheet["A2"].value == "Calvin cycle"
    assert sheet["B2"].value == "Carbon fixation"


def test_text_to_pdf_and_pdf_to_word(client):
    pdf_job = _start(
        client,
        "text-to-pdf",
        text="Photosynthesis\n\nPlants use light energy to make sugars.",
    )
    pdf_done = _wait(client, pdf_job["job_id"])
    assert pdf_done["status"] == "completed", pdf_done
    pdf_bytes = _download(client, pdf_done)
    assert pdf_bytes.startswith(b"%PDF-")

    docx_job = _start(
        client,
        "pdf-to-docx",
        filename="source.pdf",
        content=pdf_bytes,
    )
    docx_done = _wait(client, docx_job["job_id"])
    assert docx_done["status"] == "completed", docx_done
    document = DocxDocument(io.BytesIO(_download(client, docx_done)))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "Photosynthesis" in text
    assert "Plants use light energy" in text


def test_docx_to_pdf_keeps_text_and_table(client):
    document = DocxDocument()
    document.add_heading("Respiration", level=1)
    document.add_paragraph("Cells release energy from glucose.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Stage"
    table.cell(0, 1).text = "Location"
    table.cell(1, 0).text = "Glycolysis"
    table.cell(1, 1).text = "Cytoplasm"
    buffer = io.BytesIO()
    document.save(buffer)

    job = _start(
        client,
        "docx-to-pdf",
        filename="respiration.docx",
        content=buffer.getvalue(),
    )
    done = _wait(client, job["job_id"])
    assert done["status"] == "completed", done
    pdf_bytes = _download(client, done)
    assert pdf_bytes.startswith(b"%PDF-")
    exported = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    exported_text = "\n".join(page.get_text() for page in exported)
    exported.close()
    assert "Respiration" in exported_text
    assert "Cytoplasm" in exported_text


def test_summary_table_is_editable_before_export(client, monkeypatch):
    class FakeAiClient:
        def summarize_table(self, **_: object):
            return {
                "title": "Energy summary",
                "columns": ["Topic", "Detail"],
                "rows": [["Photosynthesis", "Makes sugars"]],
            }

        def close(self):
            pass

    monkeypatch.setattr(
        converter,
        "build_ai_client",
        lambda settings, owner_id=None: FakeAiClient(),
    )
    job = _start(
        client,
        "summary-text-to-xlsx",
        text="Photosynthesis makes sugars from light energy.",
        use_ai=True,
    )
    done = _wait(client, job["job_id"])
    assert done["status"] == "completed", done
    assert done["editable"] is True
    assert done["file"] is None
    assert done["table"]["columns"] == ["Topic", "Detail"]

    response = client.post(
        f"/api/v1/convert/jobs/{done['job_id']}/export",
        json={
            "format": "xlsx",
            "title": "Edited summary",
            "columns": ["Topic", "Detail"],
            "rows": [["Photosynthesis", "Uses light energy"]],
        },
    )
    assert response.status_code == 200, response.text
    exported = response.json()
    assert exported["status"] == "completed"
    assert exported["file"]["name"].endswith(".xlsx")
    workbook = load_workbook(io.BytesIO(_download(client, exported)))
    sheet = workbook.active
    assert sheet["A1"].value == "Topic"
    assert sheet["B2"].value == "Uses light energy"
    assert workbook["Note"]["A2"].value.startswith("AI-generated summary table")

    docx_response = client.post(
        f"/api/v1/convert/jobs/{done['job_id']}/export",
        json={
            "format": "docx",
            "title": "Edited summary",
            "columns": ["Topic", "Detail"],
            "rows": [["Photosynthesis", "Uses light energy"]],
        },
    )
    assert docx_response.status_code == 200, docx_response.text
    docx_done = docx_response.json()
    assert docx_done["file"]["name"].endswith(".docx")
    document = DocxDocument(io.BytesIO(_download(client, docx_done)))
    assert document.tables[0].cell(0, 0).text == "Topic"
    assert document.tables[0].cell(1, 1).text == "Uses light energy"
    docx_text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert "AI-generated summary table" in docx_text


def test_summary_table_requires_explicit_ai_consent(client):
    response = client.post(
        "/api/v1/convert/jobs",
        data={
            "conversion_id": "summary-text-to-xlsx",
            "text": "A source",
            "use_ai": "false",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "ai_consent_required"


def test_pdf_with_text_can_be_converted(client):
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Osmosis moves water across a membrane.")
    pdf_bytes = document.tobytes()
    document.close()

    job = _start(
        client,
        "pdf-to-docx",
        filename="osmosis.pdf",
        content=pdf_bytes,
    )
    done = _wait(client, job["job_id"])
    assert done["status"] == "completed", done
    text = "\n".join(
        paragraph.text
        for paragraph in DocxDocument(io.BytesIO(_download(client, done))).paragraphs
    )
    assert "Osmosis moves water" in text


def test_empty_oversized_and_unreadable_inputs_are_rejected(client, monkeypatch):
    empty = client.post(
        "/api/v1/convert/jobs",
        data={"conversion_id": "csv-to-xlsx"},
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "empty_file"

    unreadable = client.post(
        "/api/v1/convert/jobs",
        data={"conversion_id": "csv-to-xlsx"},
        files={"file": ("bad.csv", b"\x00\x01", "text/csv")},
    )
    assert unreadable.status_code == 415
    assert unreadable.json()["error"]["code"] == "file_format_mismatch"

    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    oversized = client.post(
        "/api/v1/convert/jobs",
        data={"conversion_id": "csv-to-xlsx"},
        files={"file": ("large.csv", b"a,b\n" + b"x" * (1024 * 1024), "text/csv")},
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "file_too_large"


def test_conversion_logs_metadata_without_filename_or_cells(client, caplog):
    caplog.set_level(logging.INFO, logger="app.services.converter")
    job = _start(
        client,
        "csv-to-xlsx",
        filename="private-lecture.csv",
        content=b"topic,detail\nPrivate Cell,secret value\n",
    )
    done = _wait(client, job["job_id"])
    assert done["status"] == "completed", done
    for _ in range(100):
        if "conversion done" in caplog.text:
            break
        time.sleep(0.02)
    logged = caplog.text
    assert "conversion done" in logged
    assert "input_bytes=" in logged and "output_bytes=" in logged
    assert "seconds=" in logged
    assert "private-lecture" not in logged
    assert "Private Cell" not in logged
    assert "secret value" not in logged


def test_conversion_jobs_are_owner_scoped(client, make_user):
    job = _start(
        client,
        "csv-to-xlsx",
        filename="owner.csv",
        content=b"name,score\nAda,10\n",
    )
    other = make_user()
    status = client.get(
        f"/api/v1/convert/jobs/{job['job_id']}", headers=other["headers"]
    )
    assert status.status_code == 404
    assert status.json()["error"]["code"] == "conversion_not_found"
    file_response = client.get(
        f"/api/v1/convert/jobs/{job['job_id']}/file", headers=other["headers"]
    )
    assert file_response.status_code == 404
