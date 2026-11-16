from app.config import get_settings
from app.services.agent_loop import LoopResult
from app.services.agent_store import AgentConversation
from app.services.agent_store import agent_store
from app.services.agent_service import run_agent_turn
from tests.helpers import build_pdf


def test_agent_route_is_disabled_by_default(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_enabled", False)

    response = client.post("/api/v1/agent/chat", json={"message": "hello"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "agent_disabled"


def test_agent_route_returns_conversation_and_blocks(client, identity, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_enabled", True)

    def fake_turn(owner_id, message, conversation_id, _settings, _attachments):
        assert owner_id == identity["user_id"]
        assert message == "Page 2"
        conversation = AgentConversation("conversation-a", owner_id)
        return (
            [{"type": "passages", "result_id": "result-a"}],
            conversation,
            LoopResult("Here is page 2.", model_calls=1, tool_calls=1),
        )

    monkeypatch.setattr("app.api.agent.run_agent_turn", fake_turn)
    response = client.post("/api/v1/agent/chat", json={"message": "Page 2"})

    assert response.status_code == 200, response.text
    assert response.json()["conversation_id"] == "conversation-a"
    assert response.json()["blocks"] == [{"type": "passages", "result_id": "result-a"}]
    assert response.json()["usage"] == {"model_calls": 1, "tool_calls": 1}


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
    assert "Cytology source passage." in response.json()["topics"][0]["passages"][0]["text"]

    other = make_user()
    hidden = client.get(
        f"/api/v1/agent/results/{result.result_id}", headers=other["headers"]
    )
    assert hidden.status_code == 404


def test_original_passage_followup_returns_last_result_without_model_or_tools(client, identity):
    result = agent_store.save_result(
        identity["user_id"], "doc-a", "passages", {"topics": []}
    )
    conversation = AgentConversation(
        "conversation-followup", identity["user_id"], result.result_id,
        "doc-a", ["vaginal cytology"],
    )
    agent_store.save_conversation(conversation)

    blocks, saved, usage = run_agent_turn(
        identity["user_id"], "Ok give me the original passages instead",
        conversation.conversation_id, get_settings(),
    )

    assert blocks == [{"type": "passages", "result_id": result.result_id}]
    assert saved.conversation_id == conversation.conversation_id
    assert usage.model_calls == 0
    assert usage.tool_calls == 0
