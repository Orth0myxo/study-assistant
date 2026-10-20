from __future__ import annotations

import io
import time

import pymupdf
import pytest
from docx import Document as DocxDocument

from app.config import get_settings
from app.services.exporter import render
from app.services.job_store import jobs
from tests.helpers import build_pdf

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


def _docx_text(data: bytes) -> str:
    document = DocxDocument(io.BytesIO(data))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


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
    assert "Source text" in pdf_text
    assert "AI summary" in pdf_text
    assert PAGE_ONE in pdf_text
    assert "Grounded summary" in pdf_text

    docx_response = client.get(
        f"/api/v1/study-jobs/{job['job_id']}/export?format=docx"
    )
    assert docx_response.status_code == 200
    docx_text = _docx_text(docx_response.content)
    assert "Source text" in docx_text
    assert "AI summary" in docx_text
    assert PAGE_ONE in docx_text


def test_export_summaries_only_excludes_full_source(client, monkeypatch):
    job = _completed_job(client, monkeypatch, "summaries_only")

    pdf_response = client.get(f"/api/v1/study-jobs/{job['job_id']}/export?format=pdf")
    assert pdf_response.status_code == 200
    pdf_text = _pdf_text(pdf_response.content)
    assert PAGE_ONE not in pdf_text
    assert PAGE_TWO not in pdf_text
    assert "Grounded summary" in pdf_text
    assert "Page 1" in pdf_text

    docx_response = client.get(
        f"/api/v1/study-jobs/{job['job_id']}/export?format=docx"
    )
    docx_text = _docx_text(docx_response.content)
    assert PAGE_ONE not in docx_text
    assert "Grounded summary" in docx_text


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


def test_export_not_ready_job_is_409(client):
    jobs.put(
        "queued-job",
        {
            "job_id": "queued-job",
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

    assert "AI summaries only - status: completed" in text
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
