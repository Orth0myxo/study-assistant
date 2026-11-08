from __future__ import annotations

import sqlite3

from app.config import get_settings
from app.services.db import (
    Database,
    _SCHEMA_V1,
    _V2_USERS,
    _V3_TOPIC_SOURCES,
)
from app.services.job_store import jobs
from app.services.storage import storage
from tests.helpers import build_pdf

PDF_CT = "application/pdf"
PAGE = "Iron deficiency anemia is a microcytic hypochromic anemia."
PAGE_TWO = "Megaloblastic anemia is macrocytic with hypersegmented neutrophils."


def _upload(client, headers, filename: str = "lecture.pdf") -> str:
    response = client.post(
        "/api/v1/documents",
        files={"file": (filename, build_pdf([PAGE, PAGE_TWO]), PDF_CT)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _job(client, headers, document_id: str) -> dict:
    response = client.post(
        "/api/v1/study-jobs",
        json={"document_id": document_id, "mode": "extracted_text_only"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "completed"
    return body


def _legacy_job(job_id: str = "legacy-job") -> None:
    jobs.put(
        job_id,
        {
            "job_id": job_id,
            "owner_id": None,
            "document_id": "legacy-doc",
            "filename": "legacy.pdf",
            "format": "pdf",
            "mode": "extracted_text_only",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "page_count": 1,
            "item_count": 0,
            "items": [],
            "document_summary": None,
            "warnings": [],
            "error": None,
            "progress": {"processed_units": 1, "total_units": 1},
        },
    )


def _legacy_document() -> str:
    document_id = storage.new_document_id()
    storage.save(
        document_id,
        "legacy.pdf",
        build_pdf([PAGE]),
        owner_id=None,
        format="pdf",
        page_count=1,
        extraction_status="ok",
        text_extractable=True,
        total_chars=len(PAGE),
        sections=[
            {
                "index": 0,
                "anchor": "page-1",
                "raw_text": PAGE,
                "normalized_text": PAGE,
            }
        ],
        warnings=[],
    )
    return document_id


def test_history_is_scoped_to_owner(client, identity, make_user):
    user_b = make_user()
    doc_a = _upload(client, identity["headers"], "a.pdf")
    doc_b = _upload(client, user_b["headers"], "b.pdf")
    job_a = _job(client, identity["headers"], doc_a)
    job_b = _job(client, user_b["headers"], doc_b)

    listing_a = client.get(
        "/api/v1/study-jobs", headers=identity["headers"]
    ).json()
    listing_b = client.get("/api/v1/study-jobs", headers=user_b["headers"]).json()

    assert [entry["job_id"] for entry in listing_a] == [job_a["job_id"]]
    assert [entry["job_id"] for entry in listing_b] == [job_b["job_id"]]
    assert listing_a[0]["filename"] == "a.pdf"
    assert listing_b[0]["filename"] == "b.pdf"


def test_new_user_has_empty_history(client, make_user):
    fresh = make_user()

    response = client.get("/api/v1/study-jobs", headers=fresh["headers"])

    assert response.status_code == 200
    assert response.json() == []


def test_user_cannot_read_other_users_job(client, identity, make_user):
    user_b = make_user()
    job_a = _job(client, identity["headers"], _upload(client, identity["headers"]))

    response = client.get(
        f"/api/v1/study-jobs/{job_a['job_id']}", headers=user_b["headers"]
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"
    assert "items" not in response.json()


def test_user_cannot_export_other_users_job(client, identity, make_user):
    user_b = make_user()
    job_a = _job(client, identity["headers"], _upload(client, identity["headers"]))

    response = client.get(
        f"/api/v1/study-jobs/{job_a['job_id']}/export?format=pdf",
        headers=user_b["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_user_cannot_delete_other_users_document(client, identity, make_user):
    user_b = make_user()
    doc_a = _upload(client, identity["headers"])

    response = client.delete(
        f"/api/v1/documents/{doc_a}", headers=user_b["headers"]
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"
    assert storage.get_for_owner(doc_a, identity["user_id"]) is not None


def test_user_cannot_start_job_on_other_users_document(client, identity, make_user):
    user_b = make_user()
    doc_a = _upload(client, identity["headers"])

    response = client.post(
        "/api/v1/study-jobs",
        json={"document_id": doc_a, "mode": "extracted_text_only"},
        headers=user_b["headers"],
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "document_not_found"


def test_user_can_delete_own_document_without_affecting_others(
    client, identity, make_user
):
    user_b = make_user()
    doc_a = _upload(client, identity["headers"], "a.pdf")
    doc_b = _upload(client, user_b["headers"], "b.pdf")

    assert (
        client.delete(f"/api/v1/documents/{doc_b}", headers=user_b["headers"])
        .status_code
        == 204
    )
    assert storage.get_for_owner(doc_b, user_b["user_id"]) is None
    assert storage.get_for_owner(doc_a, identity["user_id"]) is not None
    # Deleting a document never deletes another user's jobs.
    assert (
        client.get("/api/v1/study-jobs", headers=identity["headers"]).status_code
        == 200
    )


def test_missing_or_invalid_user_token_is_rejected(client):
    missing = client.get("/api/v1/study-jobs", headers={"Authorization": ""})
    invalid = client.get(
        "/api/v1/study-jobs", headers={"Authorization": "Bearer not-a-token"}
    )
    missing_delete = client.delete(
        "/api/v1/documents/anything", headers={"Authorization": ""}
    )

    for response in (missing, invalid, missing_delete):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_user_token"
        # The client relies on this challenge to tell a stale user token apart
        # from a bad application token before re-registering.
        assert response.headers["www-authenticate"] == 'Bearer realm="user"'


def test_shared_app_token_is_not_user_identity(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_access_token", "shared-token")

    only_app_token = client.get(
        "/api/v1/study-jobs",
        headers={"Authorization": "", "X-App-Token": "shared-token"},
    )
    both_tokens = client.get(
        "/api/v1/study-jobs", headers={"X-App-Token": "shared-token"}
    )

    assert only_app_token.status_code == 401
    assert only_app_token.json()["error"]["code"] == "invalid_user_token"
    assert both_tokens.status_code == 200


def test_legacy_ownerless_records_are_invisible(client, identity):
    legacy_document = _legacy_document()
    _legacy_job()

    listing = client.get("/api/v1/study-jobs", headers=identity["headers"]).json()
    read_job = client.get(
        "/api/v1/study-jobs/legacy-job", headers=identity["headers"]
    )
    export_job = client.get(
        "/api/v1/study-jobs/legacy-job/export?format=pdf",
        headers=identity["headers"],
    )
    delete_document = client.delete(
        f"/api/v1/documents/{legacy_document}", headers=identity["headers"]
    )

    assert listing == []
    assert read_job.status_code == 404
    assert export_job.status_code == 404
    assert delete_document.status_code == 404
    # The rows are quarantined, not deleted, and remain unavailable to anyone.
    assert jobs.get("legacy-job") is not None
    assert storage.get(legacy_document) is not None
    assert storage.get_for_owner(legacy_document, identity["user_id"]) is None


def test_ownership_survives_reload(client, identity, make_user):
    user_b = make_user()
    job_a = _job(client, identity["headers"], _upload(client, identity["headers"]))

    storage.reload()
    jobs.reload()

    assert [
        entry["job_id"]
        for entry in client.get(
            "/api/v1/study-jobs", headers=identity["headers"]
        ).json()
    ] == [job_a["job_id"]]
    assert (
        client.get(
            f"/api/v1/study-jobs/{job_a['job_id']}", headers=user_b["headers"]
        ).status_code
        == 404
    )


def test_registration_returns_distinct_identities(make_user):
    first = make_user()
    second = make_user()

    assert first["user_id"] != second["user_id"]
    assert first["token"] != second["token"]
    assert first["headers"] != second["headers"]


def test_v1_database_migrates_without_losing_rows(tmp_path):
    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA_V1)
    connection.execute(
        """
        INSERT INTO documents (
            document_id, filename, format, size_bytes, path, created_at,
            expires_at, page_count, extraction_status, text_extractable,
            section_count, total_chars, sections_json, warnings_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "legacy-doc",
            "legacy.pdf",
            "pdf",
            10,
            str(tmp_path / "legacy.pdf"),
            1.0,
            2.0,
            1,
            "ok",
            1,
            1,
            len(PAGE),
            "[]",
            "[]",
        ),
    )
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    legacy = Database()
    legacy.configure(path)
    try:
        document_columns = {
            row[1] for row in legacy.query("PRAGMA table_info(documents)")
        }
        job_columns = {
            row[1] for row in legacy.query("PRAGMA table_info(study_jobs)")
        }
        rows = legacy.query("SELECT document_id, owner_id FROM documents")

        assert "owner_id" in document_columns
        assert "owner_id" in job_columns
        assert legacy.query("PRAGMA user_version")[0][0] == 6
        assert len(rows) == 1
        assert rows[0]["owner_id"] is None
        assert legacy.query(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'users'"
        )
        assert legacy.query(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'topic_sources'"
        )
        assert legacy.query(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'topic_source_images'"
        )
    finally:
        legacy.close()


def test_v2_database_migrates_to_topic_sources_without_losing_ownership(tmp_path):
    path = tmp_path / "v2.db"
    connection = sqlite3.connect(path)
    connection.executescript(_SCHEMA_V1)
    connection.execute("ALTER TABLE documents ADD COLUMN owner_id TEXT")
    connection.execute("ALTER TABLE study_jobs ADD COLUMN owner_id TEXT")
    connection.executescript(_V2_USERS)
    connection.execute(
        """
        INSERT INTO documents (
            document_id, owner_id, filename, format, size_bytes, path,
            created_at, expires_at, page_count, extraction_status,
            text_extractable, section_count, total_chars, sections_json,
            warnings_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "owned-doc",
            "user-a",
            "owned.pdf",
            "pdf",
            10,
            str(tmp_path / "owned.pdf"),
            1.0,
            2.0,
            1,
            "ok",
            1,
            1,
            len(PAGE),
            "[]",
            "[]",
        ),
    )
    connection.execute(
        "INSERT INTO users (user_id, token_hash, created_at) VALUES (?, ?, ?)",
        ("user-a", "hash", 1.0),
    )
    connection.execute("PRAGMA user_version=2")
    connection.commit()
    connection.close()

    migrated = Database()
    migrated.configure(path)
    try:
        rows = migrated.query("SELECT document_id, owner_id FROM documents")
        assert rows[0]["document_id"] == "owned-doc"
        assert rows[0]["owner_id"] == "user-a"
        assert migrated.query("PRAGMA user_version")[0][0] == 6
        assert migrated.query(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'topic_sources'"
        )
        assert migrated.query(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'topic_source_images'"
        )
    finally:
        migrated.close()


def test_v3_database_migrates_transcript_provenance_and_versions(tmp_path):
    path = tmp_path / "v3.db"
    connection = sqlite3.connect(path)
    connection.executescript(_V3_TOPIC_SOURCES)
    connection.execute(
        """
        INSERT INTO topic_sources (
            source_id, owner_id, document_id, document_filename,
            document_format, document_page_count, status, image_count,
            total_chars, topics_json, warnings_json, created_at,
            updated_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source-a",
            "user-a",
            None,
            None,
            None,
            None,
            "ok",
            1,
            5,
            "[]",
            "[]",
            1.0,
            2.0,
            3.0,
        ),
    )
    connection.execute(
        """
        INSERT INTO topic_source_images (
            source_id, image_index, filename, content_type, size_bytes,
            width, height, path, ocr_status, ocr_text, warnings_json,
            created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "source-a",
            0,
            "note.png",
            "image/png",
            10,
            10,
            10,
            str(tmp_path / "note.png"),
            "ok",
            "hello",
            "[]",
            1.0,
        ),
    )
    connection.execute("PRAGMA user_version=3")
    connection.commit()
    connection.close()

    migrated = Database()
    migrated.configure(path)
    try:
        source_columns = {
            row[1] for row in migrated.query("PRAGMA table_info(topic_sources)")
        }
        image_columns = {
            row[1]
            for row in migrated.query("PRAGMA table_info(topic_source_images)")
        }
        source = migrated.query(
            "SELECT source_id, version, jottings_text FROM topic_sources"
        )[0]
        image = migrated.query(
            "SELECT source_id, transcript_source FROM topic_source_images"
        )[0]

        assert migrated.query("PRAGMA user_version")[0][0] == 6
        assert "version" in source_columns
        assert "jottings_text" in source_columns
        assert "transcript_source" in image_columns
        assert source["source_id"] == "source-a"
        assert source["version"] == 0
        assert source["jottings_text"] == ""
        assert image["source_id"] == "source-a"
        assert image["transcript_source"] == "ocr"
    finally:
        migrated.close()
