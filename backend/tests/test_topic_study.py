from __future__ import annotations

from app.config import get_settings
from app.services.ai_client import AiProviderError
from app.services.storage import storage
from tests.helpers import build_docx, build_pdf, build_png

PDF_CT = "application/pdf"
DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PNG_CT = "image/png"

PAGE_CALVIN = "The Calvin cycle fixes carbon dioxide."
PAGE_CALVIN_TWO = "The Calvin cycle also regenerates RuBP."
PAGE_PHOTO = "Photosynthesis converts light energy into chemical energy."


class FakeStudyAi:
    def __init__(
        self,
        *,
        summary: str = "Grounded AI study explanation.",
        beyond: str = "Optional background beyond the uploaded material.",
        fail_purposes: set[str] | None = None,
        fail_labels: set[str] | None = None,
    ) -> None:
        self.calls: list[dict[str, str]] = []
        self.summary = summary
        self.beyond = beyond
        self.fail_purposes = set(fail_purposes or set())
        self.fail_labels = set(fail_labels or set())

    def summarize(self, *, source_text, source_label, purpose="section"):
        self.calls.append(
            {"text": source_text, "label": source_label, "purpose": purpose}
        )
        if purpose in self.fail_purposes:
            raise AiProviderError(
                "The provider failed.", code="ai_provider_error"
            )
        if any(label in source_label for label in self.fail_labels):
            raise AiProviderError(
                "The provider failed.", code="ai_provider_error"
            )
        if purpose == "topic_beyond":
            return self.beyond
        return f"{self.summary} ({source_label})"


class FailingStudyAi(FakeStudyAi):
    def summarize(self, *, source_text, source_label, purpose="section"):
        self.calls.append(
            {"text": source_text, "label": source_label, "purpose": purpose}
        )
        raise AiProviderError("The provider failed.", code="ai_provider_error")


def _upload_document(
    client, data: bytes, filename: str = "lecture.pdf"
) -> str:
    content_type = DOCX_CT if filename.endswith(".docx") else PDF_CT
    response = client.post(
        "/api/v1/documents",
        files={"file": (filename, data, content_type)},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _create_source(client, document_id: str | None = None) -> dict:
    data = {"document_id": document_id} if document_id else None
    response = client.post(
        "/api/v1/topic-sources",
        files={"images": ("notes.png", build_png(64), PNG_CT)},
        data=data,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _set_transcript(client, source_id: str, text: str) -> None:
    response = client.patch(
        f"/api/v1/topic-sources/{source_id}",
        json={"transcripts": [{"index": 0, "text": text}]},
    )
    assert response.status_code == 200, response.text


def _review(client, source_id: str, topics: list[str]):
    return client.post(
        f"/api/v1/topic-sources/{source_id}/topic-review",
        json={"topics": topics},
    )


def _study(
    client,
    source_id: str,
    mode: str,
    *,
    topics=None,
    ai_scope=None,
    headers=None,
):
    body = {"mode": mode}
    if topics is not None:
        body["topics"] = topics
    if ai_scope is not None:
        body["ai_scope"] = ai_scope
    return client.post(
        f"/api/v1/topic-sources/{source_id}/study-output",
        json=body,
        headers=headers,
    )


def _enable_ai(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.8-flash")


def _use_fake(monkeypatch, fake: FakeStudyAi) -> None:
    monkeypatch.setattr(
        "app.services.topic_study.get_ai_client", lambda settings: fake
    )


def _pdf_source(client, pages=None, transcript: str = "Calvin cycle"):
    document_id = _upload_document(
        client, build_pdf(pages or [PAGE_CALVIN]), "lecture.pdf"
    )
    source = _create_source(client, document_id)
    _set_transcript(client, source["source_id"], transcript)
    return document_id, source["source_id"]


def _review_matches(client, source_id: str, topics: list[str]) -> dict:
    response = _review(client, source_id, topics)
    assert response.status_code == 200, response.text
    return response.json()


def test_mode_a_returns_exact_passages_without_ai(client, monkeypatch):
    exploding = FailingStudyAi()
    _use_fake(monkeypatch, exploding)
    monkeypatch.setattr(get_settings(), "gemini_api_key", None)
    document_id, source_id = _pdf_source(
        client,
        [PAGE_CALVIN, PAGE_CALVIN_TWO, PAGE_PHOTO],
        "Calvin cycle\nPhotosynthesis\nMitosis",
    )
    document = storage.get(document_id)
    assert document is not None

    response = _study(client, source_id, "source_passages")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["mode"] == "source_passages"
    assert body["ai"]["requested"] is False
    assert body["ai"]["generated_count"] == 0
    assert exploding.calls == []
    assert [topic["match_status"] for topic in body["topics"]] == [
        "ambiguous",
        "matched",
        "unmatched",
    ]
    assert [topic["material_status"] for topic in body["topics"]] == [
        "not_requested",
        "not_requested",
        "not_requested",
    ]
    assert all(topic["study_material"] is None for topic in body["topics"])
    assert body["topics"][0]["source_passages"][0]["passage"] == (
        document.sections[0]["raw_text"]
    )
    assert body["topics"][0]["source_passages"][1]["passage"] == (
        document.sections[1]["raw_text"]
    )
    assert body["topics"][1]["source_passages"][0]["passage"] == (
        document.sections[2]["raw_text"]
    )
    assert body["topics"][2]["source_passages"] == []

    reviewed = _review_matches(
        client, source_id, ["Calvin cycle", "Photosynthesis", "Mitosis"]
    )
    assert body["topics"][0]["source_passages"] == reviewed["topics"][0]["matches"]
    assert body["topics"][1]["source_passages"] == reviewed["topics"][1]["matches"]


def test_mode_b_generates_source_grounded_material_without_source_text(
    client, monkeypatch
):
    _enable_ai(monkeypatch)
    fake = FakeStudyAi()
    _use_fake(monkeypatch, fake)
    document_id, source_id = _pdf_source(client)

    response = _study(client, source_id, "ai_study_material")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["mode"] == "ai_study_material"
    assert body["ai"]["requested"] is True
    assert body["ai"]["generated_count"] == 1
    topic = body["topics"][0]
    assert topic["match_status"] == "matched"
    assert topic["material_status"] == "generated"
    assert topic["source_passages"] == []
    assert topic["study_material"]["generated"] is True
    grounded = topic["study_material"]["source_grounded"]
    assert grounded["text"] != PAGE_CALVIN
    assert grounded["basis_passage_ids"] == [f"{document_id}:0"]
    assert grounded["basis_refs"] == [
        {"kind": "pdf_page", "page": 1, "page_index": 0}
    ]
    assert topic["study_material"]["beyond_source"] is None
    assert len(fake.calls) == 1
    assert fake.calls[0]["purpose"] == "topic"
    assert PAGE_CALVIN in fake.calls[0]["text"]
    assert "Topic: Calvin cycle" in fake.calls[0]["label"]
    assert "Page 1" in fake.calls[0]["label"]


def test_mode_c_returns_source_and_generated_material_together(
    client, monkeypatch
):
    _enable_ai(monkeypatch)
    fake = FakeStudyAi()
    _use_fake(monkeypatch, fake)
    document_id, source_id = _pdf_source(client)
    document = storage.get(document_id)
    assert document is not None

    response = _study(client, source_id, "both")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["mode"] == "both"
    topic = body["topics"][0]
    assert topic["source_passages"][0]["passage"] == (
        document.sections[0]["raw_text"]
    )
    assert topic["source_passages"][0]["anchor"] == {
        "kind": "pdf_page",
        "page": 1,
        "page_index": 0,
    }
    assert topic["study_material"]["source_grounded"]["basis_refs"] == [
        topic["source_passages"][0]["anchor"]
    ]
    assert topic["study_material"]["beyond_source"] is None


def test_beyond_source_is_explicit_opt_in_and_separate(client, monkeypatch):
    _enable_ai(monkeypatch)
    fake = FakeStudyAi(
        summary="Source-grounded explanation.",
        beyond="Extra background from general knowledge.",
    )
    _use_fake(monkeypatch, fake)
    _, source_id = _pdf_source(client)

    response = _study(
        client,
        source_id,
        "both",
        ai_scope="source_and_beyond",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ai_scope"] == "source_and_beyond"
    assert body["ai"]["beyond_generated_count"] == 1
    topic = body["topics"][0]
    material = topic["study_material"]
    assert material["source_grounded"]["text"] == (
        f"Source-grounded explanation. (Topic: Calvin cycle; Page 1)"
    )
    assert material["beyond_source"]["text"] == (
        "Extra background from general knowledge."
    )
    assert material["beyond_source"]["grounded_in_uploaded_material"] is False
    assert [call["purpose"] for call in fake.calls] == ["topic", "topic_beyond"]
    assert PAGE_CALVIN not in fake.calls[1]["text"]
    assert "Source-grounded explanation." in fake.calls[1]["text"]


def test_beyond_source_failure_keeps_grounded_material(client, monkeypatch):
    _enable_ai(monkeypatch)
    _use_fake(monkeypatch, FakeStudyAi(fail_purposes={"topic_beyond"}))
    _, source_id = _pdf_source(client)

    response = _study(
        client,
        source_id,
        "ai_study_material",
        ai_scope="source_and_beyond",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "partial"
    assert body["ai"]["generated_count"] == 1
    assert body["ai"]["beyond_failed_count"] == 1
    topic = body["topics"][0]
    assert topic["material_status"] == "generated"
    assert topic["study_material"]["source_grounded"]
    assert topic["study_material"]["beyond_source"] is None
    assert any(w["code"] == "ai_provider_error" for w in topic["warnings"])


def test_ambiguous_and_unmatched_topics_never_call_ai(client, monkeypatch):
    _enable_ai(monkeypatch)
    exploding = FailingStudyAi()
    _use_fake(monkeypatch, exploding)
    _, source_id = _pdf_source(
        client,
        [PAGE_CALVIN, PAGE_CALVIN_TWO],
        "Calvin cycle\nMitosis",
    )

    response = _study(client, source_id, "both")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["ai"]["generated_count"] == 0
    assert body["ai"]["skipped_count"] == 2
    assert exploding.calls == []
    assert [topic["material_status"] for topic in body["topics"]] == [
        "skipped_ambiguous",
        "skipped_unmatched",
    ]
    assert all(topic["study_material"] is None for topic in body["topics"])
    assert any(w["code"] == "no_grounded_topics" for w in body["warnings"])
    assert len(body["topics"][0]["source_passages"]) == 2
    assert body["topics"][1]["source_passages"] == []


def test_provider_failure_is_explicit_and_a_still_works(client, monkeypatch):
    _enable_ai(monkeypatch)
    _use_fake(monkeypatch, FailingStudyAi())
    document_id, source_id = _pdf_source(client)
    document = storage.get(document_id)
    assert document is not None

    failed = _study(client, source_id, "both")

    assert failed.status_code == 200, failed.text
    body = failed.json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "ai_provider_error"
    assert body["topics"][0]["material_status"] == "ai_failed"
    assert body["topics"][0]["source_passages"][0]["passage"] == (
        document.sections[0]["raw_text"]
    )
    assert body["topics"][0]["study_material"] is None

    passages = _study(client, source_id, "source_passages")
    assert passages.status_code == 200
    assert passages.json()["status"] == "completed"
    assert passages.json()["topics"][0]["source_passages"][0]["passage"] == (
        document.sections[0]["raw_text"]
    )


def test_partial_source_ai_failure_keeps_successful_topic(client, monkeypatch):
    _enable_ai(monkeypatch)
    _use_fake(
        monkeypatch,
        FakeStudyAi(fail_labels={"Page 2"}),
    )
    document_id, source_id = _pdf_source(
        client,
        [PAGE_CALVIN, PAGE_PHOTO],
        "Calvin cycle\nPhotosynthesis",
    )
    document = storage.get(document_id)
    assert document is not None

    response = _study(client, source_id, "both")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "partial"
    assert body["topics"][0]["material_status"] == "generated"
    assert body["topics"][1]["material_status"] == "ai_failed"
    assert body["topics"][1]["source_passages"][0]["passage"] == (
        document.sections[1]["raw_text"]
    )


def test_ai_not_configured_blocks_b_and_c_but_not_a(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_api_key", None)
    _, source_id = _pdf_source(client)

    source_only = _study(client, source_id, "ai_study_material")
    both = _study(client, source_id, "both")
    passages = _study(client, source_id, "source_passages")

    assert source_only.status_code == 503
    assert source_only.json()["error"]["code"] == "ai_not_configured"
    assert both.status_code == 503
    assert both.json()["error"]["code"] == "ai_not_configured"
    assert passages.status_code == 200
    assert passages.json()["status"] == "completed"


def test_docx_anchor_integrity_in_both_mode(client, monkeypatch):
    _enable_ai(monkeypatch)
    fake = FakeStudyAi()
    _use_fake(monkeypatch, fake)
    document_id = _upload_document(
        client,
        build_docx(
            [
                ("heading", "Cellular Respiration"),
                ("paragraph", "Glycolysis breaks glucose into pyruvate."),
            ]
        ),
        "lecture.docx",
    )
    source = _create_source(client, document_id)
    _set_transcript(client, source["source_id"], "Glycolysis")

    response = _study(client, source["source_id"], "both")

    assert response.status_code == 200, response.text
    body = response.json()
    topic = body["topics"][0]
    passage = topic["source_passages"][0]
    assert passage["anchor"]["kind"] == "docx_section"
    assert passage["anchor"]["section_label"] == "Cellular Respiration"
    assert passage["anchor"]["paragraph_index"] == 1
    assert passage["passage"] == "Glycolysis breaks glucose into pyruvate."
    assert topic["study_material"]["source_grounded"]["basis_refs"] == [
        passage["anchor"]
    ]


def test_study_output_uses_persisted_topics_when_request_omits_them(client):
    _, source_id = _pdf_source(client)
    response = client.patch(
        f"/api/v1/topic-sources/{source_id}",
        json={"topics": ["Cellular respiration"]},
    )
    assert response.status_code == 200, response.text

    output = _study(client, source_id, "source_passages")

    assert output.status_code == 200, output.text
    assert output.json()["topics"][0]["text"] == "Cellular respiration"


def test_study_output_is_owner_scoped(client, make_user):
    _, source_id = _pdf_source(client)
    other = make_user()

    response = _study(
        client,
        source_id,
        "source_passages",
        headers=other["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "topic_source_not_found"
