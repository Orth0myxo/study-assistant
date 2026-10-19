from __future__ import annotations

import io

import pymupdf
import pytest
from docx import Document as DocxDocument

from app.config import get_settings
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
