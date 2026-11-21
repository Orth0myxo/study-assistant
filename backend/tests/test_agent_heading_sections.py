from __future__ import annotations

import io
import logging
import time

import pymupdf
from docx import Document as DocxDocument

from app.config import get_settings
from app.services.agent_service import (
    AGENT_TOOLS,
    AgentTools,
    _agent_system_prompt,
)
from app.services.agent_store import AgentConversation, agent_store


def _upload(client, payload: bytes, filename: str, content_type: str) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (filename, payload, content_type)},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _nested_docx() -> bytes:
    document = DocxDocument()
    document.add_heading("Cell biology", level=1)
    document.add_paragraph("Cells store genetic material.")
    document.add_heading("Mitosis", level=2)
    document.add_paragraph("Chromosomes line up during metaphase.")
    document.add_paragraph("Spindle fibers pull chromatids apart.")
    document.add_heading("Meiosis", level=2)
    document.add_paragraph("Meiosis produces gametes.")
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()


def _slide_pdf(pages: int = 6) -> bytes:
    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text(
            (72, 48),
            f"Lecture heading {index + 1}",
            fontname="hebo",
            fontsize=18,
        )
        page.insert_text(
            (72, 78),
            f"Body text for slide {index + 1}.",
            fontsize=11,
        )
    payload = document.tobytes()
    document.close()
    return payload


def _plain_pdf() -> bytes:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Only ordinary body text.", fontsize=12)
    payload = document.tobytes()
    document.close()
    return payload


def _tool(identity, document_id: str, kind: str = "pdf") -> AgentTools:
    conversation = AgentConversation(
        "section-conversation",
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


def _docx_text(path) -> str:
    document = DocxDocument(path)
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _pdf_text(path) -> str:
    with pymupdf.open(path) as document:
        return "\n".join(page.get_text() for page in document)


def test_find_sections_groups_hits_by_nearest_heading_path(client, identity, caplog):
    document_id = _upload(
        client,
        _nested_docx(),
        "notes.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    tools = _tool(identity, document_id, "docx")

    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        result = tools.execute("find_heading_sections", {"topics": ["chromosomes"]})

    assert result["source"] == "styles"
    assert len(result["sections"]) == 1
    group = result["sections"][0]
    assert group["text"] == "Mitosis"
    assert group["path"] == ["Cell biology", "Mitosis"]
    assert group["topics"] == ["chromosomes"]
    assert "agent heading_sections" in caplog.text
    assert "source=styles" in caplog.text
    assert "Chromosomes" not in caplog.text


def test_extracted_docx_section_is_whole_section_not_matched_line(client, identity, caplog):
    document_id = _upload(
        client,
        _nested_docx(),
        "notes.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    tools = _tool(identity, document_id, "docx")
    found = tools.execute("find_heading_sections", {"topics": ["chromosomes"]})
    section_id = found["sections"][0]["section_id"]

    with caplog.at_level(logging.INFO, logger="app.services.agent_service"):
        result = tools.execute(
            "extract_heading_sections",
            {"section_ids": [section_id], "format": "docx"},
        )

    assert result["sections"] == 1
    assert result["file_id"]
    file = agent_store.get_file(result["file_id"], identity["user_id"])
    assert file is not None
    text = _docx_text(file.path)
    assert "Mitosis" in text
    assert "Chromosomes line up during metaphase." in text
    assert "Spindle fibers pull chromatids apart." in text
    assert "Meiosis produces gametes." not in text
    assert "agent section_extract" in caplog.text
    assert "outcome=exported" in caplog.text
    assert "Chromosomes" not in caplog.text


def test_parent_heading_section_includes_its_subsections(client, identity):
    document_id = _upload(
        client,
        _nested_docx(),
        "notes.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    tools = _tool(identity, document_id, "docx")
    found = tools.execute("find_heading_sections", {"topics": ["genetic material"]})
    parent = next(item for item in found["sections"] if item["text"] == "Cell biology")

    result = tools.execute(
        "extract_heading_sections",
        {"section_ids": [parent["section_id"]], "format": "docx"},
    )
    file = agent_store.get_file(result["file_id"], identity["user_id"])
    assert file is not None
    text = _docx_text(file.path)
    assert "Mitosis" in text
    assert "Meiosis" in text
    assert "Spindle fibers pull chromatids apart." in text


def test_slide_titles_are_headings_and_pdf_section_stops_before_next_slide(client, identity):
    document_id = _upload(client, _slide_pdf(), "slides.pdf", "application/pdf")
    tools = _tool(identity, document_id)
    found = tools.execute("find_heading_sections", {"topics": ["Lecture heading 3"]})
    group = next(item for item in found["sections"] if item["text"] == "Lecture heading 3")
    assert group["page"] == 3

    result = tools.execute(
        "extract_heading_sections",
        {"section_ids": [group["section_id"]], "format": "pdf"},
    )
    file = agent_store.get_file(result["file_id"], identity["user_id"])
    assert file is not None
    text = _pdf_text(file.path)
    assert "Lecture heading 3" in text
    assert "Body text for slide 3." in text
    assert "Lecture heading 4" not in text


def test_pdf_without_headings_returns_hits_instead_of_inventing_headings(client, identity):
    document_id = _upload(client, _plain_pdf(), "plain.pdf", "application/pdf")
    tools = _tool(identity, document_id)

    found = tools.execute("find_heading_sections", {"topics": ["ordinary"]})

    assert found["sections"] == []
    assert found["source"] == "none"
    assert len(found["hits"]) == 1
    assert found["hits"][0]["page"] == 1
    assert found["hits"][0]["section_id"].endswith(":hit:0")
    assert "No headings were detected" in found["note"]

    result = tools.execute(
        "extract_heading_sections",
        {"section_ids": [found["hits"][0]["section_id"]], "format": "pdf"},
    )
    file = agent_store.get_file(result["file_id"], identity["user_id"])
    assert file is not None
    assert "Only ordinary body text." in _pdf_text(file.path)


def test_section_tools_are_registered_and_prompted():
    tool_names = {item["function"]["name"] for item in AGENT_TOOLS}
    assert "find_heading_sections" in tool_names
    assert "extract_heading_sections" in tool_names
    prompt = _agent_system_prompt(get_settings())
    assert "find_heading_sections" in prompt
    assert "extract_heading_sections" in prompt
    assert "do not ask for another confirmation" in prompt
