from __future__ import annotations

import time

from app.config import get_settings
from app.services.agent_service import AgentTools
from app.services.agent_store import AgentConversation
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


def _tools(identity, document_id: str) -> AgentTools:
    conversation = AgentConversation(
        "scanned-page-conversation",
        identity["user_id"],
        last_source_id=document_id,
        last_source_kind="pdf",
        last_document_id=document_id,
    )
    return AgentTools(
        identity["user_id"],
        get_settings(),
        conversation,
        time.monotonic() + 20,
        [0],
    )


def test_scanned_page_tool_requires_approval_without_sending_image(
    client, identity, monkeypatch, caplog
):
    document_id = _document(client)
    called = False

    def fake_reader(_settings):
        nonlocal called
        called = True
        raise AssertionError("provider must not be created before consent")

    monkeypatch.setattr("app.services.agent_service.get_image_reader", fake_reader)
    result = _tools(identity, document_id).execute(
        "read_scanned_pages", {"pages": [1]}
    )
    assert result == {
        "state": "needs_approval",
        "document_id": document_id,
        "pages": [1],
        "provider": "groq",
    }
    assert called is False


def test_scanned_page_tool_persists_labelled_transcription_and_allows_edit(
    client, identity, monkeypatch
):
    document_id = _document(client)
    users.set_ai_reading_consent(identity["user_id"], "always")

    class FakeReader:
        def read_page_image(self, image, mime_type):
            assert image
            assert mime_type == "image/png"
            return "AI page transcript"

        def close(self):
            pass

    monkeypatch.setattr(
        "app.services.agent_service.get_image_reader",
        lambda _settings: FakeReader(),
    )
    result = _tools(identity, document_id).execute(
        "read_scanned_pages", {"pages": [1]}
    )
    assert result["state"] == "completed"
    assert result["pages"][0]["source"] == "ai"
    assert "AI-read transcription" in result["pages"][0]["label"]
    assert storage.pages_for(document_id)[0]["text"] == "AI page transcript"

    response = client.put(
        f"/api/v1/documents/{document_id}/pages/1/text",
        json={"text": "Corrected transcript"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "ai_edited"
    page = storage.pages_for(document_id)[0]
    assert page["text"] == "Corrected transcript"
    assert page["source"] == "ai_edited"


def test_page_text_edit_is_rejected_before_ai_read(client, identity):
    document_id = _document(client)
    response = client.put(
        f"/api/v1/documents/{document_id}/pages/1/text",
        json={"text": "forged transcript"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ai_page_text_not_editable"
