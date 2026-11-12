from __future__ import annotations

import time

from app.services.job_store import jobs
from app.services.storage import storage
from tests.helpers import build_pdf

PDF_CT = "application/pdf"
PAGE_ONE = "Iron deficiency anemia is a microcytic hypochromic anemia."


def _upload(client, filename: str, data: bytes, content_type: str = PDF_CT):
    return client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": (filename, data, content_type)},
    )


def _post_job(client, document_id: str, mode: str = "extracted_text_only"):
    return client.post(
        "/api/v1/study-jobs", json={"document_id": document_id, "mode": mode}
    )


def _new_document(client, text: str = PAGE_ONE) -> str:
    response = _upload(client, "lecture.pdf", build_pdf([text]))
    assert response.status_code == 201
    return response.json()["document_id"]


def test_documents_and_jobs_survive_store_reload(client):
    document_id = _new_document(client)
    job = _post_job(client, document_id).json()
    job_id = job["job_id"]

    assert storage.reload() >= 1
    assert jobs.reload() >= 1

    reloaded_doc = storage.get(document_id)
    assert reloaded_doc is not None
    assert reloaded_doc.available is True
    assert reloaded_doc.sections

    fetched = client.get(f"/api/v1/study-jobs/{job_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["status"] == "completed"
    assert body["items"][0]["text"].strip() == PAGE_ONE
    assert body["document_available"] is True


def test_expired_documents_and_jobs_do_not_return_after_reload(client):
    document_id = _new_document(client)
    job_id = _post_job(client, document_id).json()["job_id"]

    storage.get(document_id).expires_at = 0
    jobs._expires_at[job_id] = 0

    assert storage.sweep() >= 1
    assert jobs.sweep() >= 1

    assert storage.get(document_id) is None
    assert jobs.get(job_id) is None

    assert storage.reload() == 0
    assert jobs.reload() == 0
    assert client.get(f"/api/v1/study-jobs/{job_id}").status_code == 404


def test_orphan_files_are_removed_and_known_files_kept(client):
    document_id = _new_document(client)
    record = storage.get(document_id)
    assert record is not None

    orphan = storage.root / "orphan.pdf"
    orphan.write_bytes(b"%PDF-1.7 orphan")

    removed = storage.reconcile()

    assert removed == 1
    assert not orphan.exists()
    assert record.path.exists()


def test_missing_source_file_blocks_new_jobs_but_keeps_results(client):
    document_id = _new_document(client)
    job_id = _post_job(client, document_id).json()["job_id"]

    record = storage.get(document_id)
    assert record is not None
    record.path.unlink()

    assert storage.reload() >= 1

    fetched = client.get(f"/api/v1/study-jobs/{job_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["document_available"] is False
    assert body["items"][0]["text"].strip() == PAGE_ONE

    blocked = _post_job(client, document_id)
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "document_unavailable"

    exported = client.get(f"/api/v1/study-jobs/{job_id}/export?format=pdf")
    assert exported.status_code == 200


def test_delete_document_preserves_completed_results(client):
    document_id = _new_document(client)
    job_id = _post_job(client, document_id).json()["job_id"]

    assert client.delete(f"/api/v1/documents/{document_id}").status_code == 204

    fetched = client.get(f"/api/v1/study-jobs/{job_id}")
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["document_available"] is False
    assert body["items"]

    assert client.get(f"/api/v1/study-jobs/{job_id}/export?format=docx").status_code == 200
    assert _post_job(client, document_id).status_code == 404


def test_recent_jobs_are_listed_newest_first_with_limits(client):
    first = _new_document(client, "First document content.")
    first_job = _post_job(client, first).json()["job_id"]
    time.sleep(0.02)
    second = _new_document(client, "Second document content.")
    second_job = _post_job(client, second).json()["job_id"]
    time.sleep(0.02)
    third = _new_document(client, "Third document content.")
    third_job = _post_job(client, third).json()["job_id"]

    listed = client.get("/api/v1/study-jobs").json()
    assert [entry["job_id"] for entry in listed] == [third_job, second_job, first_job]
    assert all(entry["document_available"] is True for entry in listed)
    assert listed[0]["item_count"] == 1
    assert listed[0]["mode"] == "extracted_text_only"

    limited = client.get("/api/v1/study-jobs?limit=2").json()
    assert [entry["job_id"] for entry in limited] == [third_job, second_job]

    offset = client.get("/api/v1/study-jobs?offset=1&limit=1").json()
    assert [entry["job_id"] for entry in offset] == [second_job]


def test_empty_history_returns_empty_list(client):
    assert client.get("/api/v1/study-jobs").json() == []


def test_history_records_survive_reload(client):
    document_id = _new_document(client)
    job_id = _post_job(client, document_id).json()["job_id"]

    jobs.reload()

    listed = client.get("/api/v1/study-jobs").json()
    assert [entry["job_id"] for entry in listed] == [job_id]
