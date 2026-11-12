from __future__ import annotations

from app.services.db import db
from app.services.storage import storage
from tests.helpers import build_docx, build_pdf, build_png

PNG_CT = "image/png"
PDF_CT = "application/pdf"


def _upload_document(client, pages: list[str]) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("lecture.pdf", build_pdf(pages), PDF_CT)},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _create_source(
    client,
    *,
    document_id: str | None = None,
    jottings_text: str | None = None,
    images: bool = True,
) -> dict:
    data: dict[str, str] = {}
    if document_id:
        data["document_id"] = document_id
    if jottings_text:
        data["jottings_text"] = jottings_text
    files = (
        {"images": ("notes.png", build_png(64), PNG_CT)} if images else None
    )
    response = client.post(
        "/api/v1/topic-sources", files=files, data=data or None
    )
    assert response.status_code == 201, response.text
    return response.json()


def _study(client, source_id: str, topics: list[str], mode="source_passages"):
    response = client.post(
        f"/api/v1/topic-sources/{source_id}/study-output",
        json={"mode": mode, "topics": topics},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_topic_study_output_is_persisted_and_reopenable(client):
    document_id = _upload_document(
        client, ["The Calvin cycle fixes carbon dioxide."]
    )
    source = _create_source(client, document_id=document_id)
    created = _study(client, source["source_id"], ["Calvin cycle"])

    assert created["result_id"]
    assert created["created_at"]
    assert created["expires_at"]
    result_id = created["result_id"]

    listing = client.get("/api/v1/topic-results")
    assert listing.status_code == 200, listing.text
    items = listing.json()
    assert [item["result_id"] for item in items] == [result_id]
    assert items[0]["mode"] == "source_passages"
    assert items[0]["status"] == "completed"
    assert items[0]["topic_count"] == 1
    assert items[0]["document_id"] == document_id
    assert items[0]["document_available"] is True

    reopened = client.get(f"/api/v1/topic-results/{result_id}")
    assert reopened.status_code == 200, reopened.text
    body = reopened.json()
    assert body["result_id"] == result_id
    assert body["source_id"] == source["source_id"]
    assert body["topics"] == created["topics"]
    assert body["mode"] == "source_passages"
    assert body["document_available"] is True

    deleted = client.delete(f"/api/v1/topic-results/{result_id}")
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/api/v1/topic-results/{result_id}").status_code == 404
    assert client.get("/api/v1/topic-results").json() == []


def test_topic_results_survive_source_deletion(client):
    document_id = _upload_document(
        client, ["The Calvin cycle fixes carbon dioxide."]
    )
    source = _create_source(client, document_id=document_id)
    created = _study(client, source["source_id"], ["Calvin cycle"])
    client.delete(
        f"/api/v1/documents/{document_id}", follow_redirects=True
    )
    client.delete(f"/api/v1/topic-sources/{source['source_id']}")

    reopened = client.get(f"/api/v1/topic-results/{created['result_id']}")
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["document_available"] is False


def test_topic_results_are_owner_scoped(client, make_user):
    source = _create_source(
        client, jottings_text="The Calvin cycle fixes carbon dioxide."
    )
    created = _study(client, source["source_id"], ["Calvin cycle"])
    result_id = created["result_id"]

    other = make_user()
    headers = other["headers"]
    assert client.get("/api/v1/topic-results", headers=headers).json() == []
    assert (
        client.get(
            f"/api/v1/topic-results/{result_id}", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/topic-results/{result_id}", headers=headers
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/topic-results/{result_id}/export?format=pdf",
            headers=headers,
        ).status_code
        == 404
    )
    assert client.get("/api/v1/topic-sources", headers=headers).json() == []


def test_topic_result_export_supports_pdf_and_docx(client):
    source = _create_source(
        client, jottings_text="The Calvin cycle fixes carbon dioxide."
    )
    created = _study(client, source["source_id"], ["Calvin cycle"])
    result_id = created["result_id"]

    pdf = client.get(f"/api/v1/topic-results/{result_id}/export?format=pdf")
    assert pdf.status_code == 200, pdf.text
    assert pdf.headers["content-type"] == "application/pdf"
    assert "attachment" in pdf.headers["content-disposition"]
    assert pdf.content.startswith(b"%PDF")

    docx = client.get(f"/api/v1/topic-results/{result_id}/export?format=docx")
    assert docx.status_code == 200, docx.text
    assert docx.headers["content-type"].endswith(
        "wordprocessingml.document"
    )
    assert docx.content[:2] == b"PK"

    bad = client.get(f"/api/v1/topic-results/{result_id}/export?format=html")
    assert bad.status_code == 422


def test_topic_sources_can_be_listed_with_image_urls(client):
    first = _create_source(client, jottings_text="First source")
    second = _create_source(client, jottings_text="Second source")

    listing = client.get("/api/v1/topic-sources")
    assert listing.status_code == 200, listing.text
    ids = {item["source_id"] for item in listing.json()}
    assert {first["source_id"], second["source_id"]} <= ids

    image_url = first["images"][0]["image_url"]
    assert image_url == (
        f"/api/v1/topic-sources/{first['source_id']}/images/0"
    )
    image = client.get(image_url)
    assert image.status_code == 200, image.text
    assert image.headers["content-type"] == PNG_CT
    assert "inline" in image.headers["content-disposition"]

    missing = client.get(
        f"/api/v1/topic-sources/{first['source_id']}/images/9"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "image_not_found"


def test_topic_source_image_is_owner_scoped(client, make_user):
    source = _create_source(client, jottings_text="Private source")
    other = make_user()
    response = client.get(
        f"/api/v1/topic-sources/{source['source_id']}/images/0",
        headers=other["headers"],
    )
    assert response.status_code == 404


def test_review_excerpt_is_verbatim_and_bounded(client):
    heading = "Calvin cycle"
    sentence = (
        "The Calvin cycle fixes carbon dioxide and regenerates RuBP so the "
        "plant can keep producing sugar during photosynthesis. "
    )
    long_paragraph = sentence * 20
    document_id = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={
            "file": (
                "long.docx",
                build_docx(
                    [("heading", heading), ("paragraph", long_paragraph)]
                ),
                "application/vnd.openxmlformats-officedocument."
                "wordprocessingml.document",
            )
        },
    ).json()["document_id"]
    source = _create_source(client, document_id=document_id)

    review = client.post(
        f"/api/v1/topic-sources/{source['source_id']}/topic-review",
        json={"topics": ["Calvin cycle"]},
    )
    assert review.status_code == 200, review.text
    match = review.json()["topics"][0]["matches"][0]
    passage = match["passage"]

    assert 0 < len(match["excerpt"]) <= 600
    assert match["passage"][match["excerpt_start"] : match["excerpt_end"]] == (
        match["excerpt"]
    )
    assert match["excerpt"] in passage
    assert "Calvin" in match["excerpt"]


def test_segmentation_only_reports_combined_with_transcript_text(client):
    source = _create_source(
        client, jottings_text="Calvin cycle", images=True
    )
    review = client.post(
        f"/api/v1/topic-sources/{source['source_id']}/topic-review",
        json={},
    )
    assert review.status_code == 200, review.text
    assert review.json()["segmentation"]["input"] == "jottings"

    client.patch(
        f"/api/v1/topic-sources/{source['source_id']}",
        json={"transcripts": [{"index": 0, "text": "Calvin cycle notes"}]},
    )
    review = client.post(
        f"/api/v1/topic-sources/{source['source_id']}/topic-review",
        json={},
    )
    assert review.status_code == 200, review.text
    assert review.json()["segmentation"]["input"] == "combined"


def test_schema_creates_topic_results_table(client):
    rows = db.query(
        "SELECT name FROM sqlite_master "
        "WHERE type = 'table' AND name = 'topic_study_results'"
    )
    assert rows
    assert db.query("PRAGMA user_version")[0][0] == 8


def test_result_payload_does_not_duplicate_document_text(client):
    document_id = _upload_document(
        client, ["The Calvin cycle fixes carbon dioxide."]
    )
    source = _create_source(client, document_id=document_id)
    created = _study(client, source["source_id"], ["Calvin cycle"])
    assert storage.get(document_id) is not None
    assert created["topics"][0]["source_passages"][0]["passage"]
