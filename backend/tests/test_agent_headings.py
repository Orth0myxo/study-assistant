from __future__ import annotations

import io
import logging
import time

import pymupdf
from docx import Document as DocxDocument

from app.config import get_settings
from app.services.agent_service import AGENT_TOOLS, AgentTools, _agent_system_prompt
from app.services.agent_store import AgentConversation


def _upload(client, payload: bytes, filename: str, content_type: str) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (filename, payload, content_type)},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _pdf(*, headings: bool = False, bookmarks: bool = False) -> bytes:
    document = pymupdf.open()
    count = 16 if headings else 1
    for index in range(count):
        page = document.new_page()
        if headings:
            page.insert_text(
                (72, 48), f"Lecture heading {index + 1}",
                fontname="hebo", fontsize=18,
            )
            page.insert_text((72, 78), "Body text for this page.", fontsize=11)
        else:
            page.insert_text((72, 72), "Only ordinary body text.", fontsize=12)
    if bookmarks:
        document.set_toc([[1, "Bookmarked chapter", 1]])
    payload = document.tobytes()
    document.close()
    return payload


def _docx() -> bytes:
    document = DocxDocument()
    document.add_heading("Word section heading", level=1)
    document.add_paragraph("Body text under the heading.")
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _tool(identity, document_id: str, kind: str = "pdf") -> AgentTools:
    conversation = AgentConversation(
        "heading-conversation",
        identity["user_id"],
        last_source_id=document_id,
        last_source_kind=kind,
        last_document_id=document_id,
    )
    return AgentTools(
        identity["user_id"],
        get_settings(),
        conversation,
        time.monotonic() + 10,
        [0],
    )


def test_list_headings_reads_sixteen_page_bold_pdf_headings(client, identity, caplog):
    document_id = _upload(client, _pdf(headings=True), "lecture.pdf", "application/pdf")
    tools = _tool(identity, document_id)

    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        result = tools.execute("list_headings", {})

    assert result["source"] == "font"
    assert result["computed"] == "stored_structure"
    assert [(item["text"], item["page"]) for item in result["headings"]] == [
        (f"Lecture heading {page}", page) for page in range(1, 17)
    ]
    assert "agent headings" in caplog.text
    assert "source=font count=16 pages=16" in caplog.text
    assert "Lecture heading" not in caplog.text


def test_list_headings_prefers_pdf_bookmarks(client, identity):
    document_id = _upload(
        client, _pdf(headings=True, bookmarks=True), "bookmarked.pdf", "application/pdf"
    )

    result = _tool(identity, document_id).execute("list_headings", {})

    assert result["source"] == "bookmarks"
    assert result["computed"] == "on_demand"
    assert result["headings"] == [
        {"text": "Bookmarked chapter", "page": 1, "level": 1}
    ]


def test_list_headings_uses_docx_heading_styles(client, identity):
    document_id = _upload(
        client,
        _docx(),
        "notes.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    result = _tool(identity, document_id, "docx").execute("list_headings", {})

    assert result["source"] == "styles"
    assert result["computed"] == "stored_structure"
    assert result["headings"] == [
        {"text": "Word section heading", "page": None, "block": 1}
    ]
    assert "Word page numbers are unavailable" in result["location_note"]


def test_list_headings_reports_none_without_inventing_headings(client, identity, caplog):
    document_id = _upload(client, _pdf(), "plain.pdf", "application/pdf")

    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        result = _tool(identity, document_id).execute("list_headings", {})

    assert result["headings"] == []
    assert result["page_titles"] == []
    assert result["page_image_pages"] == []
    assert "No headings were detected" in result["note"]
    assert result["source"] == "font"
    assert "outcome=none" in caplog.text
    assert "Only ordinary body text" not in caplog.text


def test_topics_in_document_prompt_uses_headings_tool():
    tool_names = {item["function"]["name"] for item in AGENT_TOOLS}

    assert "list_headings" in tool_names
    prompt = _agent_system_prompt(get_settings())
    assert "use list_headings" in prompt
    assert "never invent headings" in prompt
