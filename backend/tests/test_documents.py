from __future__ import annotations

import logging

from app.config import get_settings
from app.services.storage import storage
from tests.helpers import build_docx, build_pdf

PDF_CT = "application/pdf"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _upload(client, filename: str, data: bytes, content_type: str):
    return client.post(
        "/api/v1/documents",
        files={"file": (filename, data, content_type)},
    )


def test_pdf_extraction_preserves_one_based_page_numbers(client):
    data = build_pdf(["Page one content.", "Page two content."])

    response = _upload(client, "lecture.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["format"] == "pdf"
    assert body["page_count"] == 2
    assert body["extraction"]["status"] == "ok"
    assert body["extraction"]["text_extractable"] is True
    assert body["extraction"]["section_count"] == 2
    assert body["extraction"]["total_chars"] > 0
    assert body["warnings"] == []

    record = storage.get(body["document_id"])
    assert record is not None
    anchors = [section["anchor"] for section in record.sections]
    assert anchors[0]["kind"] == "pdf_page"
    assert anchors[0]["page"] == 1
    assert anchors[0]["page_index"] == 0
    assert anchors[1]["page"] == 2
    assert anchors[1]["page_index"] == 1
    assert "Page one content." in record.sections[0]["raw_text"]


def test_pdf_header_within_first_1024_bytes_is_accepted(client):
    data = b"J" * 400 + build_pdf(["Header tolerance check with enough content."])
    assert data.index(b"%PDF-") < 1024

    response = _upload(client, "prefixed.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "ok"
    assert body["extraction"]["text_extractable"] is True


def test_pdf_header_beyond_first_1024_bytes_is_rejected(client):
    data = b"J" * 1024 + build_pdf(["content"])

    response = _upload(client, "late-header.pdf", data, PDF_CT)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "corrupt_document"


def test_damaged_pdf_is_reported_as_repaired_not_scanned(client):
    data = build_pdf(
        [
            f"Page {index} contains plenty of extractable lecture content."
            for index in range(1, 6)
        ]
    )
    truncated = data[: int(len(data) * 0.6)]

    response = _upload(client, "damaged.pdf", truncated, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["warnings"][0]["code"] == "document_repaired"
    assert "reconstructed" in body["warnings"][0]["message"].lower()
    assert all(
        warning["code"] != "scanned_pdf_no_text" for warning in body["warnings"]
    )


def test_header_footer_only_pdf_is_no_text(client):
    data = build_pdf(["Page 1", "Page 2", "Confidential"])

    response = _upload(client, "headers.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert body["extraction"]["text_extractable"] is False
    assert body["extraction"]["section_count"] == 0
    assert body["extraction"]["total_chars"] == 0
    assert body["warnings"][0]["code"] == "scanned_pdf_no_text"


def test_whitespace_only_pdf_is_no_text(client):
    data = build_pdf(["   ", "\n\n", "\t\t"])

    response = _upload(client, "whitespace.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert body["extraction"]["section_count"] == 0
    assert body["extraction"]["total_chars"] == 0


def test_mixed_content_pdf_reports_partial_and_skips_boilerplate_pages(client):
    data = build_pdf(
        [
            "Iron deficiency anemia causes microcytic hypochromic anemia.",
            "Page 2",
            "Megaloblastic anemia causes macrocytic anemia with hypersegmented neutrophils.",
            "Page 4",
        ]
    )

    response = _upload(client, "mixed.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["text_extractable"] is True
    assert body["extraction"]["section_count"] == 2
    assert body["warnings"][0]["code"] == "partial_extraction"
    assert "2 of 4 pages" in body["warnings"][0]["message"]

    record = storage.get(body["document_id"])
    assert record is not None
    assert [section["anchor"]["page"] for section in record.sections] == [1, 3]


def test_mostly_empty_pdf_is_partial(client):
    pages = ["Only real content is on this first page of the document."]
    pages.extend(f"Page {index}" for index in range(2, 11))
    data = build_pdf(pages)

    response = _upload(client, "mostly-empty.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["section_count"] == 1
    assert body["warnings"][0]["code"] == "partial_extraction"
    assert "9 of 10 pages" in body["warnings"][0]["message"]


def test_pdf_text_threshold_counts_non_whitespace_characters(client):
    data = build_pdf(["a" * 14, "b" * 15])

    response = _upload(client, "threshold.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["section_count"] == 1
    assert body["extraction"]["total_chars"] == 15

    record = storage.get(body["document_id"])
    assert record is not None
    assert record.sections[0]["anchor"]["page"] == 2


def test_pdf_with_header_but_unreadable_content_is_corrupt(client):
    response = _upload(
        client, "bad.pdf", b"%PDF-1.7\nthis is not a real pdf", PDF_CT
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "corrupt_document"


def test_scanned_pdf_without_text_is_reported(client):
    data = build_pdf(["", ""])

    response = _upload(client, "scan.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert body["extraction"]["text_extractable"] is False
    assert body["extraction"]["section_count"] == 0
    assert body["warnings"][0]["code"] == "scanned_pdf_no_text"
    assert "scanned" in body["warnings"][0]["message"].lower()


def test_docx_extraction_uses_section_and_paragraph_anchors(client):
    data = build_docx(
        [
            ("heading", "Anemias"),
            ("para", "Iron deficiency anemia is microcytic and hypochromic."),
            ("para", "Megaloblastic anemia is macrocytic."),
            ("table", [["Test", "Result"], ["Hemoglobin", "Low"]]),
        ]
    )

    response = _upload(client, "notes.docx", data, DOCX_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["format"] == "docx"
    assert body["page_count"] is None
    assert body["extraction"]["status"] == "ok"
    assert body["extraction"]["text_extractable"] is True

    record = storage.get(body["document_id"])
    assert record is not None
    anchors = [section["anchor"] for section in record.sections]
    assert all(anchor["kind"] == "docx_section" for anchor in anchors)
    assert all("page" not in anchor for anchor in anchors)
    assert all(anchor["section_label"] == "Anemias" for anchor in anchors)
    indices = [anchor["paragraph_index"] for anchor in anchors]
    assert indices == sorted(indices)
    assert len(set(indices)) == len(indices)
    assert any("Hemoglobin" in section["raw_text"] for section in record.sections)


def test_docx_without_text_is_reported(client):
    data = build_docx([])

    response = _upload(client, "empty.docx", data, DOCX_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "no_text"
    assert body["extraction"]["text_extractable"] is False
    assert body["warnings"][0]["code"] == "no_extractable_text"


def test_unsupported_file_type_is_rejected(client):
    response = _upload(client, "notes.txt", b"hello", "text/plain")

    assert response.status_code == 415
    error = response.json()["error"]
    assert error["code"] == "unsupported_file_type"
    assert error["request_id"].startswith("req_")


def test_legacy_doc_is_rejected_with_clear_message(client):
    response = _upload(client, "notes.doc", b"\xd0\xcf\x11\xe0", "application/msword")

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_file_type"
    assert "docx" in response.json()["error"]["message"].lower()


def test_empty_file_is_rejected(client):
    response = _upload(client, "empty.pdf", b"", PDF_CT)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_file"


def test_pdf_with_wrong_magic_bytes_is_corrupt(client):
    response = _upload(client, "fake.pdf", b"this is not a pdf", PDF_CT)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "corrupt_document"


def test_invalid_docx_package_is_corrupt(client):
    response = _upload(client, "broken.docx", b"PK\x03\x04 not a zip", DOCX_CT)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "corrupt_document"


def test_truncated_docx_is_corrupt(client):
    data = build_docx([("para", "Readable content before truncation.")])

    response = _upload(client, "truncated.docx", data[: len(data) // 2], DOCX_CT)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "corrupt_document"


def test_docx_uncompressed_limit_is_enforced(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_docx_uncompressed_mb", 0)

    response = _upload(
        client, "notes.docx", build_docx([("para", "content")]), DOCX_CT
    )

    assert response.status_code == 413
    error = response.json()["error"]
    assert error["code"] == "file_too_large"
    assert error["details"]["limit_mb"] == 0


def test_docx_parse_failure_is_corrupt(client, monkeypatch):
    def explode(_document):
        raise ValueError("boom")

    monkeypatch.setattr("app.services.document_parser._iter_blocks", explode)

    response = _upload(
        client, "notes.docx", build_docx([("para", "content")]), DOCX_CT
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "corrupt_document"


def test_missing_file_field_is_a_validation_error(client):
    response = client.post("/api/v1/documents")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_file_too_large_is_rejected(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)

    response = _upload(client, "big.pdf", build_pdf(["content"]), PDF_CT)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_delete_document_removes_temp_file(client):
    response = _upload(client, "lecture.pdf", build_pdf(["content"]), PDF_CT)
    document_id = response.json()["document_id"]

    record = storage.get(document_id)
    assert record is not None
    path = record.path
    assert path.exists()

    deleted = client.delete(f"/api/v1/documents/{document_id}")
    assert deleted.status_code == 204
    assert not path.exists()

    again = client.delete(f"/api/v1/documents/{document_id}")
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "document_not_found"


def test_expired_documents_are_swept(client):
    response = _upload(client, "lecture.pdf", build_pdf(["content"]), PDF_CT)
    document_id = response.json()["document_id"]

    record = storage.get(document_id)
    assert record is not None
    path = record.path
    record.expires_at = 0

    removed = storage.sweep()

    assert removed >= 1
    assert not path.exists()
    assert storage.get(document_id) is None



def test_upload_returns_timing_headers_and_request_id(client):
    response = _upload(
        client,
        "lecture.pdf",
        build_pdf(["Enough text for extraction on this page."]),
        PDF_CT,
    )

    assert response.status_code == 201
    assert response.headers["x-request-id"].startswith("req_")
    timing = response.headers["server-timing"]
    for metric in ("read;dur=", "parse;dur=", "store;dur=", "total;dur="):
        assert metric in timing


def test_request_id_header_is_sanitized(client):
    valid = client.get("/health", headers={"X-Request-ID": "app_test-123"})
    assert valid.headers["x-request-id"] == "app_test-123"

    invalid = client.get("/health", headers={"X-Request-ID": "bad id!"})
    assert invalid.headers["x-request-id"].startswith("req_")
    assert invalid.headers["x-request-id"] != "bad id!"


def test_upload_logs_diagnostics_without_document_content(client, caplog):
    marker = "PRIVATE-LECTURE-MARKER-9f3c"

    with caplog.at_level(logging.INFO, logger="app.api.documents"):
        response = _upload(
            client,
            "lecture.pdf",
            build_pdf([f"{marker} with enough surrounding text."]),
            PDF_CT,
        )

    assert response.status_code == 201
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "upload completed" in logged
    assert "ocr_attempts=" in logged
    assert "parse_ms=" in logged
    assert marker not in logged
    assert "lecture.pdf" not in logged
