from __future__ import annotations

import io
import logging

import pymupdf
from docx import Document as DocxDocument

from app.config import Settings, get_settings
from app.services.document_parser import parse_document
from app.services.exporter import add_page_export_context, render
from app.services.storage import storage
from tests.helpers import build_docx, build_pdf, build_png

PDF_CT = "application/pdf"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _upload(client, filename: str, data: bytes, content_type: str):
    return client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (filename, data, content_type)},
    )


def _pdf_page_with_image(title: str, image_rect: pymupdf.Rect) -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), title, fontsize=12)
    page.insert_image(image_rect, stream=build_png())
    data = document.tobytes()
    document.close()
    return data


def _pdf_with_grid_table(rows: int = 12, columns: int = 3) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=540, height=760)
    left, top, right, bottom = 48, 72, 492, 600
    row_height = (bottom - top) / rows
    col_width = (right - left) / columns
    for row in range(rows + 1):
        y = top + row * row_height
        page.draw_line((left, y), (right, y), color=(0, 0, 0), width=0.8)
    for column in range(columns + 1):
        x = left + column * col_width
        page.draw_line((x, top), (x, bottom), color=(0, 0, 0), width=0.8)
    for row in range(rows):
        for column in range(columns):
            page.insert_text(
                (left + column * col_width + 6, top + row * row_height + 20),
                f"R{row:02d}C{column}",
                fontsize=10,
            )
    data = document.tobytes()
    document.close()
    return data


def test_pdf_short_title_with_large_image_is_marked_for_lazy_ocr():
    data = _pdf_page_with_image(
        "VAGINAL CYTOLOGY", pymupdf.Rect(50, 120, 545, 790)
    )
    calls: list[tuple[bytes, str]] = []

    def fake_ocr(image: bytes, suffix: str, _settings: Settings) -> str:
        calls.append((image, suffix))
        return "VAGINAL CYTOLOGY\nCervical cells and epithelial features."

    parsed = parse_document(
        "image-heavy.pdf",
        data,
        Settings(ocr_page_limit=1),
        ocr_runner=fake_ocr,
    )

    assert calls == []
    assert parsed.pages[0].source == "native"
    assert parsed.pages[0].native_char_count == len("VAGINALCYTOLOGY")
    assert parsed.pages[0].image_coverage >= 0.20
    assert parsed.pages[0].needs_ocr is True
    assert parsed.pages[0].ocr_status == "pending"
    assert parsed.pages[0].has_page_image is True
    assert parsed.sections[0].anchor["page"] == 1
    assert parsed.sections[0].raw_text.strip() == "VAGINAL CYTOLOGY"
    assert parsed.sections[0].has_page_image is True
    assert parsed.diagnostics["image_ocr_candidates"] == 1
    assert parsed.diagnostics["ocr_attempts"] == 0
    assert parsed.diagnostics["ocr_pending_pages"] == [1]


def test_pdf_short_title_with_vector_body_is_marked_for_lazy_ocr():
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "VAGINAL CYTOLOGY", fontsize=12)
    page.draw_rect(
        pymupdf.Rect(50, 120, 545, 790),
        color=None,
        fill=(0.85, 0.85, 0.85),
    )
    data = document.tobytes()
    document.close()

    parsed = parse_document(
        "vector-body.pdf",
        data,
        Settings(ocr_page_limit=1),
        ocr_runner=lambda *_args: "unexpected OCR",
    )

    assert parsed.pages[0].native_char_count == len("VAGINALCYTOLOGY")
    assert parsed.pages[0].image_coverage == 0.0
    assert parsed.pages[0].needs_ocr is True
    assert parsed.pages[0].ocr_status == "pending"
    assert parsed.diagnostics["image_ocr_candidates"] == 0
    assert parsed.diagnostics["vector_ocr_candidates"] == 1


def test_pdf_short_native_text_without_graphics_is_not_marked_for_ocr():
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Native text on the lecture page.", fontsize=12)
    data = document.tobytes()
    document.close()

    parsed = parse_document(
        "mostly-native.pdf",
        data,
        Settings(ocr_page_limit=1),
        ocr_runner=lambda *_args: "unexpected OCR",
    )

    assert parsed.pages[0].needs_ocr is False
    assert parsed.pages[0].ocr_status == "none"
    assert parsed.diagnostics["vector_ocr_candidates"] == 0


def test_pdf_small_illustration_with_native_text_does_not_trigger_extra_ocr():
    data = _pdf_page_with_image(
        "Native text on the lecture page.", pymupdf.Rect(500, 760, 530, 790)
    )
    calls: list[bytes] = []

    def fake_ocr(image: bytes, _suffix: str, _settings: Settings) -> str:
        calls.append(image)
        return "unexpected OCR"

    parsed = parse_document(
        "mostly-native.pdf",
        data,
        Settings(ocr_page_limit=1),
        ocr_runner=fake_ocr,
    )

    assert calls == []
    assert parsed.pages[0].source == "native"
    assert parsed.sections[0].raw_text.strip() == "Native text on the lecture page."
    assert parsed.diagnostics["image_ocr_candidates"] == 0


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


def test_header_footer_only_pdf_defers_ocr(client):
    data = build_pdf(["Page 1", "Page 2", "Confidential"])

    response = _upload(client, "headers.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["text_extractable"] is False
    assert body["extraction"]["section_count"] == 0
    assert body["extraction"]["total_chars"] == 0
    assert body["warnings"][0]["code"] == "ocr_deferred"


def test_whitespace_only_pdf_defers_ocr(client):
    data = build_pdf(["   ", "\n\n", "\t\t"])

    response = _upload(client, "whitespace.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["section_count"] == 0
    assert body["extraction"]["total_chars"] == 0
    assert body["warnings"][0]["code"] == "ocr_deferred"


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


def test_scanned_pdf_without_native_text_defers_ocr(client):
    data = build_pdf(["", ""])

    response = _upload(client, "scan.pdf", data, PDF_CT)

    assert response.status_code == 201
    body = response.json()
    assert body["extraction"]["status"] == "partial"
    assert body["extraction"]["text_extractable"] is False
    assert body["extraction"]["section_count"] == 0
    assert body["warnings"][0]["code"] == "ocr_deferred"
    assert "1, 2" in body["warnings"][0]["message"]


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


def test_pdf_upload_defers_table_detection(client, monkeypatch):
    from app.services import document_parser

    calls: list[int] = []

    def fake_tables(*args, **kwargs):
        calls.append(1)
        return []

    monkeypatch.setattr(document_parser, "_pdf_tables", fake_tables)
    response = _upload(client, "grid.pdf", _pdf_with_grid_table(), PDF_CT)

    assert response.status_code == 201
    document_id = response.json()["document_id"]
    page = storage.pages_for(document_id)[0]
    assert calls == []
    assert page["tables"] == []
    assert page["tables_checked"] is False


def test_lazy_table_detection_is_stored_and_reused(client, monkeypatch):
    from app.services import document_parser

    response = _upload(client, "grid.pdf", _pdf_with_grid_table(), PDF_CT)
    assert response.status_code == 201
    document_id = response.json()["document_id"]
    calls: list[int] = []
    original = document_parser._pdf_tables

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(document_parser, "_pdf_tables", counted)
    pages = storage.ensure_page_tables(document_id, {1})
    assert len(calls) == 1
    assert pages[0]["tables_checked"] is True
    assert len(pages[0]["tables"]) == 1

    storage.reload()
    pages = storage.ensure_page_tables(document_id, {1})
    assert len(calls) == 1
    assert len(pages[0]["tables"]) == 1


def test_pdf_table_cells_are_stored_separately_from_verbatim_page_text(client):
    response = _upload(client, "grid.pdf", _pdf_with_grid_table(), PDF_CT)

    assert response.status_code == 201
    document_id = response.json()["document_id"]
    record = storage.get(document_id)
    assert record is not None
    page = storage.ensure_page_tables(document_id, {1})[0]
    assert page["text"] == record.sections[0]["raw_text"]
    assert page["tables_checked"] is True
    assert len(page["tables"]) == 1
    table = page["tables"][0]
    assert table["row_count"] == 12
    assert table["column_count"] == 3
    assert table["rows"][0] == ["R00C0", "R00C1", "R00C2"]
    assert table["rows"][-1] == ["R11C0", "R11C1", "R11C2"]

    payload = {
        "kind": "topic_study",
        "format": "pdf",
        "filename": record.filename,
        "document": {"filename": record.filename},
        "requested_topics": ["Grid"],
        "topics": [
            {
                "text": "Grid",
                "match_status": "matched",
                "source_passages": [
                    {
                        "anchor": record.sections[0]["anchor"],
                        "passage": record.sections[0]["raw_text"],
                    }
                ],
            }
        ],
    }
    add_page_export_context(
        payload,
        document=record,
        page_records=storage.pages_for(document_id),
        cache_root=storage.root,
        include_page_images=False,
    )
    docx = DocxDocument(io.BytesIO(render(payload, "docx")))
    assert len(docx.tables) == 1
    assert len(docx.tables[0].rows) == 12
    assert len(docx.tables[0].columns) == 3
    assert docx.tables[0].cell(11, 2).text == "R11C2"

    exported_pdf = pymupdf.open(stream=render(payload, "pdf"), filetype="pdf")
    exported_text = "\n".join(page.get_text() for page in exported_pdf)
    exported_pdf.close()
    assert exported_text.count("R00C0") == 1
    assert exported_text.count("R11C2") == 1
    assert exported_text.index("R10C0") < exported_text.index("R11C0")


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
