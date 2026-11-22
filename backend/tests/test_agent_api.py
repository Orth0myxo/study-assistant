import logging

from app.config import get_settings
from app.services.agent_loop import LoopResult
from app.services.agent_store import AgentConversation
from app.services.agent_store import agent_store
from app.services import agent_service
from app.services.agent_models import AgentModelError, AgentTurn, ScriptedModel
from app.services.agent_service import run_agent_turn
from tests.helpers import build_pdf


def test_agent_route_is_disabled_by_default(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_enabled", False)

    response = client.post("/api/v1/agent/chat", json={"message": "request A"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent_disabled"


def test_agent_route_returns_conversation_and_blocks(client, identity, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_enabled", True)

    def fake_turn(
        owner_id,
        message,
        conversation_id,
        _settings,
        _attachments,
        _active_source,
    ):
        assert owner_id == identity["user_id"]
        assert message == "request A"
        assert _active_source is None
        conversation = AgentConversation("conversation-a", owner_id)
        return (
            [{"type": "passages", "result_id": "result-a"}],
            conversation,
            LoopResult(
                "Fixture reply.",
                model_calls=1,
                tool_calls=1,
                status="agent_output_truncated",
            ),
        )

    monkeypatch.setattr("app.api.agent.run_agent_turn", fake_turn)
    response = client.post("/api/v1/agent/chat", json={"message": "request A"})

    assert response.status_code == 200, response.text
    assert response.json()["conversation_id"] == "conversation-a"
    assert response.json()["blocks"] == [{"type": "passages", "result_id": "result-a"}]
    assert response.json()["status"] == "agent_output_truncated"
    assert response.json()["usage"] == {"model_calls": 1, "tool_calls": 1}


def test_agent_route_passes_the_active_source_and_rejects_an_unattached_one(
    client, identity, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    seen = {}

    def fake_turn(
        owner_id,
        message,
        conversation_id,
        _settings,
        _attachments,
        active_source,
    ):
        seen["active_source"] = active_source
        return (
            [],
            AgentConversation("conversation-a", owner_id),
            LoopResult("Fixture reply."),
        )

    uploaded = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("a.pdf", build_pdf(["Active source fixture."]), "application/pdf")},
        headers=identity["headers"],
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["document_id"]

    monkeypatch.setattr("app.api.agent.run_agent_turn", fake_turn)
    response = client.post(
        "/api/v1/agent/chat",
        json={
            "message": "request A",
            "attachments": [{"id": document_id, "kind": "pdf", "name": "a.pdf"}],
            "active_source": {"id": document_id, "kind": "pdf"},
        },
    )
    assert response.status_code == 200, response.text
    assert seen["active_source"] == {"id": document_id, "kind": "pdf"}

    rejected = client.post(
        "/api/v1/agent/chat",
        json={
            "message": "request A",
            "attachments": [{"id": document_id, "kind": "pdf", "name": "a.pdf"}],
            "active_source": {"id": "doc-b", "kind": "pdf"},
        },
    )
    assert rejected.status_code == 404
    assert rejected.json()["error"]["code"] == "active_source_not_found"


def test_agent_result_endpoint_reads_owned_source_text_on_demand(client, identity, make_user, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    uploaded = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("notes.pdf", build_pdf(["Cytology source passage."]), "application/pdf")},
        headers=identity["headers"],
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["document_id"]
    result = agent_store.save_result(
        identity["user_id"], document_id, "passages",
        {"topics": [{"topic": "cytology", "passages": [{
            "section_index": 0, "page": 1, "anchor": {"kind": "pdf_page", "page": 1},
        }]}]},
    )

    response = client.get(f"/api/v1/agent/results/{result.result_id}")
    assert response.status_code == 200, response.text
    passage = response.json()["topics"][0]["passages"][0]
    assert "Cytology source passage." in passage["text"]
    assert passage["passage_id"] == f"{document_id}:0"

    other = make_user()
    hidden = client.get(
        f"/api/v1/agent/results/{result.result_id}", headers=other["headers"]
    )
    assert hidden.status_code == 404


def test_original_passage_followup_reads_the_stored_result_through_a_tool(
    client, identity, monkeypatch
):
    result = agent_store.save_result(
        identity["user_id"], "doc-a", "passages",
        {"topics": [{"topic": "cytology", "passages": [{
            "section_index": 0, "page": 1, "anchor": {"kind": "pdf_page", "page": 1},
        }]}]},
    )
    conversation = AgentConversation(
        "conversation-followup", identity["user_id"], result.result_id,
        "doc-a", ["cytology"],
    )
    agent_store.save_conversation(conversation)
    model = ScriptedModel([
        AgentTurn(tool_calls=[{
            "id": "follow-up",
            "name": "get_pages",
            "arguments": {"result_id": result.result_id},
        }]),
        AgentTurn("Fixture reply."),
    ])
    monkeypatch.setattr(agent_service, "configured_models", lambda _settings: [model])

    blocks, saved, usage = run_agent_turn(
        identity["user_id"], "request A",
        conversation.conversation_id, get_settings(),
        [{"id": "doc-a", "kind": "pdf", "name": "fixture.pdf"}],
    )

    assert blocks == [{"type": "passages", "result_id": result.result_id}]
    assert saved.conversation_id == conversation.conversation_id
    assert usage.model_calls == 2
    assert usage.tool_calls == 1


def test_switching_active_source_clears_stale_result_context(
    client, identity, monkeypatch
):
    conversation = AgentConversation(
        "conversation-source-switch",
        identity["user_id"],
        "result-from-document",
        "doc-a",
        ["cytology"],
        last_source_id="doc-a",
        last_source_kind="pdf",
    )
    agent_store.save_conversation(conversation)
    model = ScriptedModel([AgentTurn("Fixture reply.")])
    monkeypatch.setattr(agent_service, "configured_models", lambda _settings: [model])

    _, saved, _ = run_agent_turn(
        identity["user_id"],
        "Use the photo now.",
        conversation.conversation_id,
        get_settings(),
        [{"id": "image-a", "kind": "image", "name": "photo.jpg"}],
        {"id": "image-a", "kind": "image"},
    )

    assert saved.last_source_id == "image-a"
    assert saved.last_source_kind == "image"
    assert saved.last_document_id is None
    assert saved.last_result_id is None
    assert saved.last_topics == []


def test_active_source_kind_rename_keeps_same_source_context(
    client, identity, monkeypatch
):
    conversation = AgentConversation(
        "conversation-source-kind",
        identity["user_id"],
        "result-from-source",
        last_topics=["cytology"],
        last_source_id="source-a",
        last_source_kind="outline",
    )
    agent_store.save_conversation(conversation)
    model = ScriptedModel([AgentTurn("Fixture reply.")])
    monkeypatch.setattr(agent_service, "configured_models", lambda _settings: [model])

    _, saved, _ = run_agent_turn(
        identity["user_id"],
        "Use the attached images.",
        conversation.conversation_id,
        get_settings(),
        [{"id": "source-a", "kind": "image", "name": "images"}],
        {"id": "source-a", "kind": "image"},
    )

    assert saved.last_source_id == "source-a"
    assert saved.last_source_kind == "image"
    assert saved.last_topics == ["cytology"]
    assert saved.last_result_id == "result-from-source"


def test_active_source_preserves_legacy_document_context(
    client, identity, monkeypatch
):
    conversation = AgentConversation(
        "conversation-legacy-source",
        identity["user_id"],
        "result-from-document",
        "doc-a",
        ["cytology"],
    )
    agent_store.save_conversation(conversation)
    model = ScriptedModel([AgentTurn("Fixture reply.")])
    monkeypatch.setattr(agent_service, "configured_models", lambda _settings: [model])

    _, saved, _ = run_agent_turn(
        identity["user_id"],
        "Continue with the document.",
        conversation.conversation_id,
        get_settings(),
        [{"id": "doc-a", "kind": "pdf", "name": "notes.pdf"}],
        {"id": "doc-a", "kind": "pdf"},
    )

    assert saved.last_source_id == "doc-a"
    assert saved.last_source_kind == "pdf"
    assert saved.last_result_id == "result-from-document"
    assert saved.last_topics == ["cytology"]


def test_agent_route_returns_a_rate_limit_code_and_wait(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)

    def fail(*_args, **_kwargs):
        raise AgentModelError(
            "limited", code="agent_rate_limited", status_code=429,
            retry_after=17.2,
        )

    monkeypatch.setattr("app.api.agent.run_agent_turn", fail)
    response = client.post("/api/v1/agent/chat", json={"message": "request A"})

    assert response.status_code == 429
    body = response.json()["error"]
    assert body["code"] == "agent_rate_limited"
    assert body["message"] == "The AI provider is rate limited."
    assert body["details"]["retry_after_s"] == 17.2


def test_missing_chat_material_logs_storage_missing(
    client, identity, monkeypatch, caplog
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)

    with caplog.at_level(logging.WARNING, logger="app.api.agent"):
        response = client.post(
            "/api/v1/agent/chat",
            json={
                "message": "request A",
                "attachments": [
                    {"id": "missing-document", "kind": "pdf", "name": "gone.pdf"}
                ],
            },
        )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "attachment_not_found"
    assert "attachment_id=missing-document" in caplog.text
    assert "outcome=storage_missing" in caplog.text
    assert identity["user_id"] in caplog.text
