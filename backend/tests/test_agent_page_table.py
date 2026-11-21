from __future__ import annotations

import logging
import time

import pytest

from app.config import get_settings
from app.errors import ApiError
from app.services.agent_loop import AgentToolError
from app.services.agent_service import (
    AGENT_TOOLS,
    AgentTools,
    _agent_system_prompt,
)
from app.services.agent_store import AgentConversation
from app.services.storage import storage
from app.services.table_reader import TableRead
from tests.helpers import build_pdf


def _document(client) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("table.pdf", build_pdf(["Scanned table page"]), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _tools(identity, document_id: str) -> AgentTools:
    conversation = AgentConversation(
        "page-table-conversation",
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


def _table() -> TableRead:
    return TableRead(
        title="Elements",
        columns=["Name", "Atomic number"],
        rows=[["Carbon", "6"], ["Oxygen", "8"]],
        confidence=[[95.0, 95.0], [95.0, 95.0]],
        low_confidence=[[False, False], [False, False]],
        engine="local",
    )


def test_read_page_table_stores_rows_and_renders_preview(
    client, identity, monkeypatch, caplog
):
    document_id = _document(client)
    monkeypatch.setattr(
        "app.services.agent_service.read_local_table",
        lambda _image, _suffix, _settings: _table(),
    )
    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        result = _tools(identity, document_id).execute(
            "read_page_table", {"page": 1}
        )

    assert result["pages"] == [1]
    assert result["row_count"] == 3
    assert result["column_count"] == 2
    page = storage.pages_for(document_id)[0]
    assert page["source"] == "ocr"
    assert page["tables_checked"] is True
    assert page["tables"][0]["rows"] == [
        ["Name", "Atomic number"],
        ["Carbon", "6"],
        ["Oxygen", "8"],
    ]
    assert "Carbon\t6" in page["text"]
    assert "table_aware_read" in caplog.text
    assert "outcome=ok" in caplog.text

    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    preview = client.post(
        f"/api/v1/agent/results/{result['result_id']}/preview",
        json={},
        headers=identity["headers"],
    )
    assert preview.status_code == 200, preview.text
    blocks = preview.json()["blocks"]
    table = next(block for block in blocks if block["type"] == "table")
    assert table["rows"] == [
        ["Name", "Atomic number"],
        ["Carbon", "6"],
        ["Oxygen", "8"],
    ]


def test_read_page_table_reports_unreadable_page(client, identity, monkeypatch, caplog):
    document_id = _document(client)

    def unreadable(*_args):
        raise ApiError("table_not_readable", "No table was recognized.", 422)

    monkeypatch.setattr("app.services.agent_service.read_local_table", unreadable)
    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        with pytest.raises(AgentToolError) as error:
            _tools(identity, document_id).execute("read_page_table", {"page": 1})

    assert error.value.code == "table_not_readable"
    assert "tables=0 rows=0" in caplog.text
    assert "outcome=table_not_readable" in caplog.text
    assert storage.pages_for(document_id)[0]["tables"] == []


def test_page_table_tool_is_registered_and_prompted():
    names = {tool["function"]["name"] for tool in AGENT_TOOLS}
    assert "read_page_table" in names
    prompt = _agent_system_prompt(get_settings())
    assert "read_page_table" in prompt
