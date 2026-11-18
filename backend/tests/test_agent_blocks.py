from __future__ import annotations

import time
from types import SimpleNamespace

from app.config import get_settings
from app.services.agent_loop import LoopResult
from app.services.agent_service import AgentTools, _turn_blocks
from app.services.agent_store import AgentConversation


def _summary(show_source: bool) -> dict:
    return {
        "result": {
            "result_id": "summary-1",
            "document_id": "doc-1",
            "source_result_id": "passages-1",
            "kind": "summary",
            "scope": "result",
            "summary": "A short summary.",
            "show_source": show_source,
        }
    }


def _passages(result_id: str, source_result_id: str = "passages-1") -> dict:
    return {
        "result": {
            "result_id": result_id,
            "document_id": "doc-1",
            "kind": "passages",
            "source_result_id": source_result_id,
        }
    }


def test_summary_only_hides_passages_from_later_search_results():
    blocks = _turn_blocks(
        LoopResult(
            reply="Here is the summary.",
            tool_results=[_summary(False), _passages("passages-2")],
        )
    )

    assert [block["type"] for block in blocks] == ["summary"]


def test_summary_with_source_keeps_the_requested_passages():
    blocks = _turn_blocks(
        LoopResult(
            reply="Here is the summary and its source.",
            tool_results=[_summary(True), _passages("passages-2")],
        )
    )

    assert [block["type"] for block in blocks] == ["summary", "passages"]


def test_a_page_selection_replaces_other_passage_results():
    page = _passages("page-1")
    page["result"]["page_selection"] = True
    blocks = _turn_blocks(
        LoopResult(
            reply="Here is page 2.",
            tool_results=[_passages("passages-2"), page],
        )
    )

    assert blocks == [{"type": "passages", "result_id": "page-1"}]


def test_ocr_warnings_are_grouped_and_deduplicated():
    tools = AgentTools(
        "owner",
        get_settings(),
        AgentConversation("conversation", "owner"),
        time.monotonic() + 1,
        [0],
    )
    tools._note_ocr(
        SimpleNamespace(pending_pages=[3, 7], failed_pages=[], warnings=[])
    )
    tools._note_ocr(
        SimpleNamespace(
            pending_pages=[7],
            failed_pages=[9],
            warnings=[{"message": "One OCR note."}],
        )
    )

    assert tools.result_warnings() == [
        "OCR is still needed for page(s) 3, 7.",
        "OCR could not read page(s) 9.",
        "One OCR note.",
    ]


def test_build_export_defaults_to_the_conversation_result(monkeypatch):
    from app.services import agent_service

    conversation = AgentConversation(
        "conversation",
        "owner",
        last_result_id="result-from-earlier-turn",
    )
    tools = AgentTools(
        "owner",
        get_settings(),
        conversation,
        time.monotonic() + 1,
        [0],
    )
    calls = []

    def fake_export(owner_id, result_id, format_name, include):
        calls.append((owner_id, result_id, format_name, include))
        return {"export_id": "export-1"}

    monkeypatch.setattr(agent_service, "build_agent_export", fake_export)

    assert tools.execute("build_export", {"format": "pdf"}) == {"export_id": "export-1"}
    assert calls == [("owner", "result-from-earlier-turn", "pdf", ["verbatim"])]
