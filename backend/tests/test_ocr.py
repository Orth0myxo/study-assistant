from __future__ import annotations

from app.config import get_settings
from app.services.ocr import OcrFailed, OcrTimeout, OcrUnavailable
from app.services.storage import storage
from tests.helpers import build_docx_with_images, build_pdf, build_png

PDF_CT = "application/pdf"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _upload(client, filename: str, data: bytes, content_type: str):
    return client.post(
        "/api/v1/documents",
        files={"file": (filename, data, content_type)},
    )


def _enable_ocr(monkeypatch, limit: int = 10) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_page_limit", limit)
    monkeypatch.setattr(settings, "ocr_dpi", 72)
    monkeypatch.setattr(settings, "ocr_timeout_s", 5)


def _codes(body: dict) -> list[str]:
    return [warning["code"] for warning in body["warnings"]]


def test_ocr_extracts_scanned_pdf_pages(client, monkeypatch):
    _enable_ocr(monkeypatch)
    responses = iter(
        [
            "First scanned page contains OCR lecture text.",
            "Second scanned page contains OCR lecture text.",
        ]
    )
    calls: list[str] = []

    def fake(image: bytes, suffix: str, settings) -> str:
        calls.append(suffix)
        return next(responses)

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    response = _upload(client, "scan.pdf", build_pdf(["", ""]), PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "ok"
    assert body["extraction"]["text_extractable"] is True
    assert body["extraction"]["section_count"] == 2
    assert body["warnings"] == []
    assert calls == [".png", ".png"]

    record = storage.get(body["document_id"])
    assert record is not None
    assert [section["anchor"]["page"] for section in record.sections] == [1, 2]
    assert "First scanned" in record.sections[0]["raw_text"]
    assert "Second scanned" in record.sections[1]["raw_text"]


def test_ocr_runs_only_for_low_text_pages(client, monkeypatch):
    _enable_ocr(monkeypatch)
    calls: list[str] = []

    def fake(image: bytes, suffix: str, settings) -> str:
        calls.append(suffix)
        return "OCR text recovered from the scanned page."

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    data = build_pdf(["Native text on this page is already sufficient.", ""])
    response = _upload(client, "mixed.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "ok"
    assert body["extraction"]["section_count"] == 2
    assert len(calls) == 1

    record = storage.get(body["document_id"])
    assert record is not None
    assert [section["anchor"]["page"] for section in record.sections] == [1, 2]


def test_ocr_page_limit_reports_remaining_pages(client, monkeypatch):
    _enable_ocr(monkeypatch, limit=1)

    def fake(image: bytes, suffix: str, settings) -> str:
        return "OCR text recovered from the scanned page."

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    response = _upload(client, "scan.pdf", build_pdf(["", "", ""]), PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["section_count"] == 1
    assert _codes(body) == ["partial_extraction", "ocr_page_limit_reached"]
    assert "2 page(s) were not processed" in body["warnings"][1]["message"]


def test_ocr_unavailable_is_reported(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def fake(image: bytes, suffix: str, settings) -> str:
        raise OcrUnavailable("missing")

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    response = _upload(client, "scan.pdf", build_pdf(["", ""]), PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert body["extraction"]["text_extractable"] is False
    assert _codes(body) == ["scanned_pdf_no_text", "ocr_unavailable"]
    assert "2 scanned page(s) could not be read" in body["warnings"][1]["message"]


def test_ocr_timeout_is_reported(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def fake(image: bytes, suffix: str, settings) -> str:
        raise OcrTimeout("slow")

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    response = _upload(client, "scan.pdf", build_pdf(["", ""]), PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert _codes(body) == ["scanned_pdf_no_text", "ocr_timeout"]
    assert "2 page(s): 1, 2" in body["warnings"][1]["message"]


def test_ocr_failure_is_reported(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def fake(image: bytes, suffix: str, settings) -> str:
        raise OcrFailed("bad image")

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    response = _upload(client, "scan.pdf", build_pdf([""]), PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert _codes(body) == ["scanned_pdf_no_text", "ocr_failed"]
    assert "1 page(s): 1" in body["warnings"][1]["message"]


def test_ocr_empty_result_is_reported(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def fake(image: bytes, suffix: str, settings) -> str:
        return "   "

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    response = _upload(client, "scan.pdf", build_pdf([""]), PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert _codes(body) == ["scanned_pdf_no_text", "ocr_no_text"]


def test_ocr_disabled_does_not_call_runner(client, monkeypatch):
    _enable_ocr(monkeypatch, limit=0)
    calls: list[bytes] = []

    def fake(image: bytes, suffix: str, settings) -> str:
        calls.append(image)
        return "should not be used"

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    response = _upload(client, "scan.pdf", build_pdf([""]), PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert _codes(body) == ["scanned_pdf_no_text"]
    assert calls == []


def test_ocr_partial_pdf_with_native_text(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def fake(image: bytes, suffix: str, settings) -> str:
        raise OcrTimeout("slow")

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    data = build_pdf(["Native text on this page is already sufficient.", ""])
    response = _upload(client, "mixed.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["text_extractable"] is True
    assert body["extraction"]["section_count"] == 1
    assert _codes(body) == ["partial_extraction", "ocr_timeout"]


def test_docx_embedded_image_is_ocrd(client, monkeypatch):
    _enable_ocr(monkeypatch)
    calls: list[str] = []

    def fake(image: bytes, suffix: str, settings) -> str:
        calls.append(suffix)
        return "Diagram labels: hemoglobin and hematocrit."

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    data = build_docx_with_images(
        "Diagrams",
        "Text before the diagram.",
        [build_png()],
    )
    response = _upload(client, "notes.docx", data, DOCX_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "ok"
    assert body["extraction"]["text_extractable"] is True
    assert body["extraction"]["section_count"] == 3
    assert body["warnings"] == []
    assert calls == [".png"]

    record = storage.get(body["document_id"])
    assert record is not None
    assert [section["anchor"]["paragraph_index"] for section in record.sections] == [
        0,
        1,
        2,
    ]
    assert all(
        section["anchor"]["section_label"] == "Diagrams"
        for section in record.sections
    )
    assert "Diagram labels" in record.sections[2]["raw_text"]


def test_docx_image_ocr_unavailable_marks_partial(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def fake(image: bytes, suffix: str, settings) -> str:
        raise OcrUnavailable("missing")

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    data = build_docx_with_images(
        "Diagrams",
        "Text before the diagram.",
        [build_png()],
    )
    response = _upload(client, "notes.docx", data, DOCX_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["text_extractable"] is True
    assert body["extraction"]["section_count"] == 2
    assert _codes(body) == ["ocr_unavailable"]
    assert "1 embedded image(s)" in body["warnings"][0]["message"]


def test_docx_image_limit_reports_remaining_images(client, monkeypatch):
    _enable_ocr(monkeypatch, limit=1)
    calls: list[bytes] = []

    def fake(image: bytes, suffix: str, settings) -> str:
        calls.append(image)
        return "OCR text from an embedded image."

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    data = build_docx_with_images(
        "Diagrams",
        "Text before the diagrams.",
        [build_png(64), build_png(80)],
    )
    response = _upload(client, "notes.docx", data, DOCX_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["section_count"] == 3
    assert _codes(body) == ["ocr_image_limit_reached"]
    assert "1 image(s) were not processed" in body["warnings"][0]["message"]
    assert len(calls) == 1


def test_docx_duplicate_images_are_ocrd_once(client, monkeypatch):
    _enable_ocr(monkeypatch)
    calls: list[bytes] = []

    def fake(image: bytes, suffix: str, settings) -> str:
        calls.append(image)
        return "OCR text from an embedded image."

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    image = build_png()
    data = build_docx_with_images(
        "Diagrams",
        "Text before the diagrams.",
        [image, image],
    )
    response = _upload(client, "notes.docx", data, DOCX_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "ok"
    assert body["extraction"]["section_count"] == 3
    assert body["warnings"] == []
    assert len(calls) == 1


def test_docx_image_only_unavailable_is_no_text(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def fake(image: bytes, suffix: str, settings) -> str:
        raise OcrUnavailable("missing")

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    data = build_docx_with_images(None, "", [build_png()])
    response = _upload(client, "image.docx", data, DOCX_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert body["extraction"]["text_extractable"] is False
    assert body["extraction"]["section_count"] == 0
    assert _codes(body) == ["no_extractable_text", "ocr_unavailable"]


def test_docx_image_timeout_marks_partial(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def fake(image: bytes, suffix: str, settings) -> str:
        raise OcrTimeout("slow")

    monkeypatch.setattr("app.services.document_parser.run_tesseract", fake)

    data = build_docx_with_images(
        "Diagrams",
        "Text before the diagram.",
        [build_png()],
    )
    response = _upload(client, "notes.docx", data, DOCX_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert _codes(body) == ["ocr_timeout"]
