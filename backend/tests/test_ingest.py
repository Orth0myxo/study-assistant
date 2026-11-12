from __future__ import annotations

import threading
import time

from app.config import get_settings
from app.errors import ApiError
from app.services import ingest
from app.services.storage import storage
from tests.helpers import build_docx, build_pdf

PDF_CT = "application/pdf"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _wait_for_document(client, document_id: str, attempts: int = 200):
    for _ in range(attempts):
        response = client.get(f"/api/v1/documents/{document_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] != "processing":
            return body
        time.sleep(0.05)
    raise AssertionError("document did not finish ingesting")


def test_upload_returns_while_ingest_runs_then_get_reports_ready(
    client, monkeypatch
):
    original_parse = ingest.parse_document
    release = threading.Event()

    def paused_parse(*args, **kwargs):
        release.wait(5)
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(ingest, "parse_document", paused_parse)
    try:
        response = client.post(
        "/api/v1/documents",
        files={
            "file": (
                "lecture.pdf",
                build_pdf(["Background ingestion keeps this text."]),
                PDF_CT,
            )
        },
        )
    finally:
        release.set()

    assert response.status_code == 201
    queued = response.json()
    assert queued["status"] == "processing"
    assert queued["extraction"]["status"] == "pending"
    assert queued["extraction"]["text_extractable"] is False

    ready = _wait_for_document(client, queued["document_id"])
    assert ready["status"] == "ready"
    assert ready["extraction"]["status"] == "ok"
    assert ready["extraction"]["text_extractable"] is True
    assert ready["extraction"]["total_chars"] > 0


def test_async_upload_rejects_page_limit_before_queueing(client):
    response = client.post(
        "/api/v1/documents",
        files={
            "file": (
                "long.pdf",
                build_pdf([f"Page {index}." for index in range(51)]),
                PDF_CT,
            )
        },
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "document_too_long"


def test_pdf_pages_include_empty_pages_and_structure(client):
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "pages.pdf",
                build_pdf(["Visible page text.", ""]),
                PDF_CT,
            )
        },
    )
    assert response.status_code == 201
    document_id = response.json()["document_id"]

    pages = storage.pages_for(document_id)
    assert [page["page_number"] for page in pages] == [1, 2]
    assert pages[0]["kind"] == "pdf_page"
    assert pages[0]["text"].strip() == "Visible page text."
    assert pages[0]["structure"]
    assert pages[0]["structure"][0]["role"] in {"normal", "heading", "title"}
    assert pages[1]["text"] == ""
    assert pages[1]["source"] == "none"
    assert pages[1]["structure"] == []


def test_docx_stores_paragraph_roles_and_hints(client):
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "notes.docx",
                build_docx(
                    [
                        ("heading", "Anemias"),
                        ("para", "Iron deficiency is microcytic."),
                    ]
                ),
                DOCX_CT,
            )
        },
    )
    assert response.status_code == 201
    pages = storage.pages_for(response.json()["document_id"])

    assert [page["kind"] for page in pages] == ["docx_block", "docx_block"]
    assert pages[0]["structure"][0]["role"] == "heading"
    assert pages[1]["structure"][0]["role"] == "normal"
    assert pages[1]["structure"][0]["start"] == 0
    assert pages[1]["structure"][0]["end"] == len(pages[1]["text"])


def test_pdf_over_page_limit_is_rejected_before_ingest(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_pages", 1)

    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("long.pdf", build_pdf(["one", "two"]), PDF_CT)},
    )

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "document_too_long"
    assert error["details"]["max_pages"] == 1


def test_docx_over_word_limit_is_rejected(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_docx_words", 2)

    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "long.docx",
                build_docx([("para", "one two three")]),
                DOCX_CT,
            )
        },
    )

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "document_too_long"
    assert error["details"]["word_count"] == 3


def test_background_failure_is_visible_on_get(client, monkeypatch):
    def fail(*args, **kwargs):
        raise ApiError("ocr_unavailable", "OCR is not available.", 503)

    monkeypatch.setattr(ingest, "parse_document", fail)
    response = client.post(
        "/api/v1/documents",
        files={"file": ("scan.pdf", build_pdf(["text"]), PDF_CT)},
    )
    assert response.status_code == 201

    body = _wait_for_document(client, response.json()["document_id"])
    assert body["status"] == "failed"
    assert body["error"]["code"] == "ocr_unavailable"


def test_other_user_cannot_read_processing_document(client, make_user):
    response = client.post(
        "/api/v1/documents",
        files={"file": ("private.pdf", build_pdf(["Private text."]), PDF_CT)},
    )
    document_id = response.json()["document_id"]
    other = make_user()

    denied = client.get(
        f"/api/v1/documents/{document_id}", headers=other["headers"]
    )

    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "document_not_found"


def test_processing_document_is_rejected_by_extraction_and_study(client, identity):
    document_id = storage.new_document_id()
    storage.create_pending(
        document_id,
        "processing.pdf",
        build_pdf(["Text that is not ready yet."]),
        owner_id=identity["user_id"],
        format="pdf",
    )

    extracted = client.post(
        f"/api/v1/documents/{document_id}/ranges",
        json={"selector": "1"},
    )
    studied = client.post(
        "/api/v1/study-jobs",
        json={"document_id": document_id, "mode": "extracted_text_only"},
    )

    assert extracted.status_code == 409
    assert extracted.json()["error"]["code"] == "document_processing"
    assert studied.status_code == 409
    assert studied.json()["error"]["code"] == "document_processing"


def test_page_rows_survive_store_reload(client):
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("keep.pdf", build_pdf(["Persisted page text."]), PDF_CT)},
    )
    document_id = response.json()["document_id"]

    assert storage.reload() >= 1
    pages = storage.pages_for(document_id)
    assert len(pages) == 1
    assert pages[0]["text"].strip() == "Persisted page text."
