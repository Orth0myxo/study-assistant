from __future__ import annotations

import time

from app.config import get_settings
from app.services import agent_service
from app.services.agent_service import AgentTools
from app.services.agent_store import AgentConversation, agent_store
from tests.helpers import build_pdf


class FixtureSummaryClient:
    def summarize(self, *, source_text: str, source_label: str, purpose: str = "section") -> str:
        return "AI-generated fixture summary."

    def close(self) -> None:
        return None


def _upload_pdf(client, pages: list[str], name: str = "notes.pdf") -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (name, build_pdf(pages), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _tools(identity, conversation: AgentConversation) -> AgentTools:
    return AgentTools(
        identity["user_id"],
        get_settings(),
        conversation,
        time.monotonic() + 10,
        [0],
    )


def test_summarize_with_no_arguments_uses_the_active_document(client, identity, monkeypatch):
    document_id = _upload_pdf(client, ["Cytology source passage."])
    conversation = AgentConversation(
        "conversation-default",
        identity["user_id"],
        last_document_id=document_id,
        last_source_id=document_id,
        last_source_kind="pdf",
    )
    agent_store.save_conversation(conversation)
    monkeypatch.setattr(
        agent_service, "build_ai_client", lambda *args, **kwargs: FixtureSummaryClient()
    )

    value = _tools(identity, conversation).execute("summarize", {})

    assert value["scope"] == "document"
    assert value["document_id"] == document_id
    assert value["summary"] == "AI-generated fixture summary."


def test_summarize_with_an_explicit_document_uses_document_scope(client, identity, monkeypatch):
    document_id = _upload_pdf(client, ["Cytology source passage."])
    conversation = AgentConversation("conversation-explicit", identity["user_id"])
    agent_store.save_conversation(conversation)
    monkeypatch.setattr(
        agent_service, "build_ai_client", lambda *args, **kwargs: FixtureSummaryClient()
    )

    value = _tools(identity, conversation).execute(
        "summarize", {"document_id": document_id}
    )

    assert value["scope"] == "document"
    assert value["document_id"] == document_id
