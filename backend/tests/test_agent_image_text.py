from __future__ import annotations

import time

import pytest

from app.config import Settings, get_settings
from app.services import agent_service
from app.services.agent_loop import AgentToolError, LoopResult
from app.services.agent_models import AgentTurn, ScriptedModel
from app.services.agent_service import (
    IMAGE_TEXT_LABEL,
    AgentTools,
    _agent_tools_for_chat,
    _turn_blocks,
    build_agent_export_payload,
)
from app.services.agent_store import AgentConversation, agent_store
from app.services.exporter import build_blocks
from app.services.topic_sources import topic_sources
from tests.helpers import build_png


def _create_source(client, count: int = 1) -> str:
    response = client.post(
        "/api/v1/topic-sources",
        data={"purpose": "study"},
        files=[
            ("images", (f"note-{index}.png", build_png(64), "image/png"))
            for index in range(1, count + 1)
        ],
    )
    assert response.status_code == 201, response.text
    return response.json()["source_id"]


def _tools(identity, source_id: str) -> AgentTools:
    return AgentTools(
        identity["user_id"],
        get_settings(),
        AgentConversation(
            "conversation-image-text",
            identity["user_id"],
            last_source_id=source_id,
            last_source_kind="image",
        ),
        time.monotonic() + 10,
        [0],
        [],
    )


def _names(materials, **settings) -> set[str]:
    return {
        tool["function"]["name"]
        for tool in _agent_tools_for_chat(
            materials,
            AgentConversation("conversation-1", "owner-1"),
            Settings(**settings),
        )
    }


def _configure_reader(monkeypatch, reader) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "image_reading_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(settings, "groq_vision_model", "test-vision-model")
    monkeypatch.setattr(
        agent_service, "get_image_reader", lambda _settings: reader
    )


class FakeNoteReader:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def read_note_image(self, image, mime_type):
        self.calls.append(mime_type)
        assert image
        return f"Note text {len(self.calls)}"

    def close(self):
        pass


class FakeSummary:
    def summarize(self, *, source_text, source_label, purpose="section"):
        assert source_text
        return "AI summary of the photo text."

    def close(self):
        pass


def test_image_reading_is_offered_for_local_text_or_a_vision_provider():
    local = _names(
        [{"kind": "outline", "source_id": "source-1", "has_image_text": True}]
    )
    assert {"read_image_text", "summarize"} <= local

    provider = _names(
        [{"kind": "outline", "source_id": "source-1"}],
        groq_api_key="key",
        groq_vision_model="vision-model",
    )
    assert "read_image_text" in provider

    assert "read_image_text" not in _names(
        [{"kind": "outline", "source_id": "source-1"}]
    )


def test_read_image_text_reads_two_images_and_continues(client, identity, monkeypatch):
    source_id = _create_source(client, count=3)
    reader = FakeNoteReader()
    _configure_reader(monkeypatch, reader)
    tools = _tools(identity, source_id)
    tools.ai_read_decision = "allow_once"

    first = tools.execute("read_image_text", {"source_id": source_id})

    assert reader.calls == ["image/png", "image/png"]
    assert first["read_count"] == 2
    assert first["image_count"] == 3
    assert first["has_more"] is True
    assert first["next_image"] == 3
    assert IMAGE_TEXT_LABEL in first["label"]
    assert "Note text 1" in first["text"]
    assert "start_image=3" in first["continue_prompt"]

    saved = agent_store.get_result(first["result_id"], identity["user_id"])
    assert saved is not None
    assert saved.kind == "image_text"
    assert saved.document_id is None
    assert saved.payload["label"] == IMAGE_TEXT_LABEL


def test_read_image_text_uses_local_ocr_without_ai_consent(client, identity, monkeypatch):
    source_id = _create_source(client)
    topic_sources.update_for_owner(
        source_id,
        identity["user_id"],
        transcripts={0: "Local OCR note text"},
    )
    monkeypatch.setattr(get_settings(), "image_reading_provider", "none")
    tools = _tools(identity, source_id)
    tools.ai_read_decision = "deny"

    result = tools.execute("read_image_text", {"source_id": source_id})

    assert "Local OCR note text" in result["text"]
    assert result["read_count"] == 1
    assert result["has_more"] is False


def test_read_image_text_without_a_provider_or_local_text_is_rejected(
    client, identity, monkeypatch
):
    source_id = _create_source(client)
    topic_sources.update_for_owner(
        source_id,
        identity["user_id"],
        transcripts={0: ""},
    )
    monkeypatch.setattr(get_settings(), "image_reading_provider", "none")
    tools = _tools(identity, source_id)

    with pytest.raises(AgentToolError) as error:
        tools.execute("read_image_text", {"source_id": source_id})

    assert error.value.code == "vision_not_configured"


def test_image_text_approval_resumes_the_same_read(client, identity, monkeypatch):
    source_id = _create_source(client, count=2)
    reader = FakeNoteReader()
    _configure_reader(monkeypatch, reader)
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    model = ScriptedModel(
        [
            AgentTurn(
                tool_calls=[
                    {
                        "id": "read-1",
                        "name": "read_image_text",
                        "arguments": {"source_id": source_id},
                    }
                ]
            ),
            AgentTurn("The photos describe the lecture."),
        ]
    )
    monkeypatch.setattr(agent_service, "configured_models", lambda _settings: [model])

    response = client.post(
        "/api/v1/agent/chat",
        json={
            "message": "Summarize my photos",
            "attachments": [
                {"id": source_id, "kind": "image", "name": "notes.png"}
            ],
            "active_source": {"id": source_id, "kind": "image"},
        },
    )
    assert response.status_code == 200, response.text
    paused = response.json()
    assert paused["status"] == "needs_approval"
    assert reader.calls == []
    approval = next(
        block for block in paused["blocks"] if block["type"] == "approval"
    )
    assert approval["source_id"] == source_id

    resumed = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": approval["pending_id"], "decision": "allow_once"},
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["status"] is None
    assert len(reader.calls) == 2
    assert any(block["type"] == "image_text" for block in body["blocks"])


def test_image_text_summary_and_exports_keep_the_ai_label(client, identity, monkeypatch):
    source_id = _create_source(client)
    topic_sources.update_for_owner(
        source_id,
        identity["user_id"],
        transcripts={0: "Photo words"},
    )
    monkeypatch.setattr(get_settings(), "image_reading_provider", "none")
    tools = _tools(identity, source_id)
    read = tools.execute("read_image_text", {"source_id": source_id})

    monkeypatch.setattr(
        agent_service, "build_ai_client", lambda *args, **kwargs: FakeSummary()
    )
    summary = tools.execute(
        "summarize",
        {"result_id": read["result_id"], "scope": "result", "length": "short"},
    )

    assert summary["document_id"] is None
    assert summary["basis"].startswith("AI-read photo text;")
    saved_summary = agent_store.get_result(
        summary["result_id"], identity["user_id"]
    )
    assert saved_summary is not None
    assert saved_summary.document_id is None

    verbatim = build_agent_export_payload(
        identity["user_id"], read["result_id"], ["verbatim"]
    )
    assert verbatim["topics"][0]["source_passages"][0]["label"] == IMAGE_TEXT_LABEL
    assert any(
        kind == "h3" and value == IMAGE_TEXT_LABEL
        for kind, value in build_blocks(verbatim)
    )

    summary_export = build_agent_export_payload(
        identity["user_id"], summary["result_id"], ["summary"]
    )
    assert summary_export["mode"] == "summaries_only"
    assert summary_export["warnings"][0]["message"] == IMAGE_TEXT_LABEL
    assert IMAGE_TEXT_LABEL in str(build_blocks(summary_export))


def test_turn_blocks_hide_photo_text_when_the_summary_hides_source():
    loop = LoopResult(
        reply="Summary ready.",
        tool_results=[
            {
                "name": "read_image_text",
                "result": {
                    "result_id": "image-1",
                    "kind": "image_text",
                    "source_id": "source-1",
                    "text": "Photo words",
                    "label": IMAGE_TEXT_LABEL,
                },
            },
            {
                "name": "summarize",
                "result": {
                    "result_id": "summary-1",
                    "kind": "summary",
                    "source_result_id": "image-1",
                    "summary": "Short summary",
                    "show_source": False,
                },
            },
        ],
    )

    blocks = _turn_blocks(loop)

    assert [block["type"] for block in blocks] == ["summary"]
