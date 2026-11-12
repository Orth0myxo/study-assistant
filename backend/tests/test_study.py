from __future__ import annotations

import time

import pytest

from app.config import get_settings
from app.services.ai_client import (
    AiInvalidResponse,
    AiNotConfigured,
    AiProviderError,
    AiRateLimited,
)
from app.services.job_store import jobs
from tests.helpers import build_docx, build_pdf

PDF_CT = "application/pdf"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

PAGE_ONE = "Iron deficiency anemia is a microcytic hypochromic anemia."
PAGE_TWO = "Megaloblastic anemia is macrocytic with hypersegmented neutrophils."


class FakeAiClient:
    def __init__(self, *, fail=None, fail_labels=None, summary="Grounded summary."):
        self.calls: list[dict[str, str]] = []
        self.fail = fail
        self.fail_labels = set(fail_labels or [])
        self.summary = summary

    def summarize(self, *, source_text, source_label, purpose="section"):
        self.calls.append(
            {"text": source_text, "label": source_label, "purpose": purpose}
        )
        if self.fail is not None:
            raise self.fail
        if source_label in self.fail_labels:
            raise AiProviderError("The provider failed.", code="ai_provider_error")
        return f"{self.summary} ({source_label})"


@pytest.fixture(autouse=True)
def _clean_jobs():
    jobs.clear()
    yield
    jobs.clear()


def _upload(client, filename: str, data: bytes, content_type: str):
    return client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (filename, data, content_type)},
    )


def _enable_ai(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.8-flash")


def _use_fake(monkeypatch, fake: FakeAiClient) -> None:
    monkeypatch.setattr(
        "app.services.study_processor.get_ai_client", lambda settings: fake
    )


def _post_job(client, document_id: str, mode: str):
    return client.post(
        "/api/v1/study-jobs", json={"document_id": document_id, "mode": mode}
    )


def _wait_for_job(client, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    body: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/api/v1/study-jobs/{job_id}")
        assert response.status_code == 200
        body = response.json()
        if body["status"] in {"completed", "partial", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job did not finish in time: {body}")


def _pdf_document(client, pages=None) -> str:
    response = _upload(
        client, "lecture.pdf", build_pdf(pages or [PAGE_ONE, PAGE_TWO]), PDF_CT
    )
    assert response.status_code == 201
    return response.json()["document_id"]


def test_extracted_text_only_never_calls_ai(client, monkeypatch):
    class ExplodingClient:
        def summarize(self, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("AI must not be called in extracted_text_only mode")

    monkeypatch.setattr(
        "app.services.study_processor.get_ai_client",
        lambda settings: ExplodingClient(),
    )
    document_id = _pdf_document(client)

    response = _post_job(client, document_id, "extracted_text_only")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["item_count"] == 2
    assert body["document_summary"] is None
    assert body["error"] is None
    item = body["items"][0]
    assert PAGE_ONE in item["text"]
    assert item["summary"] is None
    assert item["source_refs"] == [
        {"kind": "pdf_page", "page": 1, "page_index": 0}
    ]


def test_mode_one_works_without_ai_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", None)
    document_id = _pdf_document(client)

    response = _post_job(client, document_id, "extracted_text_only")

    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_text_and_summaries_is_grounded_and_keeps_refs(client, monkeypatch):
    _enable_ai(monkeypatch)
    fake = FakeAiClient()
    _use_fake(monkeypatch, fake)
    document_id = _pdf_document(client)

    response = _post_job(client, document_id, "text_and_summaries")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["document_summary"] is not None
    first = body["items"][0]
    assert PAGE_ONE in first["text"]
    assert first["summary"] is not None
    assert first["source_refs"][0]["page"] == 1

    section_calls = [call for call in fake.calls if call["purpose"] == "section"]
    assert section_calls, "expected grounded section prompts"
    assert all(PAGE_ONE in call["text"] or PAGE_TWO in call["text"] for call in section_calls)


def test_summaries_only_omits_source_text(client, monkeypatch):
    _enable_ai(monkeypatch)
    fake = FakeAiClient()
    _use_fake(monkeypatch, fake)
    document_id = _pdf_document(client)

    response = _post_job(client, document_id, "summaries_only")

    assert response.status_code == 200
    body = response.json()
    for item in body["items"]:
        assert item["text"] is None
        assert item["summary"] is not None
        assert item["source_refs"]
    assert PAGE_ONE not in str(body["items"])


def test_ai_modes_fail_when_ai_not_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", None)
    monkeypatch.setattr(get_settings(), "groq_api_key", None)
    document_id = _pdf_document(client)

    response = _post_job(client, document_id, "text_and_summaries")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ai_not_configured"


def test_provider_error_fails_job_without_fake_summaries(client, monkeypatch):
    _enable_ai(monkeypatch)
    _use_fake(monkeypatch, FakeAiClient(fail=AiProviderError("boom")))
    document_id = _pdf_document(client, [PAGE_ONE])

    body = _post_job(client, document_id, "text_and_summaries").json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "ai_provider_error"
    assert body["document_summary"] is None
    assert all(item["summary"] is None for item in body["items"])


def test_rate_limit_fails_job_with_client_safe_code(client, monkeypatch):
    _enable_ai(monkeypatch)
    _use_fake(monkeypatch, FakeAiClient(fail=AiRateLimited("slow down")))
    document_id = _pdf_document(client, [PAGE_ONE])

    body = _post_job(client, document_id, "summaries_only").json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "ai_rate_limited"


def test_malformed_output_fails_job(client, monkeypatch):
    _enable_ai(monkeypatch)
    _use_fake(monkeypatch, FakeAiClient(fail=AiInvalidResponse("bad json")))
    document_id = _pdf_document(client, [PAGE_ONE])

    body = _post_job(client, document_id, "summaries_only").json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "ai_invalid_response"


def test_empty_summary_is_treated_as_invalid(client, monkeypatch):
    class EmptyClient:
        def summarize(self, **kwargs):
            return "  \n  "

    _enable_ai(monkeypatch)
    monkeypatch.setattr(
        "app.services.study_processor.get_ai_client",
        lambda settings: EmptyClient(),
    )
    document_id = _pdf_document(client)

    body = _post_job(client, document_id, "text_and_summaries").json()

    assert body["status"] == "failed"
    assert body["error"]["code"] == "ai_invalid_response"
    assert all(item["summary"] is None for item in body["items"])


def test_empty_overview_keeps_section_summaries(client, monkeypatch):
    class EmptyOverviewClient:
        def summarize(self, *, source_text, source_label, purpose="section"):
            if purpose == "overview":
                return ""
            return f"Section summary for {source_label}."

    _enable_ai(monkeypatch)
    monkeypatch.setattr(get_settings(), "max_summary_chunk_chars", 80)
    monkeypatch.setattr(
        "app.services.study_processor.get_ai_client",
        lambda settings: EmptyOverviewClient(),
    )
    document_id = _pdf_document(client)

    body = _post_job(client, document_id, "summaries_only").json()

    assert body["status"] == "completed"
    assert body["document_summary"] is None
    assert all(item["summary"] for item in body["items"])
    assert any(w["code"] == "ai_invalid_response" for w in body["warnings"])


def test_partial_failure_keeps_successful_summaries(client, monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(get_settings(), "max_summary_chunk_chars", 100)
    fake = FakeAiClient(fail_labels={"Page 2"})
    _use_fake(monkeypatch, fake)
    document_id = _pdf_document(client, [PAGE_ONE, PAGE_TWO, PAGE_ONE])

    body = _post_job(client, document_id, "text_and_summaries").json()

    assert body["status"] == "partial"
    assert body["items"][0]["summary"] is not None
    assert body["items"][1]["summary"] is None
    assert body["items"][1]["warnings"][0]["code"] == "ai_provider_error"
    assert any(w["code"] == "ai_provider_error" for w in body["warnings"])


def test_large_document_is_chunked_within_budget(client, monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(get_settings(), "max_summary_chunk_chars", 1000)
    fake = FakeAiClient()
    _use_fake(monkeypatch, fake)
    long_text = " ".join(["anemia"] * 800)
    response = _upload(client, "long.docx", build_docx([("para", long_text)]), DOCX_CT)
    assert response.status_code == 201
    document_id = response.json()["document_id"]

    body = _post_job(client, document_id, "text_and_summaries").json()

    assert body["status"] == "completed"
    section_calls = [call for call in fake.calls if call["purpose"] == "section"]
    assert body["item_count"] >= 4
    assert len(section_calls) >= 1
    assert max(len(item["text"]) for item in body["items"]) <= 1000
    assert sum(len(item["text"]) for item in body["items"]) >= int(len(long_text) * 0.9)
    assert body["diagnostics"]["summary_cache_hits"] >= 1
    assert all(item["source_refs"] for item in body["items"])


def test_ocr_text_is_eligible_for_study(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_page_limit", 5)
    monkeypatch.setattr(settings, "ocr_dpi", 72)
    monkeypatch.setattr(
        "app.services.document_parser.run_tesseract",
        lambda image, suffix, settings: "Recovered OCR lecture content.",
    )
    response = _upload(client, "scan.pdf", build_pdf(["", ""]), PDF_CT)
    assert response.status_code == 201
    document_id = response.json()["document_id"]

    body = _post_job(client, document_id, "extracted_text_only").json()

    assert body["status"] == "completed"
    assert "Recovered OCR lecture content." in body["items"][0]["text"]


def test_empty_document_is_rejected(client):
    response = _upload(client, "empty.docx", build_docx([]), DOCX_CT)
    document_id = response.json()["document_id"]

    result = _post_job(client, document_id, "extracted_text_only")

    assert result.status_code == 409
    assert result.json()["error"]["code"] == "no_extractable_text"


def test_missing_document_is_rejected(client):
    response = _post_job(client, "does-not-exist", "extracted_text_only")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"


def test_invalid_mode_is_a_validation_error(client):
    document_id = _pdf_document(client)

    response = _post_job(client, document_id, "topics")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unknown_job_returns_not_found(client):
    response = client.get("/api/v1/study-jobs/nope")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_large_job_is_queued_and_pollable(client, monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(get_settings(), "sync_processing_max_sections", 0)
    fake = FakeAiClient()
    _use_fake(monkeypatch, fake)
    document_id = _pdf_document(client, [PAGE_ONE])

    response = _post_job(client, document_id, "text_and_summaries")

    assert response.status_code == 202
    queued = response.json()
    assert queued["status"] == "queued"
    assert queued["items"] == []

    body = _wait_for_job(client, queued["job_id"])
    assert body["status"] == "completed"
    assert body["items"][0]["summary"] is not None



def test_overview_is_reduced_without_dropping_section_summaries(client, monkeypatch):
    _enable_ai(monkeypatch)
    monkeypatch.setattr(get_settings(), "max_summary_chunk_chars", 80)
    fake = FakeAiClient()
    _use_fake(monkeypatch, fake)
    pages = [
        f"Page {page} explains a different concept in enough detail."
        for page in range(1, 7)
    ]
    document_id = _pdf_document(client, pages)

    body = _post_job(client, document_id, "summaries_only").json()

    assert body["status"] == "completed"
    assert body["document_summary"]
    assert any(w["code"] == "overview_reduced" for w in body["warnings"])

    overview_calls = [call for call in fake.calls if call["purpose"] == "overview"]
    assert len(overview_calls) > 1
    assert all(len(call["text"]) <= 80 for call in overview_calls)
    joined = "\n\n".join(call["text"] for call in overview_calls)
    for page in range(1, 7):
        assert f"(Page {page})" in joined
