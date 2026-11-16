from __future__ import annotations

import io
import logging
import re

import pymupdf
import pytest
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


def _image_only_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(pymupdf.Rect(50, 50, 545, 790), stream=build_png(256))
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


def test_ocr_progress_endpoint_reports_and_resumes_batches(client, monkeypatch):
    _enable_ocr(monkeypatch, page_limit=10, request_limit=1)
    upload = _upload(client, build_pdf(["", "", ""]))
    document_id = upload["document_id"]
    calls: list[bytes] = []

    def fake_ocr(image: bytes, _suffix: str, _settings) -> str:
        calls.append(image)
        return "Recovered scanned page text."

    monkeypatch.setattr("app.services.page_ocr.run_tesseract", fake_ocr)

    progress = client.get(f"/api/v1/documents/{document_id}/ocr")
    assert progress.status_code == 200, progress.text
    body = progress.json()
    assert body["status"] == "pending"
    assert body["pending_pages"] == [1, 2, 3]
    assert body["completed_pages"] == 0
    assert calls == []

    first = client.post(f"/api/v1/documents/{document_id}/ocr", json={})
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["attempted_pages"] == [1]
    assert first_body["completed_this_batch"] == [1]
    assert first_body["pending_pages"] == [2, 3]
    assert len(calls) == 1

    second = client.post(
        f"/api/v1/documents/{document_id}/ocr", json={"pages": [2, 3]}
    )
    assert second.status_code == 200, second.text
    second_body = second.json()
    assert second_body["attempted_pages"] == [2]
    assert second_body["pending_pages"] == [3]

    third = client.post(
        f"/api/v1/documents/{document_id}/ocr", json={"pages": [3]}
    )
    assert third.status_code == 200, third.text
    third_body = third.json()
    assert third_body["status"] == "complete"
    assert third_body["pending_pages"] == []
    assert third_body["completed_pages"] == 1
    assert len(calls) == 3

    final = client.get(f"/api/v1/documents/{document_id}/ocr").json()
    assert final["status"] == "complete"
    assert final["completed_pages"] == 3


def test_whole_study_refuses_partial_ocr_until_prepared(client, monkeypatch):
    _enable_ocr(monkeypatch, page_limit=10, request_limit=1)
    upload = _upload(client, build_pdf(["", "", ""]))
    document_id = upload["document_id"]
    monkeypatch.setattr(
        "app.services.page_ocr.run_tesseract",
        lambda image, suffix, settings: "Recovered scanned lecture text.",
    )

    blocked = client.post(
        "/api/v1/study-jobs",
        json={"document_id": document_id, "mode": "extracted_text_only"},
    )
    assert blocked.status_code == 409, blocked.text
    assert blocked.json()["error"]["code"] == "ocr_incomplete"
    assert blocked.json()["error"]["details"]["pending_pages"] == [2, 3]

    for _ in range(5):
        prepared = client.post(f"/api/v1/documents/{document_id}/ocr", json={})
        assert prepared.status_code == 200, prepared.text
        if prepared.json()["status"] != "pending":
            break
    assert prepared.json()["status"] == "complete"

    study = client.post(
        "/api/v1/study-jobs",
        json={"document_id": document_id, "mode": "extracted_text_only"},
    )
    assert study.status_code == 200, study.text
    assert study.json()["status"] == "completed"
    assert "Recovered scanned lecture text." in study.json()["items"][0]["text"]


def test_topic_review_ocrs_pages_before_declaring_no_material(client, monkeypatch):
    _enable_ocr(monkeypatch)
    upload = _upload(client, _image_body_pdf())
    document_id = upload["document_id"]
    monkeypatch.setattr(
        "app.services.page_ocr.run_tesseract",
        lambda image, suffix, settings: (
            "VAGINAL CYTOLOGY\nCervical cells and epithelial features from the slide body."
        ),
    )
    source = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id, "jottings_text": "Slide body"},
    )
    assert source.status_code == 201, source.text

    review = client.post(
        f"/api/v1/topic-sources/{source.json()['source_id']}/topic-review",
        json={"topics": ["Cervical cells and epithelial features"]},
    )
    assert review.status_code == 200, review.text
    body = review.json()
    assert body["source_material"]["status"] == "available"
    topic = body["topics"][0]
    assert topic["status"] != "unmatched"
    assert topic["matches"][0]["anchor"]["page"] == 2
    assert "Cervical cells and epithelial features" in topic["matches"][0]["passage"]


def test_topic_review_recovers_text_from_fully_scanned_pdf(client, monkeypatch):
    _enable_ocr(monkeypatch, page_limit=10)
    upload = _upload(client, build_pdf(["", ""]))
    document_id = upload["document_id"]
    monkeypatch.setattr(
        "app.services.page_ocr.run_tesseract",
        lambda image, suffix, settings: (
            "Photosynthesis converts light energy into chemical energy."
        ),
    )
    source = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id, "jottings_text": "Photosynthesis"},
    )
    assert source.status_code == 201, source.text

    review = client.post(
        f"/api/v1/topic-sources/{source.json()['source_id']}/topic-review",
        json={"topics": ["Photosynthesis"]},
    )
    assert review.status_code == 200, review.text
    body = review.json()
    assert body["source_material"]["status"] == "available"
    assert body["topics"][0]["status"] != "unmatched"
    assert body["topics"][0]["matches"][0]["anchor"]["page"] == 1
    assert "Photosynthesis converts" in body["topics"][0]["matches"][0]["passage"]

def test_failed_ocr_page_retries_then_succeeds(client, monkeypatch):
    _enable_ocr(monkeypatch, page_limit=10, request_limit=5)
    upload = _upload(client, build_pdf([""]))
    document_id = upload["document_id"]
    calls: list[int] = []

    def flaky_ocr(image: bytes, _suffix: str, _settings) -> str:
        calls.append(1)
        if len(calls) < 3:
            raise OcrFailed("transient OCR failure")
        return "Recovered after retry."

    monkeypatch.setattr("app.services.page_ocr.run_tesseract", flaky_ocr)

    first = client.post(
        f"/api/v1/documents/{document_id}/ocr", json={"pages": [1]}
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["status"] == "pending"
    assert first_body["pending_pages"] == [1]
    assert first_body["failed_pages"] == []
    assert first_body["failed_this_batch"] == [1]

    second = client.post(
        f"/api/v1/documents/{document_id}/ocr", json={"pages": [1]}
    )
    assert second.status_code == 200, second.text
    assert second.json()["status"] == "pending"
    assert second.json()["failed_pages"] == []

    third = client.post(
        f"/api/v1/documents/{document_id}/ocr", json={"pages": [1]}
    )
    assert third.status_code == 200, third.text
    third_body = third.json()
    assert third_body["status"] == "complete"
    assert third_body["completed_this_batch"] == [1]
    assert third_body["failed_pages"] == []
    assert len(calls) == 3

    page = storage.pages_for(document_id)[0]
    assert page["ocr_attempts"] == 3
    assert page["ocr_status"] == "done"
    assert "Recovered after retry." in page["text"]


@pytest.mark.parametrize("ocr_text", ["", " \n\t  "])
def test_empty_ocr_is_terminal_done_and_reported(client, monkeypatch, ocr_text):
    _enable_ocr(monkeypatch, page_limit=10, request_limit=1)
    upload = _upload(client, _image_only_pdf())
    document_id = upload["document_id"]
    page = storage.pages_for(document_id)[0]
    assert page["ocr_status"] == "pending"
    assert page["has_page_image"] is True
    calls: list[int] = []

    def empty_ocr(_image: bytes, _suffix: str, _settings) -> str:
        calls.append(1)
        return ocr_text

    monkeypatch.setattr("app.services.page_ocr.run_tesseract", empty_ocr)
    endpoint = f"/api/v1/documents/{document_id}/ocr"
    first = client.post(endpoint, json={"pages": [1]})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "complete"
    assert body["completed_this_batch"] == [1]
    assert body["failed_this_batch"] == []
    assert body["pending_pages"] == []
    assert body["failed_pages"] == []
    assert body["empty_pages"] == [1]
    assert any(
        warning["message"] == "Picture page(s), no text could be read: 1."
        for warning in body["warnings"]
    )

    stored = storage.pages_for(document_id)[0]
    assert stored["ocr_status"] == "done"
    assert stored["ocr_empty"] is True
    assert stored["ocr_attempts"] == 1
    assert stored["has_page_image"] is True
    assert stored["text"] == ""
    assert storage.get(document_id).sections == []

    second = client.post(endpoint, json={"pages": [1]})
    assert second.status_code == 200, second.text
    assert second.json()["attempted_pages"] == []
    assert second.json()["empty_pages"] == [1]
    assert second.json()["failed_pages"] == []
    assert len(calls) == 1

    source = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id, "jottings_text": "Picture page"},
    )
    assert source.status_code == 201, source.text
    review = client.post(
        f"/api/v1/topic-sources/{source.json()['source_id']}/topic-review",
        json={"topics": ["Picture page"]},
    )
    assert review.status_code == 200, review.text
    assert any(
        warning["code"] == "ocr_empty_pages"
        and warning["message"] == "Picture page(s), no text could be read: 1."
        for warning in review.json()["warnings"]
    )


def test_page_extraction_represents_empty_ocr_as_status_and_keeps_image(
    client, monkeypatch
):
    _enable_ocr(monkeypatch, page_limit=10, request_limit=1)
    upload = _upload(client, _image_only_pdf())
    document_id = upload["document_id"]
    monkeypatch.setattr("app.services.page_ocr.run_tesseract", lambda *_args: " \n")

    response = client.post(
        f"/api/v1/documents/{document_id}/extract",
        json={"selector": {"type": "pages", "spec": "1"}},
    )

    assert response.status_code == 200, response.text
    passage = response.json()["passages"][0]
    assert passage["text"] == "No text could be read from this page"
    assert passage["ocr_empty"] is True
    assert passage["has_page_image"] is True


def test_ocr_failure_diagnostics_are_logged_but_not_returned(client, monkeypatch, caplog):
    _enable_ocr(monkeypatch, page_limit=10, request_limit=1)
    upload = _upload(client, build_pdf([""]), filename="private-lecture.pdf")
    document_id = upload["document_id"]
    stderr = "TESSERACT_TEST_DIAGNOSTIC " + "x" * 220 + " UNLOGGED_TAIL"

    def fail(_image: bytes, _suffix: str, _settings) -> str:
        raise OcrFailed(
            "test failure",
            return_code=17,
            stderr=stderr,
        )

    monkeypatch.setattr("app.services.page_ocr.run_tesseract", fail)
    with caplog.at_level(logging.INFO, logger="app.services.page_ocr"):
        response = client.post(
            f"/api/v1/documents/{document_id}/ocr", json={"pages": [1]}
        )

    assert response.status_code == 200
    log_line = next(
        record.getMessage()
        for record in caplog.records
        if "pdf page OCR" in record.getMessage()
    )
    assert "exception_type=OcrFailed" in log_line
    assert "return_code=17" in log_line
    assert "image_width=" in log_line and "image_height=" in log_line
    assert "dpi=72" in log_line
    assert "ocr_characters=0" in log_line
    assert "TESSERACT_TEST_DIAGNOSTIC" in log_line
    excerpt = re.search(r"stderr_excerpt=(.*)$", log_line)
    assert excerpt is not None
    assert len(excerpt.group(1)) == 200
    assert "UNLOGGED_TAIL" not in log_line
    assert "private-lecture.pdf" not in log_line
    assert "TESSERACT_TEST_DIAGNOSTIC" not in response.text
    assert "private-lecture.pdf" not in response.text


def test_failed_ocr_page_stops_after_two_retries(client, monkeypatch):
    _enable_ocr(monkeypatch, page_limit=10, request_limit=5)
    upload = _upload(client, build_pdf([""]))
    document_id = upload["document_id"]
    calls: list[int] = []

    def always_fail(_image: bytes, _suffix: str, _settings) -> str:
        calls.append(1)
        raise OcrFailed("permanent OCR failure")

    monkeypatch.setattr("app.services.page_ocr.run_tesseract", always_fail)

    for _ in range(3):
        response = client.post(
            f"/api/v1/documents/{document_id}/ocr", json={"pages": [1]}
        )
        assert response.status_code == 200, response.text

    body = response.json()
    assert body["status"] == "incomplete"
    assert body["pending_pages"] == []
    assert body["failed_pages"] == [1]
    assert len(calls) == 3

    page = storage.pages_for(document_id)[0]
    assert page["ocr_attempts"] == 3
    assert page["ocr_status"] == "failed"

    no_more = client.post(
        f"/api/v1/documents/{document_id}/ocr", json={"pages": [1]}
    )
    assert no_more.status_code == 200, no_more.text
    assert no_more.json()["failed_pages"] == [1]
    assert no_more.json()["attempted_pages"] == []
    assert len(calls) == 3
