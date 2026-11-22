from __future__ import annotations

import time
from io import BytesIO

from docx import Document

from app.config import get_settings
from app.services.agent_service import AgentTools
from app.services.agent_store import AgentConversation, agent_store
from app.services.page_ocr import OcrProgress
from app.services.storage import storage
from tests.helpers import build_docx, build_pdf


SOURCE_TEXT = (
    "Cytology source passage examines epithelial cells in this sample preparation."
)


def _upload_source(client, text: str) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("notes.pdf", build_pdf([text]), "application/pdf")},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _save_passages_result(owner_id: str, document_id: str):
    document = storage.get_for_owner(document_id, owner_id)
    assert document is not None
    return agent_store.save_result(
        owner_id,
        document_id,
        "passages",
        {
            "topics": [
                {
                    "topic": "Cytology",
                    "passages": [
                        {
                            "section_index": 0,
                            "page": 1,
                            "anchor": {"kind": "pdf_page", "page": 1},
                        }
                    ],
                }
            ]
        },
    )


def _upload_docx_paragraphs(client, texts: list[str]) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "notes.docx",
                build_docx([("paragraph", text) for text in texts]),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _save_sections_result(owner_id: str, document_id: str):
    document = storage.get_for_owner(document_id, owner_id)
    assert document is not None
    sections = [
        section
        for section in document.sections
        if str(section.get("raw_text") or "").strip()
    ]
    assert len(sections) >= 2
    passages = [
        {
            "section_index": section["index"],
            "page": (section.get("anchor") or {}).get("page"),
            "anchor": section.get("anchor") or {},
        }
        for section in sections
    ]
    result = agent_store.save_result(
        owner_id,
        document_id,
        "passages",
        {"topics": [{"topic": "Notes", "passages": passages}]},
    )
    return result, sections


def _wait_for_export(client, job_id: str, headers: dict[str, str]):
    for _ in range(100):
        response = client.get(
            f"/api/v1/agent/exports/{job_id}", headers=headers
        )
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in {"completed", "failed"}:
            return body
        time.sleep(0.05)
    raise AssertionError("export job did not finish")


def test_agent_export_job_builds_docx_without_the_model(
    client, identity, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    document_id = _upload_source(client, SOURCE_TEXT)
    result = _save_passages_result(identity["user_id"], document_id)

    started = client.post(
        f"/api/v1/agent/results/{result.result_id}/exports",
        json={"format": "docx"},
        headers=identity["headers"],
    )
    assert started.status_code == 202, started.text
    job = _wait_for_export(
        client, started.json()["job_id"], identity["headers"]
    )

    assert job["status"] == "completed", job
    assert job["file_id"]
    assert job["name"].endswith(".docx")
    downloaded = client.get(
        f"/api/v1/agent/files/{job['file_id']}", headers=identity["headers"]
    )
    assert downloaded.status_code == 200, downloaded.text
    document = Document(BytesIO(downloaded.content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)
    assert SOURCE_TEXT in text


def test_agent_export_job_is_owner_scoped(
    client, identity, make_user, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    document_id = _upload_source(client, SOURCE_TEXT)
    result = _save_passages_result(identity["user_id"], document_id)
    other = make_user()

    hidden = client.post(
        f"/api/v1/agent/results/{result.result_id}/exports",
        json={"format": "pdf"},
        headers=other["headers"],
    )
    assert hidden.status_code == 404, hidden.text
    assert hidden.json()["error"]["code"] == "agent_result_not_found"

    started = client.post(
        f"/api/v1/agent/results/{result.result_id}/exports",
        json={"format": "pdf"},
        headers=identity["headers"],
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    other_poll = client.get(
        f"/api/v1/agent/exports/{job_id}", headers=other["headers"]
    )
    assert other_poll.status_code == 404
    assert other_poll.json()["error"]["code"] == "agent_export_not_found"
    _wait_for_export(client, job_id, identity["headers"])


def test_agent_export_can_report_or_include_pending_ocr(
    client, identity, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    document_id = _upload_source(client, SOURCE_TEXT)
    result = _save_passages_result(identity["user_id"], document_id)
    pending = lambda *_args, **_kwargs: OcrProgress(
        "pending", 1, 1, 0, [1], [], []
    )
    monkeypatch.setattr("app.services.agent_service.ocr_progress", pending)
    monkeypatch.setattr("app.services.page_ocr.ocr_progress", pending)

    blocked = client.post(
        f"/api/v1/agent/results/{result.result_id}/exports",
        json={"format": "docx"},
        headers=identity["headers"],
    )
    assert blocked.status_code == 202, blocked.text
    blocked_job = _wait_for_export(
        client, blocked.json()["job_id"], identity["headers"]
    )
    assert blocked_job["status"] == "failed"
    assert blocked_job["error"]["code"] == "ocr_incomplete"
    assert blocked_job["error"]["details"]["pending_pages"] == [1]

    ready = client.post(
        f"/api/v1/agent/results/{result.result_id}/exports",
        json={"format": "docx", "allow_incomplete_ocr": True},
        headers=identity["headers"],
    )
    assert ready.status_code == 202, ready.text
    ready_job = _wait_for_export(
        client, ready.json()["job_id"], identity["headers"]
    )
    assert ready_job["status"] == "completed", ready_job
    downloaded = client.get(
        f"/api/v1/agent/files/{ready_job['file_id']}",
        headers=identity["headers"],
    )
    assert downloaded.status_code == 200
    text = "\n".join(
        paragraph.text for paragraph in Document(BytesIO(downloaded.content)).paragraphs
    )
    assert "PARTIAL EXPORT" in text
    assert "page(s) 1" in text


def _preview_text(body: dict) -> str:
    return "\n".join(block.get("text") or "" for block in body["blocks"])


def test_agent_preview_applies_removal_and_reorder_without_exporting(
    client, identity, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    first_text = "First editable passage about epithelial cells."
    second_text = "Second editable passage about staining methods."
    document_id = _upload_docx_paragraphs(client, [first_text, second_text])
    result, sections = _save_sections_result(identity["user_id"], document_id)

    detail = client.get(
        f"/api/v1/agent/results/{result.result_id}",
        headers=identity["headers"],
    )
    assert detail.status_code == 200, detail.text
    passage_ids = [
        passage["passage_id"]
        for passage in detail.json()["topics"][0]["passages"]
    ]
    assert len(passage_ids) == 2

    removed = client.post(
        f"/api/v1/agent/results/{result.result_id}/preview",
        json={"edits": {"removed_passage_ids": [passage_ids[0]]}},
        headers=identity["headers"],
    )
    assert removed.status_code == 200, removed.text
    removed_text = _preview_text(removed.json())
    assert first_text not in removed_text
    assert second_text in removed_text

    reordered = client.post(
        f"/api/v1/agent/results/{result.result_id}/preview",
        json={"edits": {"ordered_passage_ids": list(reversed(passage_ids))}},
        headers=identity["headers"],
    )
    assert reordered.status_code == 200, reordered.text
    reordered_text = _preview_text(reordered.json())
    assert reordered_text.index(second_text) < reordered_text.index(first_text)
    assert sections


def test_agent_preview_rejects_unknown_ids_and_other_owners(
    client, identity, make_user, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    document_id = _upload_docx_paragraphs(
        client, ["Alpha passage for editing.", "Beta passage for editing."]
    )
    result, _sections = _save_sections_result(identity["user_id"], document_id)

    invalid = client.post(
        f"/api/v1/agent/results/{result.result_id}/preview",
        json={"edits": {"removed_passage_ids": ["not-in-this-result"]}},
        headers=identity["headers"],
    )
    assert invalid.status_code == 422, invalid.text
    assert invalid.json()["error"]["code"] == "agent_edit_invalid"

    other = make_user()
    hidden = client.post(
        f"/api/v1/agent/results/{result.result_id}/preview",
        json={},
        headers=other["headers"],
    )
    assert hidden.status_code == 404, hidden.text
    assert hidden.json()["error"]["code"] == "agent_result_not_found"


def test_agent_export_uses_removed_passages_from_the_request(
    client, identity, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    first_text = "Removed passage must not appear in the export."
    second_text = "Kept passage must appear in the export."
    document_id = _upload_docx_paragraphs(client, [first_text, second_text])
    result, _sections = _save_sections_result(identity["user_id"], document_id)
    detail = client.get(
        f"/api/v1/agent/results/{result.result_id}",
        headers=identity["headers"],
    )
    passage_ids = [
        passage["passage_id"]
        for passage in detail.json()["topics"][0]["passages"]
    ]

    started = client.post(
        f"/api/v1/agent/results/{result.result_id}/exports",
        json={
            "format": "docx",
            "edits": {"removed_passage_ids": [passage_ids[0]]},
        },
        headers=identity["headers"],
    )
    assert started.status_code == 202, started.text
    job = _wait_for_export(
        client, started.json()["job_id"], identity["headers"]
    )
    assert job["status"] == "completed", job
    downloaded = client.get(
        f"/api/v1/agent/files/{job['file_id']}", headers=identity["headers"]
    )
    text = "\n".join(
        paragraph.text
        for paragraph in Document(BytesIO(downloaded.content)).paragraphs
    )
    assert first_text not in text
    assert second_text in text


def test_agent_summary_preview_and_export_use_the_edited_summary(
    client, identity, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    document_id = _upload_docx_paragraphs(
        client, ["Source passage for the summary.", "Another source passage."]
    )
    source, _sections = _save_sections_result(identity["user_id"], document_id)
    summary = agent_store.save_result(
        identity["user_id"],
        document_id,
        "summary",
        {
            "source_result_id": source.result_id,
            "summary": "Original grounded summary.",
        },
    )

    preview = client.post(
        f"/api/v1/agent/results/{summary.result_id}/preview",
        json={"edits": {"summary": "Edited summary shown before export."}},
        headers=identity["headers"],
    )
    assert preview.status_code == 200, preview.text
    preview_text = _preview_text(preview.json())
    assert "Edited summary shown before export." in preview_text
    assert "Original grounded summary." not in preview_text

    started = client.post(
        f"/api/v1/agent/results/{summary.result_id}/exports",
        json={
            "format": "docx",
            "edits": {"summary": "Edited summary shown before export."},
        },
        headers=identity["headers"],
    )
    assert started.status_code == 202, started.text
    job = _wait_for_export(
        client, started.json()["job_id"], identity["headers"]
    )
    assert job["status"] == "completed", job
    downloaded = client.get(
        f"/api/v1/agent/files/{job['file_id']}", headers=identity["headers"]
    )
    text = "\n".join(
        paragraph.text
        for paragraph in Document(BytesIO(downloaded.content)).paragraphs
    )
    assert "Edited summary shown before export." in text
    assert "Original grounded summary." not in text


def test_agent_summary_preview_returns_table_rows_and_docx_table(
    client, identity, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    document_id = _upload_docx_paragraphs(
        client, ["Source passage for a table summary.", "Another passage."]
    )
    source, _sections = _save_sections_result(identity["user_id"], document_id)
    summary = agent_store.save_result(
        identity["user_id"],
        document_id,
        "summary",
        {
            "source_result_id": source.result_id,
            "summary": (
                "Overview before the table.\n\n"
                "| Topic | Detail |\n"
                "| --- | --- |\n"
                "| Cells | Epithelial |\n\n"
                "Text after the table."
            ),
        },
    )

    preview = client.post(
        f"/api/v1/agent/results/{summary.result_id}/preview",
        json={},
        headers=identity["headers"],
    )
    assert preview.status_code == 200, preview.text
    tables = [
        block for block in preview.json()["blocks"] if block["type"] == "table"
    ]
    assert tables
    assert tables[0]["rows"] == [
        ["Topic", "Detail"],
        ["Cells", "Epithelial"],
    ]

    started = client.post(
        f"/api/v1/agent/results/{summary.result_id}/exports",
        json={"format": "docx"},
        headers=identity["headers"],
    )
    assert started.status_code == 202, started.text
    job = _wait_for_export(
        client, started.json()["job_id"], identity["headers"]
    )
    assert job["status"] == "completed", job
    downloaded = client.get(
        f"/api/v1/agent/files/{job['file_id']}", headers=identity["headers"]
    )
    document = Document(BytesIO(downloaded.content))
    assert document.tables[0].cell(0, 0).text == "Topic"
    assert document.tables[0].cell(1, 1).text == "Epithelial"
    paragraph_text = "\n".join(
        paragraph.text for paragraph in document.paragraphs
    )
    assert "Text after the table." in paragraph_text


def test_build_export_defaults_to_each_summarys_displayed_text(
    client, identity, monkeypatch
):
    monkeypatch.setattr(get_settings(), "agent_enabled", True)
    document_id = _upload_docx_paragraphs(
        client, [SOURCE_TEXT, "Second source passage for the second summary."]
    )
    source, _sections = _save_sections_result(identity["user_id"], document_id)
    first = agent_store.save_result(
        identity["user_id"],
        document_id,
        "summary",
        {
            "source_result_id": source.result_id,
            "summary": "First displayed summary text.",
        },
    )
    second = agent_store.save_result(
        identity["user_id"],
        document_id,
        "summary",
        {
            "source_result_id": source.result_id,
            "summary": "Second displayed summary text.",
        },
    )
    conversation = AgentConversation(
        "conversation-export",
        identity["user_id"],
        last_result_id=second.result_id,
        last_document_id=document_id,
    )
    tools = AgentTools(
        identity["user_id"],
        get_settings(),
        conversation,
        time.monotonic() + 10,
        [0],
    )

    for summary, expected in (
        (first, "First displayed summary text."),
        (second, "Second displayed summary text."),
    ):
        value = tools.execute(
            "build_export", {"format": "docx", "result_id": summary.result_id}
        )
        downloaded = client.get(
            f"/api/v1/agent/files/{value['file_id']}", headers=identity["headers"]
        )
        text = "\n".join(
            paragraph.text
            for paragraph in Document(BytesIO(downloaded.content)).paragraphs
        )
        assert expected in text
        assert SOURCE_TEXT not in text
