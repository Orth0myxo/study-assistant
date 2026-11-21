from __future__ import annotations

from app.config import get_settings
from app.services import agent_service
from app.services.agent_models import AgentTurn, ScriptedModel
from app.services.agent_store import agent_store
from app.services.storage import storage
from app.services.users import users
from tests.helpers import build_pdf


def _document(client) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("scan.pdf", build_pdf(["Native page text"]), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _chat(client, document_id: str, message: str = "Read page 1") -> dict:
    response = client.post(
        "/api/v1/agent/chat",
        json={
            "message": message,
            "attachments": [
                {"id": document_id, "kind": "pdf", "name": "scan.pdf"}
            ],
            "active_source": {"id": document_id, "kind": "pdf"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _pending_id(body: dict) -> str:
    block = next(block for block in body["blocks"] if block["type"] == "approval")
    return block["pending_id"]


class FakeReader:
    def __init__(self) -> None:
        self.calls = 0

    def read_page_image(self, image, mime_type):
        self.calls += 1
        assert image
        assert mime_type == "image/png"
        return "AI page transcript"

    def close(self):
        pass


def _configure(monkeypatch, turns):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    model = ScriptedModel(turns)
    monkeypatch.setattr(agent_service, "configured_models", lambda _settings: [model])
    reader = FakeReader()
    monkeypatch.setattr(
        "app.services.agent_service.get_image_reader",
        lambda _settings: reader,
    )
    return model, reader


def test_allow_once_resumes_the_pending_page_read(client, monkeypatch):
    document_id = _document(client)
    model, reader = _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {"id": "read-1", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn("The page describes native text."),
        ],
    )

    paused = _chat(client, document_id)
    assert paused["status"] == "needs_approval"
    assert paused["usage"] == {"model_calls": 1, "tool_calls": 1}
    pending_id = _pending_id(paused)
    assert paused["blocks"][0]["provider"] == "groq"
    assert paused["blocks"][0]["pages"] == [1]
    assert reader.calls == 0
    assert model.calls == 1
    assert storage.pages_for(document_id)[0]["text"] == "Native page text"

    resumed = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": pending_id, "decision": "allow_once"},
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] is None
    assert body["reply"] == "The page describes native text."
    assert body["usage"] == {"model_calls": 3, "tool_calls": 1}
    assert any(block["type"] == "ai_read" for block in body["blocks"])
    assert reader.calls == 1
    assert model.calls == 2
    assert storage.pages_for(document_id)[0]["text"] == "AI page transcript"


def test_deny_resumes_without_sending_the_page(client, monkeypatch):
    document_id = _document(client)
    _, reader = _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {"id": "read-1", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn("I did not read the page."),
        ],
    )
    pending_id = _pending_id(_chat(client, document_id))

    resumed = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": pending_id, "decision": "deny"},
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["reply"] == "I did not read the page."
    assert not any(block["type"] == "ai_read" for block in body["blocks"])
    assert reader.calls == 0
    assert storage.pages_for(document_id)[0]["text"] == "Native page text"


def test_remember_resumes_once_and_skips_later_approval(client, identity, monkeypatch):
    document_id = _document(client)
    _, reader = _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {"id": "read-1", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn("The page was read."),
            AgentTurn(
                tool_calls=[
                    {"id": "read-2", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn("The page was read again."),
        ],
    )
    paused = _chat(client, document_id)
    pending_id = _pending_id(paused)

    resumed = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": pending_id, "decision": "remember"},
    )
    assert resumed.status_code == 200, resumed.text
    assert users.get_ai_reading_consent(identity["user_id"]) == "always"

    second = client.post(
        "/api/v1/agent/chat",
        json={
            "message": "Read it again",
            "conversation_id": resumed.json()["conversation_id"],
            "attachments": [
                {"id": document_id, "kind": "pdf", "name": "scan.pdf"}
            ],
        },
    )
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["status"] is None
    assert not any(block["type"] == "approval" for block in body["blocks"])
    assert any(block["type"] == "ai_read" for block in body["blocks"])
    assert reader.calls == 2


def test_resume_is_owner_scoped(client, make_user, monkeypatch):
    document_id = _document(client)
    _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {"id": "read-1", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn("The owner resumed the read."),
        ],
    )
    pending_id = _pending_id(_chat(client, document_id))
    other = make_user()

    denied = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": pending_id, "decision": "allow_once"},
        headers=other["headers"],
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "agent_pending_expired"

    allowed = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": pending_id, "decision": "allow_once"},
    )
    assert allowed.status_code == 200, allowed.text


def test_expired_approval_returns_a_safe_error(client, monkeypatch):
    document_id = _document(client)
    _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {"id": "read-1", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn("This reply must not be used."),
        ],
    )
    monkeypatch.setattr(get_settings(), "agent_pending_ttl_s", 0)
    pending_id = _pending_id(_chat(client, document_id))

    resumed = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": pending_id, "decision": "allow_once"},
    )
    assert resumed.status_code == 409
    assert resumed.json()["error"]["code"] == "agent_pending_expired"


def test_lost_pending_turn_returns_a_safe_error(client, monkeypatch):
    document_id = _document(client)
    _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {"id": "read-1", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn("This reply must not be used."),
        ],
    )
    pending_id = _pending_id(_chat(client, document_id))
    agent_store.clear()

    resumed = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": pending_id, "decision": "remember"},
    )
    assert resumed.status_code == 409
    assert resumed.json()["error"]["code"] == "agent_pending_expired"


def test_never_preference_blocks_the_read_without_an_approval(
    client, identity, monkeypatch
):
    document_id = _document(client)
    users.set_ai_reading_consent(identity["user_id"], "never")
    _, reader = _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {"id": "read-1", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn("AI reading is turned off in settings."),
        ],
    )

    body = _chat(client, document_id)
    assert body["status"] is None
    assert not any(block["type"] == "approval" for block in body["blocks"])
    assert reader.calls == 0
    assert storage.pages_for(document_id)[0]["text"] == "Native page text"
