from __future__ import annotations

import time

from app.config import get_settings
from app.services.agent_service import AgentTools
from app.services.agent_store import AgentConversation, agent_store
from tests.helpers import build_docx

DOCX_CT = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _upload_docx(client, blocks: list[tuple[str, object]], name: str = "notes.docx") -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (name, build_docx(blocks), DOCX_CT)},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _tools(identity, document_id: str) -> AgentTools:
    conversation = AgentConversation(
        "conversation-word",
        identity["user_id"],
        last_document_id=document_id,
        last_source_id=document_id,
        last_source_kind="docx",
    )
    return AgentTools(
        identity["user_id"],
        get_settings(),
        conversation,
        time.monotonic() + 10,
        [0],
    )


def test_get_pages_on_a_word_file_returns_nearby_paragraph_context(client, identity):
    document_id = _upload_docx(
        client, [("para", f"Paragraph {number}") for number in range(1, 11)]
    )

    value = _tools(identity, document_id).execute("get_pages", {"pages": [5]})

    assert value["section_selection"] is True
    assert value["pages"] == []
    assert value["sections"] == [3, 4, 5, 6, 7]
    assert "Showing paragraphs 3-7 of 10 with nearby context." in value["note"]
    assert value["extend_above"] == "1-2"
    assert value["extend_below"] == "8-10"
    stored = agent_store.get_result(value["result_id"], identity["user_id"])
    refs = stored.payload["topics"][0]["passages"]
    assert [ref["section_index"] for ref in refs] == [2, 3, 4, 5, 6]


def test_get_pages_on_a_word_file_accepts_a_heading(client, identity):
    document_id = _upload_docx(
        client,
        [
            ("heading", "Topic One"),
            ("para", "First topic text."),
            ("heading", "Topic Two"),
            ("para", "Second topic text."),
        ],
    )

    value = _tools(identity, document_id).execute("get_pages", {"range": "Topic Two"})

    assert value["section_selection"] is True
    assert value["pages"] == []
    assert 3 in value["sections"]
    stored = agent_store.get_result(value["result_id"], identity["user_id"])
    assert stored.payload["topics"][0]["passages"]
