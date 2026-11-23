from __future__ import annotations

from app.config import get_settings
from app.services import agent_service
from app.services.agent_models import AgentTurn, ScriptedModel
from app.services.agent_store import agent_store
from tests.helpers import build_docx, build_pdf, build_png


class FakeReader:
    def __init__(self) -> None:
        self.outline_calls = 0
        self.page_calls = 0

    def interpret_outline_image(self, image, mime_type):
        self.outline_calls += 1
        assert image
        assert mime_type == "image/png"
        return ["Cytology"]

    def read_page_image(self, image, mime_type):
        self.page_calls += 1
        assert image
        assert mime_type == "image/png"
        return "AI page transcript"

    def close(self):
        pass


def _upload_pdf(client, pages: list[str], name: str = "notes.pdf") -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (name, build_pdf(pages), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _upload_docx(client, text: str = "Word source text.", name: str = "notes.docx") -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                name,
                build_docx([("heading", "Cytology"), ("paragraph", text)]),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _create_outline(client) -> str:
    response = client.post(
        "/api/v1/topic-sources",
        data={"purpose": "study"},
        files={"images": ("sheet.png", build_png(), "image/png")},
    )
    assert response.status_code == 201, response.text
    return response.json()["source_id"]


def _configure(monkeypatch, turns):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_enabled", True)
    monkeypatch.setattr(settings, "groq_api_key", "test-key")
    monkeypatch.setattr(settings, "groq_vision_model", "test-vision-model")
    model = ScriptedModel(turns)
    monkeypatch.setattr(agent_service, "configured_models", lambda _settings: [model])
    reader = FakeReader()
    monkeypatch.setattr(
        agent_service, "get_image_reader", lambda _settings: reader
    )
    return model, reader


def _install_recording_tools(monkeypatch):
    class RecordingTools(agent_service.AgentTools):
        instances = []

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.executed = []
            RecordingTools.instances.append(self)

        def execute(self, name, raw):
            value = super().execute(name, raw)
            self.executed.append((name, value))
            return value

    monkeypatch.setattr(agent_service, "AgentTools", RecordingTools)
    return RecordingTools


def _chat(client, attachments, active_source, message="Read the attached material"):
    response = client.post(
        "/api/v1/agent/chat",
        json={
            "message": message,
            "attachments": attachments,
            "active_source": active_source,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _pending_id(body):
    return next(
        block["pending_id"] for block in body["blocks"] if block["type"] == "approval"
    )


def _resume(client, pending_id, decision="allow_once"):
    response = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": pending_id, "decision": decision},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_pdf_and_outline_resume_keeps_document_tools(client, monkeypatch, caplog):
    document_id = _upload_pdf(client, ["Cytology studies cells."])
    source_id = _create_outline(client)
    _, reader = _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {
                        "id": "outline-1",
                        "name": "read_outline_image",
                        "arguments": {"source_id": source_id},
                    }
                ]
            ),
            AgentTurn(
                tool_calls=[
                    {
                        "id": "passages-1",
                        "name": "find_passages",
                        "arguments": {
                            "document_id": document_id,
                            "topics": ["Cytology"],
                        },
                    }
                ]
            ),
            AgentTurn("I found the source passage."),
        ],
    )
    RecordingTools = _install_recording_tools(monkeypatch)

    paused = _chat(
        client,
        [
            {"id": document_id, "kind": "pdf", "name": "notes.pdf"},
            {"id": source_id, "kind": "outline", "name": "sheet.png"},
        ],
        {"id": source_id, "kind": "outline"},
    )
    resumed = _resume(client, _pending_id(paused))

    assert resumed["status"] is None
    assert any(block["type"] == "passages" for block in resumed["blocks"])
    assert RecordingTools.instances[-1].offered_names == RecordingTools.instances[0].offered_names
    assert "find_passages" in RecordingTools.instances[-1].offered_names
    assert "outcome=tool_not_offered" not in caplog.text
    assert "source=stored outcome=ready" in caplog.text
    assert reader.outline_calls == 1


def test_word_active_source_resume_runs_outline_call(client, monkeypatch, caplog):
    document_id = _upload_docx(client)
    source_id = _create_outline(client)
    _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {
                        "id": "outline-1",
                        "name": "read_outline_image",
                        "arguments": {"source_id": source_id},
                    }
                ]
            ),
            AgentTurn("The outline was read."),
        ],
    )
    RecordingTools = _install_recording_tools(monkeypatch)

    paused = _chat(
        client,
        [
            {"id": document_id, "kind": "docx", "name": "notes.docx"},
            {"id": source_id, "kind": "outline", "name": "sheet.png"},
        ],
        {"id": document_id, "kind": "docx"},
    )
    resumed = _resume(client, _pending_id(paused))

    assert resumed["status"] is None
    assert resumed["reply"] == "The outline was read."
    assert "outcome=tool_not_offered" not in caplog.text
    assert RecordingTools.instances[-1].offered_names == RecordingTools.instances[0].offered_names
    assert "read_outline_image" in RecordingTools.instances[-1].offered_names


def test_legacy_pending_uses_fallback_tools(client, identity, monkeypatch, caplog):
    document_id = _upload_pdf(client, ["Cytology studies cells."])
    _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {"id": "read-1", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn("The page was read."),
        ],
    )
    paused = _chat(
        client,
        [{"id": document_id, "kind": "pdf", "name": "notes.pdf"}],
        {"id": document_id, "kind": "pdf"},
    )
    pending_id = _pending_id(paused)
    pending = agent_store.get_pending(pending_id, identity["user_id"])
    assert pending is not None
    pending.tool_names = []
    pending.attachments = []

    resumed = _resume(client, pending_id)

    assert resumed["status"] is None
    assert "source=fallback outcome=ready" in caplog.text


def test_pending_tool_missing_returns_expired(client, identity, monkeypatch, caplog):
    document_id = _upload_pdf(client, ["Cytology studies cells."])
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
    paused = _chat(
        client,
        [{"id": document_id, "kind": "pdf", "name": "notes.pdf"}],
        {"id": document_id, "kind": "pdf"},
    )
    pending_id = _pending_id(paused)
    pending = agent_store.get_pending(pending_id, identity["user_id"])
    assert pending is not None
    pending.tool_names = ["list_materials"]
    pending.attachments = [{"id": document_id, "kind": "pdf"}]

    response = client.post(
        "/api/v1/agent/resume",
        json={"pending_id": pending_id, "decision": "allow_once"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "agent_pending_expired"
    assert "outcome=pending_tool_missing" in caplog.text


def test_single_pdf_resume_keeps_tools(client, monkeypatch):
    document_id = _upload_pdf(client, ["Cytology studies cells."])
    _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {"id": "read-1", "name": "read_scanned_pages", "arguments": {"pages": [1]}}
                ]
            ),
            AgentTurn(
                tool_calls=[
                    {
                        "id": "passages-1",
                        "name": "find_passages",
                        "arguments": {
                            "document_id": document_id,
                            "topics": ["Cytology"],
                        },
                    }
                ]
            ),
            AgentTurn("The passage is in the document."),
        ],
    )
    RecordingTools = _install_recording_tools(monkeypatch)

    paused = _chat(
        client,
        [{"id": document_id, "kind": "pdf", "name": "notes.pdf"}],
        {"id": document_id, "kind": "pdf"},
    )
    resumed = _resume(client, _pending_id(paused))

    assert resumed["status"] is None
    assert any(block["type"] == "passages" for block in resumed["blocks"])
    assert RecordingTools.instances[-1].offered_names == RecordingTools.instances[0].offered_names


def test_list_materials_after_resume_returns_all_attachments(client, monkeypatch):
    first = _upload_pdf(client, ["First document text."], "first.pdf")
    second = _upload_pdf(client, ["Second document text."], "second.pdf")
    source_id = _create_outline(client)
    _configure(
        monkeypatch,
        [
            AgentTurn(
                tool_calls=[
                    {
                        "id": "outline-1",
                        "name": "read_outline_image",
                        "arguments": {"source_id": source_id},
                    }
                ]
            ),
            AgentTurn(
                tool_calls=[{"id": "materials-1", "name": "list_materials", "arguments": {}}]
            ),
            AgentTurn("All materials were listed."),
        ],
    )
    RecordingTools = _install_recording_tools(monkeypatch)

    paused = _chat(
        client,
        [
            {"id": first, "kind": "pdf", "name": "first.pdf"},
            {"id": second, "kind": "pdf", "name": "second.pdf"},
            {"id": source_id, "kind": "outline", "name": "sheet.png"},
        ],
        {"id": source_id, "kind": "outline"},
    )
    resumed = _resume(client, _pending_id(paused))

    assert resumed["status"] is None
    materials = next(
        value
        for name, value in RecordingTools.instances[-1].executed
        if name == "list_materials"
    )["materials"]
    assert len(materials) == 3
    assert sum(1 for item in materials if item["active"]) == 1
    assert {item.get("document_id") or item.get("source_id") for item in materials} == {
        first,
        second,
        source_id,
    }
