from __future__ import annotations

import time
from io import BytesIO

from docx import Document

from app.config import get_settings
from app.services.agent_store import agent_store
from app.services.page_ocr import OcrProgress
from app.services.storage import storage
from tests.helpers import build_pdf


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
