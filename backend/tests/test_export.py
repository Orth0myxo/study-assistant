from __future__ import annotations

import io
import random
import re
import struct
import time
from types import SimpleNamespace
from zipfile import ZipFile
import zlib

import pymupdf
import pytest
from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm

from app.config import get_settings
from app.services.exporter import (
    add_page_export_context,
    build_blocks,
    export_filename,
    organize_study_job_payload,
    render,
)
from app.services.job_store import jobs
from app.services.page_images import render_export_page_image, render_page_image
from tests.helpers import build_pdf, build_png

PDF_CT = "application/pdf"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PAGE_ONE = "Iron deficiency anemia is a microcytic hypochromic anemia."
PAGE_TWO = "Megaloblastic anemia is macrocytic with hypersegmented neutrophils."


class FakeAiClient:
    def __init__(self, summary: str = "Grounded summary.") -> None:
        self.summary = summary
        self.calls: list[dict[str, str]] = []

    def summarize(self, *, source_text, source_label, purpose="section"):
        self.calls.append({"label": source_label, "purpose": purpose})
        return f"{self.summary} {source_label}"


@pytest.fixture(autouse=True)
def _clean_jobs():
    jobs.clear()
    yield
    jobs.clear()


def _enable_ai(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.8-flash")


def _use_fake(monkeypatch) -> None:
    fake = FakeAiClient()
    monkeypatch.setattr(
        "app.services.study_processor.get_ai_client", lambda settings: fake
    )
    return fake


def _completed_job(client, monkeypatch, mode: str) -> dict:
    if mode in {"text_and_summaries", "summaries_only"}:
        _enable_ai(monkeypatch)
        _use_fake(monkeypatch)
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("lecture.pdf", build_pdf([PAGE_ONE, PAGE_TWO]), PDF_CT)},
    )
    assert response.status_code == 201
    document_id = response.json()["document_id"]
    job = client.post(
        "/api/v1/study-jobs", json={"document_id": document_id, "mode": mode}
    ).json()
    assert job["status"] == "completed"
    return job


def _pdf_text(data: bytes) -> str:
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return "\n".join(page.get_text() for page in document)
    finally:
        document.close()


def _pdf_spans(data: bytes) -> list[dict]:
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        spans = []
        for page in document:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines") or []:
                    spans.extend(line.get("spans") or [])
        return spans
    finally:
        document.close()


def _docx_text(data: bytes) -> str:
    document = DocxDocument(io.BytesIO(data))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _synthetic_photo_png(width: int = 720, height: int = 960) -> bytes:
    """Build a noisy, gradient RGB image using only the Python standard library."""
    rng = random.Random(12)
    rows = bytearray()
    for y in range(height):
        row = bytearray((0,))
        vertical = y * 100 // height
        for x in range(width):
            grain = rng.randrange(-32, 33)
            horizontal = x * 80 // width
            row.extend(
                (
                    max(0, min(255, 65 + vertical + grain)),
                    max(0, min(255, 55 + horizontal + grain)),
                    max(0, min(255, 90 + (vertical + horizontal) // 2 + grain)),
                )
            )
        rows.extend(row)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">2I5B", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows)))
        + chunk(b"IEND", b"")
    )


def _source_payload(
    text: str,
    *,
    filename: str = "lecture.pdf",
    page: int = 1,
) -> dict:
    return {
        "job_id": "format-job",
        "document_id": "format-doc",
        "filename": filename,
        "format": "pdf",
        "mode": "extracted_text_only",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "page_count": 1,
        "item_count": 1,
        "items": [
            {
                "index": 0,
                "source_refs": [
                    {"kind": "pdf_page", "page": page, "page_index": page - 1}
                ],
                "text": text,
                "summary": None,
                "warnings": [],
            }
        ],
        "document_summary": None,
        "warnings": [],
        "error": None,
        "progress": {"processed_units": 1, "total_units": 1},
        "document_available": True,
    }


def test_export_extracted_text_only_pdf_has_source_but_no_ai_content(client, monkeypatch):
    job = _completed_job(client, monkeypatch, "extracted_text_only")

    response = client.get(f"/api/v1/study-jobs/{job['job_id']}/export?format=pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-disposition"].endswith('.pdf"')

    text = _pdf_text(response.content)
    assert PAGE_ONE in text
    assert PAGE_TWO in text
    assert "AI summary" not in text
    assert "Grounded summary" not in text


def test_export_extracted_text_only_docx_has_source_text(client, monkeypatch):
    job = _completed_job(client, monkeypatch, "extracted_text_only")

    response = client.get(f"/api/v1/study-jobs/{job['job_id']}/export?format=docx")

    assert response.status_code == 200
    assert response.headers["content-type"] == DOCX_CT
    text = _docx_text(response.content)
    assert PAGE_ONE in text
    assert "Source text" not in text


def test_export_text_and_summaries_contains_both(client, monkeypatch):
    job = _completed_job(client, monkeypatch, "text_and_summaries")

    pdf_response = client.get(f"/api/v1/study-jobs/{job['job_id']}/export?format=pdf")
    assert pdf_response.status_code == 200
    pdf_text = _pdf_text(pdf_response.content)
    assert "Original source text" in pdf_text
    assert "AI summary" in pdf_text
    assert PAGE_ONE in pdf_text
    assert "Grounded summary" in pdf_text
    assert pdf_text.index("Original source text") < pdf_text.index("AI summary")

    docx_response = client.get(
        f"/api/v1/study-jobs/{job['job_id']}/export?format=docx"
    )
    assert docx_response.status_code == 200
    docx_text = _docx_text(docx_response.content)
    assert "Original source text" in docx_text
    assert "AI summary" in docx_text
    assert PAGE_ONE in docx_text
    assert docx_text.index("Original source text") < docx_text.index("AI summary")


def test_export_summaries_only_excludes_full_source(client, monkeypatch):
    job = _completed_job(client, monkeypatch, "summaries_only")

    pdf_response = client.get(f"/api/v1/study-jobs/{job['job_id']}/export?format=pdf")
    assert pdf_response.status_code == 200
    pdf_text = _pdf_text(pdf_response.content)
    assert PAGE_ONE not in pdf_text
    assert PAGE_TWO not in pdf_text
    assert "Grounded summary" in pdf_text
    assert "AI summary" in pdf_text
    assert "Summary coverage: 1 of 1 part summarized." in pdf_text
    assert "Page 1" in pdf_text

    docx_response = client.get(
        f"/api/v1/study-jobs/{job['job_id']}/export?format=docx"
    )
    docx_text = _docx_text(docx_response.content)
    assert PAGE_ONE not in docx_text
    assert "Grounded summary" in docx_text
    assert "AI summary" in docx_text


def test_one_part_summary_is_not_printed_twice_in_a_study_job_export():
    payload = _source_payload("Source text that is not exported.")
    payload["mode"] = "summaries_only"
    payload["items"][0]["text"] = None
    payload["items"][0]["summary"] = "One shared summary sentence."
    payload["document_summary"] = "One shared summary sentence."

    pdf_text = _normalize(_pdf_text(render(payload, "pdf")))
    docx_text = _normalize(_docx_text(render(payload, "docx")))

    assert pdf_text.count("One shared summary sentence.") == 1
    assert docx_text.count("One shared summary sentence.") == 1


def test_overall_summary_is_kept_when_it_differs_from_the_part_summary():
    payload = _source_payload("Source text that is not exported.")
    payload["mode"] = "summaries_only"
    payload["items"][0]["text"] = None
    payload["items"][0]["summary"] = "Part summary."
    payload["document_summary"] = "Overall summary text."

    docx_text = _normalize(_docx_text(render(payload, "docx")))

    assert "Part summary." in docx_text
    assert "Overall summary text." in docx_text


def test_topic_export_does_not_repeat_the_grounded_summary_as_the_overall_summary():
    payload = {
        "mode": "summaries_only",
        "document": {"filename": "notes.docx"},
        "requested_topics": ["Whole document"],
        "document_summary": "Same summary text.",
        "topics": [
            {
                "text": "Whole document",
                "source_passages": [],
                "study_material": {
                    "source_grounded": {"text": "Same summary text.", "basis_refs": []}
                },
            }
        ],
    }

    docx_text = _normalize(_docx_text(render(payload, "docx")))

    assert docx_text.count("Same summary text.") == 1


def test_summary_only_export_includes_source_when_summary_failed():
    source = "The original source remains available after the summary provider failed."
    payload = _source_payload(source)
    payload["mode"] = "summaries_only"
    payload["status"] = "failed"
    payload["error"] = {
        "code": "ai_quota_exhausted",
        "message": "The AI provider quota is exhausted.",
    }
    payload["items"][0]["text"] = source
    payload["warnings"] = [
        {"code": "ai_quota_exhausted", "message": "The AI provider quota is exhausted."}
    ]

    pdf_text = _normalize(_pdf_text(render(payload, "pdf")))
    docx_text = _normalize(_docx_text(render(payload, "docx")))

    for text in (pdf_text, docx_text):
        assert "Original source text" in text
        assert source in text
        assert "The AI provider quota is exhausted." in text


def test_export_uses_persisted_payload_after_reload(client, monkeypatch):
    job = _completed_job(client, monkeypatch, "extracted_text_only")

    jobs.reload()

    response = client.get(f"/api/v1/study-jobs/{job['job_id']}/export?format=pdf")
    assert response.status_code == 200
    assert PAGE_ONE in _pdf_text(response.content)


def test_export_missing_job_is_404(client):
    response = client.get("/api/v1/study-jobs/nope/export?format=pdf")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_export_not_ready_job_is_409(client, identity):
    jobs.put(
        "queued-job",
        {
            "job_id": "queued-job",
            "owner_id": identity["user_id"],
            "document_id": "doc",
            "filename": "lecture.pdf",
            "format": "pdf",
            "mode": "extracted_text_only",
            "status": "queued",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "page_count": 2,
            "item_count": 0,
            "items": [],
            "document_summary": None,
            "warnings": [],
            "error": None,
            "progress": {"processed_units": 0, "total_units": 0},
        },
    )

    response = client.get("/api/v1/study-jobs/queued-job/export?format=pdf")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_not_ready"


def test_export_unknown_format_is_validation_error(client, monkeypatch):
    job = _completed_job(client, monkeypatch, "extracted_text_only")

    response = client.get(f"/api/v1/study-jobs/{job['job_id']}/export?format=csv")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"



def test_export_decodes_filename_and_removes_internal_metadata():
    payload = _source_payload(
        "Page 1 of 1\nImmune tolerance is maintained.",
        filename=(
            "3-IMMUNOLOGICAL%20TOLERANCE%20LECTURE%20NOTE%20CURRENT_"
            "compressed.pdf"
        ),
    )

    docx_text = _normalize(_docx_text(render(payload, "docx")))
    pdf_text = _normalize(_pdf_text(render(payload, "pdf")))
    title = "Immunological Tolerance Lecture Note Current"

    for text in (docx_text, pdf_text):
        assert title in text
        assert "%20" not in text
        assert "_compressed" not in text
        assert "Extracted text only" not in text
        assert "status: completed" not in text
        assert "Exported " not in text

    safe_title = title.replace(" ", "-")
    assert (
        export_filename(payload, "docx")
        == f"{safe_title}-extracted_text_only.docx"
    )


def test_docx_source_paragraphs_are_not_restyled_as_headings():
    payload = _source_payload(
        "Short note\nThe second line stays its own paragraph.",
        filename="notes.docx",
    )
    payload["format"] = "docx"

    data = render(payload, "docx")
    document = DocxDocument(io.BytesIO(data))
    paragraphs = [item for item in document.paragraphs if item.text]
    note = next(item for item in paragraphs if item.text == "Short note")

    assert note.style.name == "Normal"
    assert not any(item.style.name.startswith("Heading") for item in paragraphs)


def test_extracted_export_reflows_text_and_removes_duplicate_page_labels():
    payload = _source_payload(
        "Page 1 of 2\n\n"
        "IMMUNOLOGICAL TOLERANCE\n"
        "Immune tolerance refers to self and non-\n"
        "self, and keeps the body healthy.\n\n"
        "1. Central tolerance: Elimination of autoreactive T cells in the\n"
        "thymus.\n"
        "2. Peripheral tolerance: Regulation in the periphery.\n\n"
        "- Spleen\n"
        "    - Lymph nodes\n"
    )

    docx_text = _normalize(_docx_text(render(payload, "docx")))
    pdf_text = _normalize(_pdf_text(render(payload, "pdf")))

    for text in (docx_text, pdf_text):
        assert "Page 1 of 2" not in text
        assert text.count("Source: Page 1") == 1
        assert "self and non-self, and keeps the body healthy." in text
        assert "1. Central tolerance: Elimination of autoreactive T cells in the thymus." in text
        assert "2. Peripheral tolerance: Regulation in the periphery." in text
        assert "- Spleen" in text
        assert "- Lymph nodes" in text


def test_pdf_export_text_and_heading_alignment():
    body = "Alignment body text."

    left_payload = _source_payload(body)
    left_payload["text_align"] = "left"
    left_span = next(
        span for span in _pdf_spans(render(left_payload, "pdf")) if body in span["text"]
    )
    assert abs(left_span["bbox"][0] - 56.0) < 2

    center_payload = _source_payload(body)
    center_payload["text_align"] = "center"
    center_payload["heading_align"] = "center"
    center_spans = _pdf_spans(render(center_payload, "pdf"))
    center_span = next(span for span in center_spans if body in span["text"])
    center_x = (center_span["bbox"][0] + center_span["bbox"][2]) / 2
    assert abs(center_x - 297.5) < 8
    title_span = next(span for span in center_spans if span["text"] == "lecture")
    assert abs((title_span["bbox"][0] + title_span["bbox"][2]) / 2 - 297.5) < 8

    right_payload = _source_payload(body)
    right_payload["text_align"] = "right"
    right_span = next(
        span for span in _pdf_spans(render(right_payload, "pdf")) if body in span["text"]
    )
    assert abs(right_span["bbox"][2] - 539.0) < 8

    justify_payload = _source_payload(body)
    justify_payload["text_align"] = "justify"
    assert body in _pdf_text(render(justify_payload, "pdf"))


@pytest.mark.parametrize(
    ("align", "expected"),
    [
        ("left", WD_ALIGN_PARAGRAPH.LEFT),
        ("center", WD_ALIGN_PARAGRAPH.CENTER),
        ("right", WD_ALIGN_PARAGRAPH.RIGHT),
        ("justify", WD_ALIGN_PARAGRAPH.JUSTIFY),
    ],
)
def test_docx_export_alignment_applies_to_body_and_headings(align, expected):
    body = "Alignment body text."
    payload = _source_payload(body)
    payload["mode"] = "summaries_only"
    payload["document_summary"] = "Overall summary body."
    payload["text_align"] = align
    payload["heading_align"] = "center"

    document = DocxDocument(io.BytesIO(render(payload, "docx")))
    paragraphs = document.paragraphs

    body_paragraph = next(paragraph for paragraph in paragraphs if body in paragraph.text)
    summary_paragraph = next(
        paragraph for paragraph in paragraphs if "Overall summary body." in paragraph.text
    )
    assert body_paragraph.alignment == expected
    assert summary_paragraph.alignment == expected

    title_paragraph = next(
        paragraph for paragraph in paragraphs if paragraph.style.name == "Title"
    )
    assert title_paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER
    heading_paragraph = next(
        paragraph for paragraph in paragraphs if paragraph.text == "Overall summary"
    )
    assert heading_paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_study_export_query_applies_alignment(client, monkeypatch):
    job = _completed_job(client, monkeypatch, "extracted_text_only")
    response = client.get(
        f"/api/v1/study-jobs/{job['job_id']}/export"
        "?format=docx&text_align=right&heading_align=center"
    )
    assert response.status_code == 200
    document = DocxDocument(io.BytesIO(response.content))
    source_paragraph = next(
        paragraph for paragraph in document.paragraphs if PAGE_ONE in paragraph.text
    )
    assert source_paragraph.alignment == WD_ALIGN_PARAGRAPH.RIGHT
    title_paragraph = next(
        paragraph for paragraph in document.paragraphs if paragraph.style.name == "Title"
    )
    assert title_paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_export_rejects_unknown_alignment(client, monkeypatch):
    job = _completed_job(client, monkeypatch, "extracted_text_only")
    response = client.get(
        f"/api/v1/study-jobs/{job['job_id']}/export?format=docx&text_align=diagonal"
    )
    assert response.status_code == 422


def test_docx_export_uses_academic_layout_and_page_number_footer():
    payload = _source_payload(
        "Page 1 of 1\n\n"
        "Immune tolerance is maintained by regulatory T cells.\n\n"
        "1. Central tolerance: Deletion in the thymus."
    )
    data = render(payload, "docx")
    document = DocxDocument(io.BytesIO(data))
    section = document.sections[0]

    assert abs(section.page_width - Cm(21.0)) < 1000
    assert abs(section.page_height - Cm(29.7)) < 1000
    assert abs(section.left_margin - Cm(2.2)) < 1000
    assert abs(section.right_margin - Cm(2.2)) < 1000

    normal = document.styles["Normal"]
    assert normal.font.name == "Calibri"
    assert normal.font.size.pt == 13.5
    assert document.styles["Title"].font.size.pt == 24.0
    assert document.styles["Heading 2"].font.size.pt == 17.0
    assert document.styles["Heading 3"].font.size.pt == 14.0

    footer_xml = section.footer._element.xml
    assert "PAGE" in footer_xml
    assert "NUMPAGES" in footer_xml

    title_paragraph = next(
        paragraph
        for paragraph in document.paragraphs
        if paragraph.style.name == "Title"
    )
    assert title_paragraph.text == "lecture"

    list_paragraph = next(
        paragraph
        for paragraph in document.paragraphs
        if paragraph.text.startswith("1. Central tolerance")
    )
    assert abs(list_paragraph.paragraph_format.left_indent - Cm(0.75)) < 1000
    assert abs(
        list_paragraph.paragraph_format.first_line_indent - Cm(-0.4)
    ) < 1000

    assert document.core_properties.title == "lecture"
    assert document.core_properties.author == "AI Study Assistant"


def test_pdf_export_adds_page_numbers_and_document_metadata():
    paragraph = (
        "This paragraph is long enough to span several rendered pages while "
        "keeping every source word intact. "
    )
    payload = _source_payload("Page 1 of 1\n\n" + paragraph * 300)

    data = render(payload, "pdf")
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        page_count = document.page_count
        assert page_count > 1
        assert document.metadata["title"] == "lecture"
        assert document.metadata["author"] == "AI Study Assistant"
        text = "\n".join(page.get_text() for page in document)
    finally:
        document.close()

    normalized = _normalize(text)
    assert f"Page 1 of {page_count}" in normalized
    assert f"Page {page_count} of {page_count}" in normalized


def test_source_parser_preserves_every_source_character():
    text = (
        "Page 1 of 2\n\n"
        "IMMUNOLOGICAL TOLERANCE\n"
        "Tolerance prevents self and non-\n"
        "self reactions.\n\n"
        "1. Central tolerance: Deletion in the thymus.\n"
        "2. Peripheral tolerance: Suppression in tissues.\n\n"
        "Page 2 of 2\n\n"
        "Key Players\n"
        "1. Antigen-presenting cells (APCs) 2. T-cell receptor (TCR)\n"
        "3. B-cell receptor\n"
        "(BCR)\n\n"
        "- Spleen\n"
        "    - Lymph nodes\n"
    )
    payload = _source_payload(text)
    payload["page_count"] = 2
    payload["item_count"] = 2
    payload["items"] = [
        {
            "index": 0,
            "source_refs": [{"kind": "pdf_page", "page": 1, "page_index": 0}],
            "text": text.split("Page 2 of 2")[0],
            "summary": None,
            "warnings": [],
        },
        {
            "index": 1,
            "source_refs": [{"kind": "pdf_page", "page": 2, "page_index": 1}],
            "text": "Page 2 of 2" + text.split("Page 2 of 2")[1],
            "summary": None,
            "warnings": [],
        },
    ]

    raw = "".join(
        line
        for line in text.splitlines()
        if not re.match(r"^Page \d+ of \d+$", line.strip())
    )
    rendered = "".join(
        block_text
        for kind, block_text in build_blocks(payload)
        if kind not in {"title", "source"}
    )

    assert re.sub(r"\s+", "", raw) == re.sub(r"\s+", "", rendered)


def test_pdf_export_large_payload_is_fast_and_keeps_text():
    text = (
        "The lecture explains a concept with sufficient detail. " * 400
    )[:12000]
    items = [
        {
            "index": index,
            "source_refs": [
                {"kind": "pdf_page", "page": index + 1, "page_index": index}
            ],
            "text": text,
            "summary": None,
            "warnings": [],
        }
        for index in range(5)
    ]
    payload = {
        "job_id": "large-job",
        "document_id": "large-doc",
        "filename": "large-lecture.pdf",
        "format": "pdf",
        "mode": "extracted_text_only",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "page_count": 5,
        "item_count": len(items),
        "items": items,
        "document_summary": None,
        "warnings": [],
        "error": None,
        "progress": {"processed_units": 5, "total_units": 5},
        "document_available": True,
    }

    started = time.perf_counter()
    data = render(payload, "pdf")
    elapsed = time.perf_counter() - started

    assert elapsed < 5.0
    extracted = _pdf_text(data)
    assert extracted.count("The lecture explains a concept") >= 5


def test_pdf_export_translates_bullets_and_typographic_quotes():
    payload = {
        "job_id": "summary-job",
        "document_id": "summary-doc",
        "filename": "summary.pdf",
        "format": "pdf",
        "mode": "summaries_only",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "page_count": 1,
        "item_count": 1,
        "items": [
            {
                "index": 0,
                "source_refs": [{"kind": "pdf_page", "page": 1, "page_index": 0}],
                "text": None,
                "summary": "\u2022 First point\n\n\u201cQuoted\u201d \u2014 dash\u2026",
                "warnings": [],
            }
        ],
        "document_summary": None,
        "warnings": [],
        "error": None,
        "progress": {"processed_units": 1, "total_units": 1},
        "document_available": True,
    }

    text = _pdf_text(render(payload, "pdf"))

    assert "AI summaries only - status: completed" not in text
    assert "Exported " not in text
    assert "Source: Page 1" in text
    assert "- First point" in text
    assert '"Quoted" - dash...' in text


def test_pdf_export_preserves_scientific_notation_and_embeds_font():
    summary = (
        "\u03b1 \u03b2 5\u00b5g 5\u03bcg 37\u00b0C Na+/K+-ATPase "
        "H. pylori x\u00b2 H\u2082O 10\u2070"
    )
    payload = {
        "job_id": "scientific-job",
        "document_id": "scientific-doc",
        "filename": "scientific.pdf",
        "format": "pdf",
        "mode": "summaries_only",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "page_count": 1,
        "item_count": 1,
        "items": [
            {
                "index": 0,
                "source_refs": [{"kind": "pdf_page", "page": 1, "page_index": 0}],
                "text": None,
                "summary": summary,
                "warnings": [],
            }
        ],
        "document_summary": None,
        "warnings": [],
        "error": None,
        "progress": {"processed_units": 1, "total_units": 1},
        "document_available": True,
    }

    data = render(payload, "pdf")
    text = _pdf_text(data)

    for token in (
        "\u03b1",
        "\u03b2",
        "5\u00b5g",
        "5\u03bcg",
        "37\u00b0C",
        "Na+/K+-ATPase",
        "H. pylori",
        "x\u00b2",
        "H\u2082O",
        "10\u2070",
    ):
        assert token in text
    assert "\ufffd" not in text
    assert "\x00" not in text
    assert "?" not in text

    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        fonts = [font[3] for font in document.get_page_fonts(0)]
    finally:
        document.close()
    assert any("DejaVu Sans" in name for name in fonts)


def test_pdf_export_scientific_notation_wraps_across_pages():
    text = (
        "\u03b1 \u03b2 5\u00b5g 5\u03bcg 37\u00b0C Na+/K+-ATPase "
        "H. pylori x\u00b2 H\u2082O 10\u2070. "
    ) * 500
    payload = {
        "job_id": "wrapped-scientific-job",
        "document_id": "wrapped-scientific-doc",
        "filename": "wrapped-scientific.pdf",
        "format": "pdf",
        "mode": "extracted_text_only",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "page_count": 1,
        "item_count": 1,
        "items": [
            {
                "index": 0,
                "source_refs": [{"kind": "pdf_page", "page": 1, "page_index": 0}],
                "text": text,
                "summary": None,
                "warnings": [],
            }
        ],
        "document_summary": None,
        "warnings": [],
        "error": None,
        "progress": {"processed_units": 1, "total_units": 1},
        "document_available": True,
    }

    data = render(payload, "pdf")
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        extracted = "\n".join(page.get_text() for page in document)
        assert document.page_count > 1
    finally:
        document.close()

    for token in ("\u03b1", "\u03b2", "5\u00b5g", "5\u03bcg", "37\u00b0C", "H\u2082O"):
        assert token in extracted
    assert "\ufffd" not in extracted
    assert "\x00" not in extracted


def test_pdf_font_assets_are_packaged():
    from app.services.exporter import _PDF_FONT_FILES

    assert _PDF_FONT_FILES
    for path in _PDF_FONT_FILES.values():
        assert path.is_file()
        assert path.stat().st_size > 100_000


def test_export_does_not_create_jobs_or_call_ai(client, monkeypatch):
    _enable_ai(monkeypatch)
    fake = _use_fake(monkeypatch)
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("lecture.pdf", build_pdf([PAGE_ONE, PAGE_TWO]), PDF_CT)},
    )
    assert response.status_code == 201
    document_id = response.json()["document_id"]
    job = client.post(
        "/api/v1/study-jobs",
        json={"document_id": document_id, "mode": "text_and_summaries"},
    ).json()
    assert job["status"] == "completed"

    jobs_before = len(jobs.list_recent(limit=100))
    calls_before = len(fake.calls)

    for _ in range(2):
        exported = client.get(
            f"/api/v1/study-jobs/{job['job_id']}/export?format=docx"
        )
        assert exported.status_code == 200

    assert len(jobs.list_recent(limit=100)) == jobs_before
    assert len(fake.calls) == calls_before


def _multi_topic_image_export_payload(tmp_path, *, include_page_images=True):
    source = pymupdf.open()
    page = source.new_page()
    page.insert_text((72, 72), "Native lecture notes on cytology.")
    page = source.new_page()
    page.insert_text((72, 72), "CYTOLOGY SLIDE")
    page.insert_image(pymupdf.Rect(50, 120, 545, 790), stream=build_png())
    page = source.new_page()
    page.insert_text((72, 72), "Text-only cell morphology notes.")
    page = source.new_page()
    page.insert_image(pymupdf.Rect(50, 50, 545, 790), stream=build_png())
    source_path = tmp_path / "cytology-source.pdf"
    source.save(source_path)
    source.close()

    payload = {
        "kind": "topic_study",
        "filename": "Cytology_Lecture.pdf",
        "created_at": "2026-11-15T12:34:00Z",
        "mode": "both",
        "status": "completed",
        "topics": [
            {
                "text": "Cytology slides",
                "match_status": "matched",
                "material_status": "generated",
                "source_passages": [
                    {
                        "anchor": {"kind": "pdf_page", "page": 2},
                        "passage": "CYTOLOGY SLIDE\n\n- Epithelial cells are examined.",
                    },
                    {
                        "anchor": {"kind": "pdf_page", "page": 1},
                        "passage": "Native lecture notes on cytology.",
                    },
                    {
                        "anchor": {"kind": "pdf_page", "page": 3},
                        "passage": "Text-only cell morphology notes.",
                    },
                ],
                "study_material": {
                    "source_grounded": {
                        "text": "The slide describes epithelial cells."
                    }
                },
            },
            {
                "text": "Image review",
                "match_status": "matched",
                "material_status": "not_requested",
                "source_passages": [
                    {
                        "anchor": {"kind": "pdf_page", "page": 2},
                        "passage": "CYTOLOGY SLIDE\n\n- Epithelial cells are examined.",
                    },
                    {
                        "anchor": {"kind": "pdf_page", "page": 4},
                        "passage": "Image-only lecture slide.",
                    },
                ],
            },
            {
                "text": "Unmatched topic",
                "match_status": "unmatched",
                "material_status": "skipped_unmatched",
                "source_passages": [],
            },
        ],
        "warnings": [],
    }
    document = SimpleNamespace(
        document_id="fixture-document",
        format="pdf",
        path=source_path,
        available=True,
    )
    page_records = [
        {
            "index": 0,
            "page_number": 1,
            "source": "native",
            "ocr_status": "none",
            "has_page_image": False,
        },
        {
            "index": 1,
            "page_number": 2,
            "source": "ocr",
            "ocr_status": "done",
            "has_page_image": True,
        },
        {
            "index": 2,
            "page_number": 3,
            "source": "native",
            "ocr_status": "pending",
            "has_page_image": False,
        },
        {
            "index": 3,
            "page_number": 4,
            "source": "native",
            "ocr_status": "failed",
            "has_page_image": True,
        },
    ]
    add_page_export_context(
        payload,
        document=document,
        page_records=page_records,
        cache_root=tmp_path / "cache",
        include_page_images=include_page_images,
    )
    return payload


def _pdf_export_image_count(data: bytes) -> int:
    document = pymupdf.open(stream=data, filetype="pdf")
    try:
        return sum(len(page.get_images(full=True)) for page in document)
    finally:
        document.close()


def _single_image_topic_payload(tmp_path, passage: str) -> dict:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = pymupdf.open()
    page = source.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=_synthetic_photo_png())
    source_path = tmp_path / "flow-layout-source.pdf"
    source.save(source_path)
    source.close()
    payload = {
        "kind": "topic_study",
        "filename": "flow-layout.pdf",
        "requested_topics": ["Flow layout"],
        "topics": [
            {
                "text": "Flow layout",
                "match_status": "matched",
                "source_passages": [
                    {
                        "anchor": {"kind": "pdf_page", "page": 1},
                        "passage": passage,
                        "has_page_image": True,
                    }
                ],
            }
        ],
    }
    add_page_export_context(
        payload,
        document=SimpleNamespace(
            document_id="flow-layout-fixture",
            format="pdf",
            path=source_path,
            available=True,
        ),
        page_records=[
            {
                "page_number": 1,
                "source": "native",
                "ocr_status": "none",
                "has_page_image": True,
            }
        ],
        cache_root=tmp_path / "cache",
        include_page_images=True,
    )
    return payload


def test_organized_exports_have_deduplicated_page_images_and_clear_labels(tmp_path):
    payload = _multi_topic_image_export_payload(tmp_path)
    pdf_data = render(payload, "pdf")
    docx_data = render(payload, "docx")

    assert _pdf_export_image_count(pdf_data) == 2
    docx = DocxDocument(io.BytesIO(docx_data))
    assert len(docx.inline_shapes) == 2
    with ZipFile(io.BytesIO(docx_data)) as package:
        assert len([name for name in package.namelist() if name.startswith("word/media/")]) == 2

    pdf_text = _pdf_text(pdf_data)
    docx_text = _docx_text(docx_data)
    for text in (pdf_text, docx_text):
        assert "Cytology Lecture" in text
        assert "Date: 2026-11-15" in text
        assert (
            "Topics requested: Cytology slides, Image review, Unmatched topic"
            in text
        )
        assert text.index("Page 1") < text.index("Page 2")
        assert "No matching passage found" in text
        assert "Text read by OCR; may contain errors" in text
        assert "OCR is pending for page(s): 3." in text
        assert "OCR failed for page(s): 4." in text
        assert "AI summary, not source text" in text
        assert "See page 2 above" in text
        assert "Epithelial cells are examined." in text
        assert "Original text from the source" in text
        assert "matched · source passages only" not in text


def test_export_page_selection_header_uses_page_and_range_wording():
    selections = [
        ([2], "Page 2"),
        ([2, 3, 4], "Pages 2-4"),
    ]
    for pages, label in selections:
        payload = {
            "filename": "selection.pdf",
            "mode": "extracted_text_only",
            "items": [
                {
                    "source_refs": [
                        {"kind": "pdf_page", "page": page} for page in pages
                    ],
                    "text": "Selected source passage.",
                }
            ],
        }
        organize_study_job_payload(payload)
        blocks = build_blocks(payload)
        assert ("meta", f"Topics requested: {label}") in blocks
        assert ("h2", label) in blocks
        assert ("meta", "Original text from the source") in blocks

    several_topics = {
        "filename": "selection.pdf",
        "mode": "extracted_text_only",
        "items": [
            {
                "source_refs": [{"kind": "pdf_page", "page": page}],
                "text": f"Source passage on page {page}.",
            }
            for page in (2, 4)
        ],
    }
    organize_study_job_payload(several_topics)
    blocks = build_blocks(several_topics)
    assert ("meta", "Topics requested: Page 2, Page 4") in blocks
    assert ("h2", "Page 2") in blocks and ("h2", "Page 4") in blocks


def test_organized_exports_can_omit_all_page_images(tmp_path):
    payload = _multi_topic_image_export_payload(tmp_path, include_page_images=False)

    assert _pdf_export_image_count(render(payload, "pdf")) == 0
    docx_data = render(payload, "docx")
    assert len(DocxDocument(io.BytesIO(docx_data)).inline_shapes) == 0


def test_empty_ocr_page_export_keeps_slide_image_and_labels_status(
    tmp_path, monkeypatch
):
    slide = tmp_path / "slide.png"
    slide.write_bytes(build_png(64))
    payload = {
        "kind": "topic_study",
        "filename": "picture.pdf",
        "topics": [
            {
                "text": "Cytology",
                "source_passages": [
                    {
                        "passage": "No text could be read from this page",
                        "anchor": {"kind": "pdf_page", "page": 1},
                        "has_page_image": True,
                        "ocr_empty": True,
                    }
                ],
                "study_material": {},
            }
        ],
        "include_page_images": True,
        "_export_page_image_context": {
            "document_id": "picture-doc",
            "source_path": tmp_path / "source.pdf",
            "cache_root": tmp_path,
        },
    }
    monkeypatch.setattr(
        "app.services.exporter.render_export_page_image",
        lambda **_kwargs: slide,
    )

    blocks = build_blocks(payload)
    assert ("ocr", "No text could be read from this page") in blocks
    assert ("body", "No text could be read from this page") not in blocks
    assert ("ocr", "Text read by OCR; may contain errors") not in blocks
    assert sum(kind == "page_image" for kind, _ in blocks) == 1

    pdf = render(payload, "pdf")
    docx = render(payload, "docx")
    assert _pdf_export_image_count(pdf) == 1
    assert len(DocxDocument(io.BytesIO(docx)).inline_shapes) == 1
    assert "No text could be read from this page" in _pdf_text(pdf)
    assert "No text could be read from this page" in _docx_text(docx)


def test_export_jpeg_is_smaller_than_route_png_and_embedded_in_both_formats(tmp_path):
    source = pymupdf.open()
    page = source.new_page(width=595, height=842)
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=_synthetic_photo_png())
    source_path = tmp_path / "photo-slide.pdf"
    source.save(source_path)
    source.close()

    render_args = {
        "document_id": "photo-fixture",
        "source_path": source_path,
        "cache_root": tmp_path / "cache",
        "page_number": 1,
    }
    route_png = render_page_image(**render_args)
    export_jpeg = render_export_page_image(**render_args)
    assert export_jpeg.suffix == ".jpg"
    assert export_jpeg.read_bytes().startswith(b"\xff\xd8")
    assert export_jpeg.stat().st_size < route_png.stat().st_size / 2
    assert render_export_page_image(**render_args) == export_jpeg

    payload = {
        "kind": "topic_study",
        "filename": "photo-slide.pdf",
        "requested_topics": ["Slide image"],
        "topics": [
            {
                "text": "Slide image",
                "match_status": "matched",
                "source_passages": [
                    {
                        "anchor": {"kind": "pdf_page", "page": 1},
                        "passage": "Slide caption.",
                        "has_page_image": True,
                    }
                ],
            }
        ],
    }
    document = SimpleNamespace(
        document_id="photo-fixture",
        format="pdf",
        path=source_path,
        available=True,
    )
    add_page_export_context(
        payload,
        document=document,
        page_records=[
            {
                "page_number": 1,
                "source": "native",
                "ocr_status": "none",
                "has_page_image": True,
            }
        ],
        cache_root=tmp_path / "cache",
        include_page_images=True,
    )

    pdf_data = render(payload, "pdf")
    docx_data = render(payload, "docx")
    assert _pdf_export_image_count(pdf_data) == 1
    assert len(DocxDocument(io.BytesIO(docx_data)).inline_shapes) == 1


def test_pdf_image_flows_after_short_text_and_follows_long_text(tmp_path):
    short_pdf = render(
        _single_image_topic_payload(tmp_path / "short", "Short passage."),
        "pdf",
    )
    short_document = pymupdf.open(stream=short_pdf, filetype="pdf")
    try:
        short_page = next(
            page
            for page in short_document
            if "Short passage." in page.get_text()
        )
        assert short_page.get_images(full=True)
    finally:
        short_document.close()

    long_passage = "A cytology source passage contains details. " * 2200
    long_payload = _single_image_topic_payload(
        tmp_path / "long", long_passage + "LONG_PASSAGE_END"
    )
    long_pdf = render(long_payload, "pdf")
    long_document = pymupdf.open(stream=long_pdf, filetype="pdf")
    try:
        marker_page = next(
            index
            for index, page in enumerate(long_document)
            if "LONG_PASSAGE_END" in page.get_text()
        )
        image_pages = [
            index
            for index, page in enumerate(long_document)
            if page.get_images(full=True)
        ]
        assert len(image_pages) == 1
        assert image_pages[0] > marker_page
    finally:
        long_document.close()


def test_export_image_count_and_byte_limits_name_omitted_pages(tmp_path, monkeypatch):
    image_file = tmp_path / "small.png"
    image_file.write_bytes(b"x")

    def fake_render(*, page_number, **_kwargs):
        return image_file

    monkeypatch.setattr("app.services.exporter.render_export_page_image", fake_render)
    passages = [
        {
            "anchor": {"kind": "pdf_page", "page": page},
            "passage": f"Page {page}",
            "has_page_image": True,
        }
        for page in range(1, 32)
    ]
    payload = {
        "kind": "topic_study",
        "filename": "many-images.pdf",
        "include_page_images": True,
        "_export_page_image_context": {
            "document_id": "doc",
            "source_path": "source.pdf",
            "cache_root": tmp_path,
        },
        "topics": [
            {
                "text": "Many pages",
                "match_status": "matched",
                "source_passages": passages,
            }
        ],
    }
    blocks = build_blocks(payload)
    assert sum(kind == "page_image" for kind, _ in blocks) == 30
    count_warning = next(text for kind, text in blocks if kind == "warning")
    assert "31" in count_warning

    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    third = tmp_path / "third.png"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    third.write_bytes(b"x")
    with first.open("r+b") as image:
        image.truncate(20 * 1024 * 1024)
    with second.open("r+b") as image:
        image.truncate(6 * 1024 * 1024)

    def sized_render(*, page_number, **_kwargs):
        return {1: first, 2: second, 3: third}[page_number]

    monkeypatch.setattr("app.services.exporter.render_export_page_image", sized_render)
    payload["topics"][0]["source_passages"] = passages[:3]
    blocks = build_blocks(payload)
    assert sum(kind == "page_image" for kind, _ in blocks) == 1
    size_warning = next(text for kind, text in blocks if kind == "warning")
    assert "2, 3" in size_warning


def _paragraph_refs(count: int) -> list[dict]:
    return [
        {"kind": "docx_section", "section_label": "Lecture", "paragraph_index": index}
        for index in range(count)
    ]


def test_reference_labels_collapse_runs_and_use_one_based_paragraphs():
    from app.services.exporter import _ref_label, _refs_label

    paragraphs = _paragraph_refs(71)
    assert _ref_label(paragraphs[0]) == "Section: Lecture, Paragraph 1"
    assert _refs_label(paragraphs) == "Section: Lecture, Paragraphs 1-71"
    assert _refs_label(paragraphs, paragraphs) == "Whole document"
    assert _refs_label(
        [{"kind": "pdf_page", "page": page} for page in (2, 3, 4)]
    ) == "Pages 2-4"


def test_long_paragraph_topic_heading_and_meta_stay_compact_and_wrap():
    payload = _source_payload("Original selected paragraph.")
    payload["format"] = "docx"
    payload["items"][0]["source_refs"] = _paragraph_refs(71)
    payload["_export_all_refs"] = _paragraph_refs(100)
    organize_study_job_payload(payload)
    blocks = build_blocks(payload)
    heading = next(text for kind, text in blocks if kind == "h2")
    meta = next(
        text
        for kind, text in blocks
        if kind == "meta" and text.startswith("Topics requested:")
    )
    assert heading == "Section: Lecture, Paragraphs 1-71"
    assert meta == "Topics requested: Section: Lecture, Paragraphs 1-71"
    assert len(heading) < 200 and len(meta) < 200

    data = render(payload, "pdf")
    text = _pdf_text(data)
    assert "paragraph 0" not in text.lower()
    assert "Paragraphs 1-71" in _normalize(text)
    for span in _pdf_spans(data):
        assert span["bbox"][2] <= 539.5


def test_pdf_export_uses_readable_font_sizes():
    payload = _source_payload(
        "Readable body text for older readers. " * 4
    )
    payload["mode"] = "text_and_summaries"
    payload["items"][0]["summary"] = "Overall AI summary."
    payload["document_summary"] = "Overall AI summary."
    spans = _pdf_spans(render(payload, "pdf"))
    body = next(span for span in spans if "Readable body" in span["text"])
    source = next(span for span in spans if "Source: Page 1" in span["text"])
    title = next(span for span in spans if span["text"] == "lecture")
    assert body["size"] >= 13.0
    assert source["size"] >= 11.0
    assert title["size"] >= 24.0


def test_docx_export_uses_readable_font_sizes():
    payload = _source_payload(
        "Readable body text for older readers. " * 4
    )
    document = DocxDocument(io.BytesIO(render(payload, "docx")))
    body = next(
        paragraph
        for paragraph in document.paragraphs
        if "Readable body" in paragraph.text
    )
    source = next(
        paragraph for paragraph in document.paragraphs if paragraph.text.startswith("Source:")
    )
    assert body.style.font.size.pt == 13.5
    assert source.runs[0].font.size.pt == 11.0


def test_empty_passage_line_is_hidden_only_in_summary_only_topic_exports():
    base = {
        "kind": "topic_study",
        "filename": "summary.pdf",
        "topics": [
            {
                "text": "Topic",
                "match_status": "unmatched",
                "source_passages": [],
                "study_material": {
                    "source_grounded": {"text": "Grounded summary.", "basis_refs": []}
                },
            }
        ],
    }
    summary_blocks = build_blocks({**base, "mode": "summaries_only"})
    source_blocks = build_blocks({**base, "mode": "text_and_summaries"})
    assert ("body", "No matching passage found") not in summary_blocks
    assert ("body", "No matching passage found") in source_blocks


def test_default_export_alignment_centers_headings_and_left_aligns_body():
    payload = _source_payload("Default body alignment text. " * 4)
    payload["mode"] = "summaries_only"
    payload["document_summary"] = "Default overall summary."

    spans = _pdf_spans(render(payload, "pdf"))
    body = next(span for span in spans if "Default body alignment" in span["text"])
    title = next(span for span in spans if span["text"] == "lecture")
    heading = next(span for span in spans if span["text"] == "Overall summary")
    assert abs(body["bbox"][0] - 56.0) < 2
    assert abs((title["bbox"][0] + title["bbox"][2]) / 2 - 297.5) < 8
    assert abs((heading["bbox"][0] + heading["bbox"][2]) / 2 - 297.5) < 8

    document = DocxDocument(io.BytesIO(render(payload, "docx")))
    body_paragraph = next(
        paragraph for paragraph in document.paragraphs if "Default body alignment" in paragraph.text
    )
    heading_paragraph = next(
        paragraph for paragraph in document.paragraphs if paragraph.text == "Overall summary"
    )
    title_paragraph = next(
        paragraph for paragraph in document.paragraphs if paragraph.style.name == "Title"
    )
    assert body_paragraph.alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert heading_paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert title_paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER
