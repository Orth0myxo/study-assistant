from __future__ import annotations

import io

import pymupdf
from docx import Document
import pytest

from app.config import get_settings
from app.errors import ApiError
from app.services.document_edit import (
    apply_safe_word_removals,
    require_approved_edit_review,
)
from tests.helpers import build_docx, build_pdf


WORDS = "very really quite simply basically actually very really quite simply"
SOURCE = (
    f"These observations are {WORDS} useful for study. "
    "A catalyst is a substance that changes reaction rate. "
    "Opportunity cost means the value of the next best alternative."
)


def test_exact_ten_word_edit_request_preserves_subject_definitions_and_exports(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.8-flash")

    def proposed_spans(_self, source_text, count):
        assert count == 10
        spans = []
        search_from = source_text.index(WORDS)
        for word in WORDS.split():
            start = source_text.index(word, search_from)
            spans.append({
                "start": start,
                "end": start + len(word),
                "word": word,
                "safe": True,
                "confidence": "high",
                "justification": "This is a redundant intensifier in this sentence.",
            })
            search_from = start + len(word)
        return spans

    def approved_review(_self, *, original_text, edited_text, removals):
        assert "A catalyst is a substance that changes reaction rate." in original_text
        assert "Opportunity cost means the value of the next best alternative." in original_text
        assert edited_text != original_text
        assert len(removals) == 10
        return {
            "approved": True,
            "confidence": "high",
            "issues": [],
            "justification": "Both subject-specific definitions remain unchanged.",
        }

    monkeypatch.setattr(
        "app.services.ai_client.GeminiClient.propose_safe_word_removals",
        proposed_spans,
    )
    monkeypatch.setattr(
        "app.services.ai_client.GeminiClient.review_document_edit",
        approved_review,
    )
    upload = client.post(
        "/api/v1/documents", params={"wait": True},
        files={
            "file": (
                "lecture.docx",
                build_docx([("paragraph", SOURCE)]),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]
    edited = client.post(f"/api/v1/documents/{document_id}/edit", json={"remove_words": 10})
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["removed_count"] == 10
    assert len(body["removed_words"]) == 10
    assert all(word in WORDS for word in body["removed_words"])
    assert "A catalyst is a substance that changes reaction rate." in body["edited_text"]
    assert "Opportunity cost means the value of the next best alternative." in body["edited_text"]
    job_id = body["job_id"]

    pdf = client.get(f"/api/v1/study-jobs/{job_id}/export?format=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    parsed_pdf = pymupdf.open(stream=pdf.content, filetype="pdf")
    try:
        exported_pdf_text = "\n".join(page.get_text() for page in parsed_pdf)
        assert "A catalyst is a substance that changes reaction rate." in exported_pdf_text
        assert "Opportunity cost means the value of the next best alternative." in exported_pdf_text
        assert not any(word in " ".join(page.get_text().lower() for page in parsed_pdf).split() for word in WORDS.split())
    finally:
        parsed_pdf.close()
    docx = client.get(f"/api/v1/study-jobs/{job_id}/export?format=docx")
    assert docx.status_code == 200
    parsed_docx = Document(io.BytesIO(docx.content))
    exported_docx_text = "\n".join(paragraph.text for paragraph in parsed_docx.paragraphs)
    assert "A catalyst is a substance that changes reaction rate." in exported_docx_text
    assert "Opportunity cost means the value of the next best alternative." in exported_docx_text
    assert "original_text" not in body["edited_text"]


def test_edit_requests_clarification_when_not_enough_safe_words():
    text = "The definition of velocity is displacement divided by time."
    with pytest.raises(ApiError) as error:
        apply_safe_word_removals(text, 1, [])
    assert error.value.code == "safe_edit_unavailable"


@pytest.mark.parametrize(
    "text",
    [
        # Biology: generic copular definition.
        "A catalyst is basically a substance that changes reaction rate.",
        # Economics: direct definition.
        "Opportunity cost means the value of the next best alternative.",
        # Law: definition phrased as a named doctrine.
        "The doctrine of consideration is really a legal concept in contract law.",
        # History: named concept plus explanatory causal clause.
        "The Treaty of Versailles was signed because the parties accepted its terms.",
        # Literature: definition and explanatory description.
        "The unreliable narrator is simply a storytelling voice that limits what readers know.",
        # Engineering: technical term and explanation.
        "The relay is really an electrical switch that controls another circuit.",
        "The result x = y is expected to hold for the named concept.",
        # General claims whose emphasis changes their meaning.
        "The principle is really that pressure increases with temperature.",
        "A material is very stable and conducts heat.",
        "This process simply converts energy because the reaction requires it.",
    ],
)
def test_edit_refuses_to_remove_words_from_subject_specific_context(text):
    with pytest.raises(ApiError) as error:
        apply_safe_word_removals(text, 1, [])
    assert error.value.code == "safe_edit_unavailable"


def test_edit_rejects_provider_spans_that_do_not_match_exact_source_words():
    with pytest.raises(ApiError) as error:
        apply_safe_word_removals(
            "A safe preview.",
            1,
            [{
                "start": 2,
                "end": 6,
                "word": "safe",
                "safe": True,
                "confidence": "high",
                "justification": "A review judged this word redundant.",
            }],
        )
    assert error.value.code == "safe_edit_unavailable"


def test_edit_rejects_overlapping_provider_spans():
    source = "Alpha beta."
    safe_fields = {
        "safe": True,
        "confidence": "high",
        "justification": "The word is redundant in context.",
    }
    proposals = [
        {"start": 0, "end": 5, "word": "Alpha", **safe_fields},
        {"start": 0, "end": 5, "word": "Alpha", **safe_fields},
    ]
    with pytest.raises(ApiError) as error:
        apply_safe_word_removals(source, 2, proposals)
    assert error.value.code == "safe_edit_unavailable"
    assert "overlapped" in error.value.message


@pytest.mark.parametrize(
    "verdict",
    [
        {"confidence": "high", "justification": "No effect."},
        {"safe": False, "confidence": "high", "justification": "It changes meaning."},
        {"safe": True, "confidence": "low", "justification": "Maybe harmless."},
        {"safe": True, "confidence": "high", "justification": "  "},
    ],
)
def test_edit_rejects_missing_or_negative_per_word_safety_verdicts(verdict):
    proposal = {"start": 4, "end": 9, "word": "draft", **verdict}
    with pytest.raises(ApiError) as error:
        apply_safe_word_removals("The draft is clear.", 1, [proposal])
    assert error.value.code == "safe_edit_unavailable"


def test_edit_rejects_source_valid_but_semantically_unsafe_proposal():
    source = "The treatment is only effective for adults."
    word = "only"
    start = source.index(word)
    proposal = {
        "start": start,
        "end": start + len(word),
        "word": word,
        "safe": False,
        "confidence": "high",
        "justification": "Removing it would broaden the treatment claim.",
    }
    with pytest.raises(ApiError) as error:
        apply_safe_word_removals(source, 1, [proposal])
    assert error.value.code == "safe_edit_unavailable"


@pytest.mark.parametrize(
    "review",
    [
        {"approved": False, "confidence": "high", "issues": ["Definition changed."], "justification": "Rejected."},
        {"approved": True, "confidence": "low", "issues": [], "justification": "Uncertain."},
        {"approved": True, "confidence": "high", "issues": ["Explanation is incomplete."], "justification": "Issue found."},
        {"approved": True, "confidence": "high", "issues": []},
    ],
)
def test_edit_rejects_failed_or_uncertain_second_stage_review(review):
    with pytest.raises(ApiError) as error:
        require_approved_edit_review(review)
    assert error.value.code == "safe_edit_unavailable"


def test_failed_second_stage_review_creates_no_edit_job(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.8-flash")

    def propose(_self, *, source_text, count):
        assert count == 1
        word = "harmless"
        start = source_text.index(word)
        return [{
            "start": start,
            "end": start + len(word),
            "word": word,
            "safe": True,
            "confidence": "high",
            "justification": "The model judged it redundant.",
        }]

    def reject_review(_self, *, original_text, edited_text, removals):
        assert original_text != edited_text
        assert len(removals) == 1
        return {
            "approved": False,
            "confidence": "high",
            "issues": ["The word is part of a technical qualification."],
            "justification": "The comparison found a meaning change.",
        }

    monkeypatch.setattr(
        "app.services.ai_client.GeminiClient.propose_safe_word_removals",
        propose,
    )
    monkeypatch.setattr(
        "app.services.ai_client.GeminiClient.review_document_edit",
        reject_review,
    )
    upload = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "notes.pdf",
                build_pdf(["These notes include a harmless extra word."]),
                "application/pdf",
            )
        },
    )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document_id"]

    response = client.post(
        f"/api/v1/documents/{document_id}/edit",
        json={"remove_words": 1},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "safe_edit_unavailable"
    jobs = client.get("/api/v1/study-jobs")
    assert jobs.status_code == 200
    assert all(job["document_id"] != document_id for job in jobs.json())
