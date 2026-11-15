from __future__ import annotations

import io

import pymupdf
from docx import Document as DocxDocument

from app.config import get_settings
from app.services.ocr import OcrFailed
from app.services.page_ocr import ensure_pdf_pages_ocr
from app.services.storage import storage
from tests.helpers import build_pdf, build_png

PDF_CT = "application/pdf"


def _image_body_pdf() -> bytes:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "Other page has native lecture text.")
    page = document.new_page()
    page.insert_text((72, 72), "VAGINAL CYTOLOGY", fontsize=12)
    page.insert_image(pymupdf.Rect(50, 120, 545, 790), stream=build_png())
    document.new_page().insert_text((72, 72), "An unrelated native lecture page.")
    data = document.tobytes()
    document.close()
    return data


def _upload(client, data: bytes, filename: str = "lecture.pdf") -> dict:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (filename, data, PDF_CT)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _enable_ocr(monkeypatch, *, page_limit: int = 10, request_limit: int = 5, budget: float = 20.0):
    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_page_limit", page_limit)
    monkeypatch.setattr(settings, "ocr_request_page_limit", request_limit)
    monkeypatch.setattr(settings, "ocr_request_time_budget_s", budget)
    monkeypatch.setattr(settings, "ocr_dpi", 72)
    monkeypatch.setattr(settings, "ocr_timeout_s", 5)
    return settings


def test_image_body_topic_ocr_is_only_for_match_and_cached(client, monkeypatch):
    _enable_ocr(monkeypatch)
    upload = _upload(client, _image_body_pdf())
    document_id = upload["document_id"]
    pages = storage.pages_for(document_id)
    assert [page["ocr_status"] for page in pages] == ["none", "pending", "none"]

    calls: list[bytes] = []

    def fake_ocr(image: bytes, _suffix: str, _settings) -> str:
        calls.append(image)
        return "VAGINAL CYTOLOGY\nCervical cells and epithelial features from the slide body."

    monkeypatch.setattr("app.services.page_ocr.run_tesseract", fake_ocr)
    source = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id, "jottings_text": "VAGINAL CYTOLOGY"},
    )
    assert source.status_code == 201, source.text
    endpoint = f"/api/v1/topic-sources/{source.json()['source_id']}/topic-review"

    first = client.post(endpoint, json={"topics": ["VAGINAL CYTOLOGY"]})
    assert first.status_code == 200, first.text
    match = first.json()["topics"][0]["matches"][0]
    assert match["anchor"]["page"] == 2
    assert "Cervical cells and epithelial features" in match["passage"]
    assert match["has_page_image"] is True
    assert len(calls) == 1

    second = client.post(endpoint, json={"topics": ["VAGINAL CYTOLOGY"]})
    assert second.status_code == 200, second.text
    assert "Cervical cells and epithelial features" in second.json()["topics"][0]["matches"][0]["passage"]
    assert len(calls) == 1
    assert [page["ocr_status"] for page in storage.pages_for(document_id)] == ["none", "done", "none"]


def test_pdf_upload_timing_has_no_ocr_and_ingest_never_calls_runner(client, monkeypatch):
    calls: list[bytes] = []
    monkeypatch.setattr(get_settings(), "ocr_page_limit", 10)

    def fake_ocr(image: bytes, _suffix: str, _settings) -> str:
        calls.append(image)
        return "unexpected OCR"

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake_ocr)
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("slides.pdf", _image_body_pdf(), PDF_CT)},
    )

    assert response.status_code == 201, response.text
    timing = response.headers["server-timing"]
    assert "parse;dur=" in timing
    assert "store;dur=" in timing
    assert "ocr;dur=" not in timing
    assert calls == []


def test_53_page_pdf_upload_timing_stays_under_ten_seconds(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "max_pages", 60)
    monkeypatch.setattr(settings, "ocr_page_limit", 10)
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "53-pages.pdf",
                build_pdf([f"Native lecture page {number} has readable text." for number in range(1, 54)]),
                PDF_CT,
            )
        },
    )

    assert response.status_code == 201, response.text
    timing = response.headers["server-timing"]
    total_ms = float(next(part for part in timing.split(", ") if part.startswith("total;dur=")).split("=", 1)[1])
    assert "ocr;dur=" not in timing
    assert total_ms < 10_000
    print(f"Synthetic 53-page PDF upload Server-Timing total: {total_ms:.1f} ms")


def test_request_page_cap_warns_with_remaining_page_numbers(client, monkeypatch):
    _enable_ocr(monkeypatch, page_limit=10, request_limit=1)
    upload = _upload(client, build_pdf(["", "", ""]))
    calls: list[bytes] = []

    def fake_ocr(image: bytes, _suffix: str, _settings) -> str:
        calls.append(image)
        return "Recovered body text on this page."

    monkeypatch.setattr("app.services.page_ocr.run_tesseract", fake_ocr)
    response = client.post(
        f"/api/v1/documents/{upload['document_id']}/ranges",
        json={"selector": "pages 1-3"},
    )

    assert response.status_code == 200, response.text
    warnings = response.json()["warnings"]
    warning = next(item for item in warnings if item["code"] == "ocr_pending_pages")
    assert "2, 3" in warning["message"]
    assert len(calls) == 1
    assert "Recovered body text" in response.json()["sections"][0]["raw_text"]


def test_time_budget_warns_with_remaining_page_numbers(client, monkeypatch):
    settings = _enable_ocr(monkeypatch, request_limit=10, budget=1.1)
    upload = _upload(client, build_pdf(["", "", ""]))
    record = storage.get(upload["document_id"])
    assert record is not None
    now = [0.0]

    def fake_ocr(_image: bytes, _suffix: str, _settings) -> str:
        now[0] += 1.2
        return "Recovered body text on this page."

    report = ensure_pdf_pages_ocr(
        record,
        [1, 2, 3],
        settings,
        ocr_runner=fake_ocr,
        clock=lambda: now[0],
    )
    assert report.completed_pages == [1]
    assert report.pending_pages == [2, 3]
    refreshed = storage.get(record.document_id)
    assert refreshed is not None
    assert "Recovered body text" in refreshed.sections[0]["raw_text"]
    warning = next(item for item in report.warnings if item["code"] == "ocr_pending_pages")
    assert "2, 3" in warning["message"]


def test_selected_page_pdf_and_docx_exports_reuse_cached_ocr(client, monkeypatch):
    _enable_ocr(monkeypatch)
    upload = _upload(client, _image_body_pdf())
    calls: list[bytes] = []

    def fake_ocr(image: bytes, _suffix: str, _settings) -> str:
        calls.append(image)
        return "VAGINAL CYTOLOGY\nCervical cells and epithelial features from the slide body."

    monkeypatch.setattr("app.services.page_ocr.run_tesseract", fake_ocr)
    endpoint = f"/api/v1/documents/{upload['document_id']}/extract/export"
    params = {"type": "pages", "spec": "2", "mode": "source_passages"}
    pdf_response = client.get(endpoint, params={**params, "format": "pdf"})
    assert pdf_response.status_code == 200, pdf_response.text
    pdf = pymupdf.open(stream=pdf_response.content, filetype="pdf")
    pdf_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    assert "Cervical cells and epithelial features" in pdf_text

    docx_response = client.get(endpoint, params={**params, "format": "docx"})
    assert docx_response.status_code == 200, docx_response.text
    docx = DocxDocument(io.BytesIO(docx_response.content))
    docx_text = "\n".join(paragraph.text for paragraph in docx.paragraphs)
    assert "Cervical cells and epithelial features" in docx_text
    assert len(calls) == 1


def test_ocr_failure_keeps_native_text_and_marks_page_failed(client, monkeypatch):
    _enable_ocr(monkeypatch)
    upload = _upload(client, _image_body_pdf())
    document_id = upload["document_id"]
    source = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id, "jottings_text": "VAGINAL CYTOLOGY"},
    )

    def fail(_image: bytes, _suffix: str, _settings) -> str:
        raise OcrFailed("test failure")

    monkeypatch.setattr("app.services.page_ocr.run_tesseract", fail)
    response = client.post(
        f"/api/v1/topic-sources/{source.json()['source_id']}/topic-review",
        json={"topics": ["VAGINAL CYTOLOGY"]},
    )

    assert response.status_code == 200, response.text
    match = response.json()["topics"][0]["matches"][0]
    assert match["passage"].strip() == "VAGINAL CYTOLOGY"
    assert any(item["code"] == "ocr_failed" for item in response.json()["warnings"])
    assert storage.pages_for(document_id)[1]["ocr_status"] == "failed"


def test_page_image_endpoint_png_owner_and_invalid_page(client, make_user):
    upload = _upload(client, build_pdf(["Page image endpoint test text."]))
    document_id = upload["document_id"]

    image = client.get(f"/api/v1/documents/{document_id}/pages/1/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG\r\n\x1a\n")

    other = make_user()
    denied = client.get(
        f"/api/v1/documents/{document_id}/pages/1/image",
        headers=other["headers"],
    )
    assert denied.status_code == 404

    missing = client.get(f"/api/v1/documents/{document_id}/pages/2/image")
    assert missing.status_code == 404
