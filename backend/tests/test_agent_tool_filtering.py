from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.services.agent_loop import AgentToolError
from app.services.agent_service import AgentTools, _agent_tools_for_chat
from app.services.agent_store import AgentConversation

DOCUMENT_TOOLS = {
    "list_headings",
    "find_heading_sections",
    "find_section",
    "extract_heading_sections",
    "find_passages",
    "get_pages",
    "summarize",
}


def _conversation(**changes) -> AgentConversation:
    conversation = AgentConversation("conversation-1", "owner-1")
    for key, value in changes.items():
        setattr(conversation, key, value)
    return conversation


def _names(materials, conversation=None, **settings) -> set[str]:
    tools = _agent_tools_for_chat(
        materials, conversation or _conversation(), Settings(**settings)
    )
    return {tool["function"]["name"] for tool in tools}


def test_no_material_offers_only_material_listing():
    assert _names([]) == {"list_materials"}


def test_image_source_offers_only_outline_reading():
    names = _names([{"kind": "outline", "source_id": "source-1"}])
    assert names == {"list_materials", "read_outline_image"}


def test_word_document_offers_document_tools_without_page_tools():
    names = _names([{"kind": "docx", "document_id": "doc-1"}])
    assert names == {"list_materials", "build_export"} | DOCUMENT_TOOLS


def test_pdf_offers_page_table_but_not_vision_without_a_provider():
    names = _names([{"kind": "pdf", "document_id": "doc-1"}])
    assert "read_page_table" in names
    assert "read_scanned_pages" not in names


def test_pdf_offers_scanned_reading_when_vision_is_configured():
    names = _names(
        [{"kind": "pdf", "document_id": "doc-1"}],
        groq_api_key="key",
        groq_vision_model="vision-model",
    )
    assert "read_scanned_pages" in names


def test_pdf_plus_outline_offers_both_families():
    names = _names(
        [
            {"kind": "pdf", "document_id": "doc-1"},
            {"kind": "outline", "source_id": "source-1"},
        ]
    )
    assert "read_page_table" in names
    assert "read_outline_image" in names


def test_export_is_offered_only_with_a_result_or_result_tool():
    no_result = _names([], _conversation())
    assert "build_export" not in no_result
    result_only = _names([], _conversation(last_result_id="result-1"))
    assert {"summarize", "build_export"} <= result_only
    document = _names([{"kind": "pdf", "document_id": "doc-1"}])
    assert "build_export" in document


def test_tool_list_is_stable_for_a_turn():
    materials = [{"kind": "pdf", "document_id": "doc-1"}]
    conversation = _conversation()
    first = _agent_tools_for_chat(materials, conversation, Settings())
    second = _agent_tools_for_chat(materials, conversation, Settings())
    assert first == second


def test_tool_not_offered_returns_a_safe_error(caplog):
    tools = AgentTools(
        "owner-1",
        Settings(),
        _conversation(),
        time.monotonic() + 5,
        [0],
        [],
        {"list_materials"},
    )

    with pytest.raises(AgentToolError) as excinfo:
        tools.execute("find_passages", {"topics": ["cell"]})

    assert excinfo.value.code == "tool_not_offered"
    assert "outcome=tool_not_offered" in caplog.text
    assert "cell" not in caplog.text
