from __future__ import annotations

import logging
import time

import pytest

from app.config import get_settings
from app.services import agent_service
from app.services.agent_loop import AgentToolError
from app.services.agent_models import AgentTurn, ScriptedModel
from app.services.agent_service import AgentTools
from app.services.agent_store import AgentConversation, agent_store
from tests.helpers import build_pdf, build_png


def _upload_pdf(client, pages: list[str], name: str = "notes.pdf") -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (name, build_pdf(pages), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _create_source(
    client,
    *,
    purpose: str = "study",
    document_id: str | None = None,
    skip_ocr: bool = False,
) -> str:
    data: dict[str, str] = {"purpose": purpose}
    if document_id:
        data["document_id"] = document_id
    if skip_ocr:
        data["skip_ocr"] = "true"
    response = client.post(
        "/api/v1/topic-sources",
        data=data,
        files={"images": ("sheet.png", build_png(), "image/png")},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["purpose"] == purpose
    return body["source_id"]


def _tools(identity, *, source_id: str | None = None, kind: str | None = None, document_id: str | None = None):
    conversation = AgentConversation(
        "conversation-a",
        identity["user_id"],
        last_source_id=source_id,
        last_source_kind=kind,
        last_document_id=document_id,
    )
    return AgentTools(
        identity["user_id"],
        get_settings(),
        conversation,
        time.monotonic() + 10,
        [0],
    )


def test_converter_source_is_marked_and_excluded_from_materials(client, identity, caplog):
    source_id = _create_source(client, purpose="table", skip_ocr=True)
    tools = _tools(identity, source_id=source_id, kind="outline")

    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        result = tools.execute("list_materials", {})

    assert result["materials"] == []
    assert "converter_source excluded_from_materials count=1" in caplog.text


def test_legacy_converter_source_without_a_flag_is_not_listed(client, identity):
    source_id = _create_source(client, purpose="study", skip_ocr=True)
    tools = _tools(identity, source_id=source_id, kind="outline")

    assert tools.execute("list_materials", {})["materials"] == []


def test_converter_active_source_clears_an_older_selection(client, identity, monkeypatch):
    document_id = _upload_pdf(client, ["Old document text."], "old.pdf")
    source_id = _create_source(client, purpose="table", skip_ocr=True)
    conversation = AgentConversation(
        "conversation-converter",
        identity["user_id"],
        "old-result",
        document_id,
        ["old topic"],
        last_source_id=document_id,
        last_source_kind="pdf",
    )
    agent_store.save_conversation(conversation)
    model = ScriptedModel([AgentTurn("Fixture reply.")])
    monkeypatch.setattr(agent_service, "configured_models", lambda _settings: [model])

    _, saved, _ = agent_service.run_agent_turn(
        identity["user_id"],
        "Use the converter image.",
        conversation.conversation_id,
        get_settings(),
        [{"id": source_id, "kind": "image", "name": "sheet.png"}],
        {"id": source_id, "kind": "image"},
    )

    assert saved.last_source_id is None
    assert saved.last_source_kind is None
    assert saved.last_document_id is None
    assert saved.last_result_id is None
    assert saved.last_topics == []


def test_list_materials_only_returns_the_selected_source(client, identity, caplog):
    document_id = _upload_pdf(client, ["Selected source text."], "selected.pdf")
    _upload_pdf(client, ["Other source text."], "other.pdf")
    tools = _tools(identity, source_id=document_id, kind="pdf", document_id=document_id)

    result = tools.execute("list_materials", {})

    assert result["materials"] == [
        {
            "document_id": document_id,
            "id_kind": "document",
            "kind": "pdf",
            "name": "selected.pdf",
            "pages": 1,
            "image_heavy": False,
            "active": True,
        }
    ]


def test_list_materials_with_no_selection_does_not_read_stored_documents(client, identity, caplog):
    _upload_pdf(client, ["Hidden source text."], "hidden.pdf")
    tools = _tools(identity)

    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        result = tools.execute("list_materials", {})

    assert result["materials"] == []
    assert "agent no_material_selected" in caplog.text


def test_image_source_returns_source_id_and_wrong_document_id_is_named(client, identity):
    document_id = _upload_pdf(client, ["A document, not an outline."], "document.pdf")
    source_id = _create_source(client, document_id=document_id)
    tools = _tools(identity, source_id=source_id, kind="image")

    materials = tools.execute("list_materials", {})["materials"]
    assert materials[0]["source_id"] == source_id
    assert materials[0]["id_kind"] == "source"

    with pytest.raises(AgentToolError) as source_error:
        tools.execute("read_outline_image", {"source_id": document_id})
    assert source_error.value.code == "wrong_id_kind"
    assert source_error.value.id_kind == "document"
    assert "get_pages or find_passages" in source_error.value.message

    with pytest.raises(AgentToolError) as document_error:
        tools.execute("find_passages", {"document_id": source_id, "topics": ["x"]})
    assert document_error.value.code == "wrong_id_kind"
    assert document_error.value.id_kind == "source"
    assert "read_outline_image" in document_error.value.message


def test_converter_source_cannot_be_read_as_an_outline(client, identity):
    source_id = _create_source(client, purpose="table", skip_ocr=True)
    tools = _tools(identity, source_id=source_id, kind="image")

    with pytest.raises(AgentToolError) as error:
        tools.execute("read_outline_image", {"source_id": source_id})

    assert error.value.code == "not_outline_source"
    assert error.value.id_kind == "source"


def test_pdf_guidance_and_plain_ocr_warning(client, identity, caplog):
    document_id = _upload_pdf(client, ["A page with readable text."], "pdf.pdf")
    tools = _tools(identity, source_id=document_id, kind="pdf", document_id=document_id)
    caplog.set_level(logging.INFO, logger="app.services.agent_service")
    tools._note_ocr(
        type(
            "Report",
            (),
            {
                "pending_pages": [2],
                "failed_pages": [3],
                "failure_kinds": {3: "timeout"},
            },
        )(),
        pages={3},
    )

    warnings = tools.result_warnings()
    assert warnings == [
        "Page(s) 3 could not be read; the readable text was used."
    ]
    assert "OCR" not in warnings[0]
    assert "ocr_warning pages=3 kinds=timeout" in caplog.text

    description = next(
        tool["function"]["description"]
        for tool in agent_service.AGENT_TOOLS
        if tool["function"]["name"] == "read_outline_image"
    )
    assert "not for finding topics in a PDF or DOCX" in description
    assert "never ask for a clearer image" in agent_service._agent_system_prompt(
        get_settings()
    )


def test_detailed_summary_uses_all_text_chunks_without_word_cut(client, identity, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_max_model_calls", 6)
    monkeypatch.setattr(get_settings(), "max_summary_chunk_chars", 300)
    pages = [("Detailed source sentence. " * 60) for _ in range(10)]
    document_id = _upload_pdf(client, pages, "deck.pdf")

    class FakeSummary:
        def __init__(self):
            self.calls: list[str] = []

        def summarize(self, *, source_text, source_label, purpose="section"):
            self.calls.append(source_label)
            return "detailed " + ("summary word " * 500)

        def close(self):
            pass

    fake = FakeSummary()
    monkeypatch.setattr(agent_service, "build_ai_client", lambda *args, **kwargs: fake)
    tools = _tools(identity, source_id=document_id, kind="pdf", document_id=document_id)

    result = tools.execute(
        "summarize",
        {"document_id": document_id, "scope": "document", "length": "detailed"},
    )

    assert len(fake.calls) >= 2
    assert len(result["summary"].split()) > 1000
    assert result["basis"].startswith("based on ")
    assert "parts" in result["basis"]


def test_detailed_summary_has_its_own_budget_and_continues_from_the_next_part(
    client, identity, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_max_model_calls", 1)
    monkeypatch.setattr(settings, "agent_summary_model_calls", 3)
    monkeypatch.setattr(settings, "max_summary_chunk_chars", 50)
    document_id = _upload_pdf(
        client,
        [f"Part {number} source sentence here." for number in range(1, 6)],
        "five-parts.pdf",
    )

    class FakeSummary:
        def __init__(self):
            self.calls: list[str] = []

        def summarize(self, *, source_text, source_label, purpose="section"):
            self.calls.append(source_label)
            return f"Summary of {source_label}"

        def close(self):
            pass

    fake = FakeSummary()
    monkeypatch.setattr(agent_service, "build_ai_client", lambda *args, **kwargs: fake)
    tools = _tools(identity, source_id=document_id, kind="pdf", document_id=document_id)

    first = tools.execute(
        "summarize",
        {"document_id": document_id, "scope": "document", "length": "detailed"},
    )

    assert len(fake.calls) == 3
    assert first["has_more"] is True
    assert first["next_part"] == 4
    assert "3 of 5 parts" in first["basis"]
    assert "continue" in first["continue_prompt"].lower()

    second = tools.execute(
        "summarize",
        {
            "result_id": first["result_id"],
            "scope": "document",
            "length": "detailed",
            "start_part": first["next_part"],
        },
    )

    assert len(fake.calls) == 5
    assert second["has_more"] is False
    assert second["next_part"] is None
    assert "parts 1-5 of 5" in second["basis"]
    assert "part 1 of 5" in second["summary"]
    assert "part 5 of 5" in second["summary"]


def test_detailed_summary_covers_all_parts_with_the_summary_budget(
    client, identity, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_max_model_calls", 1)
    monkeypatch.setattr(settings, "agent_summary_model_calls", 8)
    monkeypatch.setattr(settings, "max_summary_chunk_chars", 50)
    document_id = _upload_pdf(
        client,
        [f"Part {number} source sentence here." for number in range(1, 6)],
        "all-parts.pdf",
    )

    class FakeSummary:
        def __init__(self):
            self.calls = 0

        def summarize(self, *, source_text, source_label, purpose="section"):
            self.calls += 1
            return f"Summary of {source_label}"

        def close(self):
            pass

    fake = FakeSummary()
    monkeypatch.setattr(agent_service, "build_ai_client", lambda *args, **kwargs: fake)
    tools = _tools(identity, source_id=document_id, kind="pdf", document_id=document_id)

    result = tools.execute(
        "summarize",
        {"document_id": document_id, "scope": "document", "length": "detailed"},
    )

    assert fake.calls == 5
    assert result["has_more"] is False
    assert "5 of 5 parts" in result["basis"]


def test_short_summary_still_applies_the_word_limit(client, identity, monkeypatch):
    document_id = _upload_pdf(
        client, ["Short source sentence. " * 40], "short.pdf"
    )

    class FakeSummary:
        def summarize(self, *, source_text, source_label, purpose="section"):
            return "word " * 400

        def close(self):
            pass

    monkeypatch.setattr(
        agent_service, "build_ai_client", lambda *args, **kwargs: FakeSummary()
    )
    tools = _tools(identity, source_id=document_id, kind="pdf", document_id=document_id)

    result = tools.execute(
        "summarize",
        {"document_id": document_id, "scope": "document", "length": "short"},
    )

    assert len(result["summary"].split()) <= 80


def test_pdf_upload_logs_bounded_page_metrics_without_text(client, caplog):
    with caplog.at_level(logging.INFO, logger="app.services.document_parser"):
        _upload_pdf(client, ["Confidential lecture wording.", ""], "metrics.pdf")

    assert "pdf page metrics page_count=2" in caplog.text
    assert "native_chars=" in caplog.text
    assert "image_coverage=" in caplog.text
    assert "min_pdf_chars_per_page:" in caplog.text
    assert "Confidential lecture wording" not in caplog.text


def test_empty_outline_read_says_what_was_read_and_what_to_do(
    client, identity, monkeypatch
):
    source_id = _create_source(client)

    class FakeReader:
        def interpret_outline_image(self, image, mime):
            return []

        def close(self):
            pass

    monkeypatch.setattr(
        agent_service, "get_image_reader", lambda _settings: FakeReader()
    )
    tools = _tools(identity, source_id=source_id, kind="outline")
    tools.ai_read_decision = "allow_once"

    result = tools.execute("read_outline_image", {"source_id": source_id})

    assert result["topics"] == []
    assert "checked 1 outline image" in result["question"]
    assert "document page" in result["question"]
    assert "clearer image" not in result["question"]
