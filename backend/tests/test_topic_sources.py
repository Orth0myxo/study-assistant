from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pymupdf
import pytest

from app.config import get_settings
from app.errors import ApiError
from app.services import topic_images
from app.services.db import db
from app.services.ocr import OcrFailed, OcrTimeout, OcrUnavailable
from app.services.storage import storage
from app.services.topic_sources import (
    TopicImageInput,
    topic_sources,
)
from tests.helpers import build_pdf, build_png

PNG_CT = "image/png"
JPEG_CT = "image/jpeg"
PDF_CT = "application/pdf"


def _jpeg(size: int = 64) -> bytes:
    document = pymupdf.open()
    page = document.new_page(width=size, height=size)
    page.draw_rect(pymupdf.Rect(2, 2, size / 2, size / 2), color=(0, 0, 0), fill=(0, 0, 0))
    data = page.get_pixmap(dpi=72).tobytes("jpeg")
    document.close()
    return data


def _create(
    client,
    images: list[tuple[str, bytes, str]],
    *,
    document_id: str | None = None,
    headers: dict[str, str] | None = None,
):
    files = [("images", (name, data, content_type)) for name, data, content_type in images]
    data = {"document_id": document_id} if document_id else None
    return client.post(
        "/api/v1/topic-sources", files=files, data=data, headers=headers
    )


def _upload_document(client, headers: dict[str, str] | None = None) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("lecture.pdf", build_pdf(["Topic source lecture text."]), PDF_CT)},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def _enable_ocr(monkeypatch, limit: int = 10) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_page_limit", limit)
    monkeypatch.setattr(settings, "ocr_timeout_s", 5)


def _codes(warnings: list[dict[str, str]]) -> list[str]:
    return [warning["code"] for warning in warnings]


def _image_input() -> TopicImageInput:
    data = build_png(64)
    return TopicImageInput(
        filename="note.png",
        content_type=PNG_CT,
        data=data,
        width=64,
        height=64,
        ocr_status="empty",
        text="",
        warnings=[],
    )


def test_png_and_jpeg_are_accepted_from_actual_bytes(client, monkeypatch):
    _enable_ocr(monkeypatch)

    response = _create(
        client,
        [
            ("C:\\notes\\page one.png", build_png(64), "application/octet-stream"),
            ("scan.txt", _jpeg(64), "text/plain"),
        ],
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["image_count"] == 2
    assert [image["content_type"] for image in body["images"]] == [PNG_CT, JPEG_CT]
    assert [image["filename"] for image in body["images"]] == ["page one.png", "scan.txt"]
    assert all(image["width"] == 64 for image in body["images"])


def test_unsupported_image_bytes_are_rejected(client):
    response = _create(
        client,
        [("photo.png", b"GIF89a not really a png", PNG_CT)],
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_image_type"


def test_empty_and_corrupt_images_are_rejected(client):
    empty = _create(client, [("empty.png", b"", PNG_CT)])
    corrupt = _create(client, [("corrupt.png", b"\x89PNG\r\n\x1a\nshort", PNG_CT)])

    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "empty_image"
    assert corrupt.status_code == 400
    assert corrupt.json()["error"]["code"] == "corrupt_image"


def test_decompression_bomb_is_rejected_before_decode(client):
    width = 100_000
    height = 100_000
    header = (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )

    response = _create(client, [("bomb.png", header, PNG_CT)])

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "image_too_large"
    assert response.json()["error"]["details"]["max_pixels"] == 40_000_000


def test_per_image_and_total_byte_limits(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "topic_max_image_mb", 0)
    per_image = _create(client, [("large.png", build_png(64), PNG_CT)])
    assert per_image.status_code == 413
    assert per_image.json()["error"]["code"] == "image_too_large"

    monkeypatch.setattr(settings, "topic_max_image_mb", 10)
    monkeypatch.setattr(settings, "max_upload_mb", 0)
    total = _create(client, [("one.png", build_png(64), PNG_CT)])
    assert total.status_code == 413
    assert total.json()["error"]["code"] == "images_too_large"


def test_image_count_limit_and_missing_images(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "topic_max_images", 1)
    too_many = _create(
        client,
        [("one.png", build_png(64), PNG_CT), ("two.png", build_png(64), PNG_CT)],
    )
    missing = client.post("/api/v1/topic-sources")

    assert too_many.status_code == 413
    assert too_many.json()["error"]["code"] == "too_many_images"
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "jottings_required"


def test_typed_jottings_length_limit(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "topic_max_jottings_chars", 5)

    response = client.post(
        "/api/v1/topic-sources",
        data={"jottings_text": "too long"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "jottings_too_long"
    assert body["error"]["details"]["max_chars"] == 5


def test_ocr_success_returns_editable_text_and_review_warnings(
    client, identity, monkeypatch
):
    _enable_ocr(monkeypatch)
    ocr_text = "Chapter 1: Photosynthesis\nLight reactions and the Calvin cycle."

    def fake(image: bytes, suffix: str, settings) -> str:
        assert suffix == ".png"
        return ocr_text

    monkeypatch.setattr("app.services.topic_images.run_tesseract", fake)

    response = _create(client, [("notes.png", build_png(64), PNG_CT)])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["total_chars"] == len(ocr_text)
    assert body["images"][0]["ocr_status"] == "ok"
    assert body["images"][0]["text"] == ocr_text
    assert _codes(body["images"][0]["warnings"]) == ["ocr_review_recommended"]
    assert _codes(body["warnings"]) == ["ocr_handwriting_unreliable"]

    record = topic_sources.get_for_owner(body["source_id"], identity["user_id"])
    assert record is not None
    assert record.images[0].path.exists()


def test_ocr_empty_text_is_reported(client, monkeypatch):
    _enable_ocr(monkeypatch)
    monkeypatch.setattr(
        "app.services.topic_images.run_tesseract",
        lambda image, suffix, settings: "   \n",
    )

    response = _create(client, [("blank.png", build_png(64), PNG_CT)])

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "no_text"
    assert body["images"][0]["ocr_status"] == "empty"
    assert _codes(body["images"][0]["warnings"]) == ["ocr_empty"]
    assert "ocr_incomplete" in _codes(body["warnings"])


def test_ocr_timeout_and_failure_are_reported(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def timeout(image, suffix, settings):
        raise OcrTimeout("slow")

    monkeypatch.setattr("app.services.topic_images.run_tesseract", timeout)
    timed_out = _create(client, [("slow.png", build_png(64), PNG_CT)])
    assert timed_out.status_code == 201
    assert timed_out.json()["images"][0]["ocr_status"] == "timeout"
    assert _codes(timed_out.json()["images"][0]["warnings"]) == ["ocr_timeout"]

    def failed(image, suffix, settings):
        raise OcrFailed("bad")

    monkeypatch.setattr("app.services.topic_images.run_tesseract", failed)
    failed_response = _create(client, [("bad.png", build_png(64), PNG_CT)])
    assert failed_response.status_code == 201
    assert failed_response.json()["images"][0]["ocr_status"] == "failed"
    assert _codes(failed_response.json()["images"][0]["warnings"]) == ["ocr_failed"]


def test_ocr_unavailable_is_reported(client, monkeypatch):
    _enable_ocr(monkeypatch)

    def unavailable(image, suffix, settings):
        raise OcrUnavailable("missing")

    monkeypatch.setattr("app.services.topic_images.run_tesseract", unavailable)

    response = _create(client, [("notes.png", build_png(64), PNG_CT)])

    assert response.status_code == 201
    body = response.json()
    assert body["images"][0]["ocr_status"] == "unavailable"
    assert _codes(body["images"][0]["warnings"]) == ["ocr_unavailable"]
    assert body["status"] == "no_text"


def test_ocr_disabled_does_not_run_and_reports_warning(client, monkeypatch):
    calls: list[bytes] = []

    def fake(image: bytes, suffix: str, settings) -> str:
        calls.append(image)
        return "should not be used"

    monkeypatch.setattr("app.services.topic_images.run_tesseract", fake)

    response = _create(client, [("notes.png", build_png(64), PNG_CT)])

    assert response.status_code == 201
    body = response.json()
    assert calls == []
    assert body["images"][0]["ocr_status"] == "unavailable"
    assert _codes(body["images"][0]["warnings"]) == ["ocr_disabled"]
    assert body["warnings"][0]["code"] == "ocr_handwriting_unreliable"
    assert "ocr_incomplete" in _codes(body["warnings"])


def test_ocr_limit_skips_extra_images(client, monkeypatch):
    _enable_ocr(monkeypatch, limit=1)
    calls: list[str] = []

    def fake(image: bytes, suffix: str, settings) -> str:
        calls.append(suffix)
        return "Recognized lecture text."

    monkeypatch.setattr("app.services.topic_images.run_tesseract", fake)

    response = _create(
        client,
        [
            ("one.png", build_png(64), PNG_CT),
            ("two.png", build_png(80), PNG_CT),
        ],
    )

    assert response.status_code == 201
    body = response.json()
    assert len(calls) == 1
    assert body["status"] == "partial"
    assert body["images"][1]["ocr_status"] == "skipped"
    assert _codes(body["images"][1]["warnings"]) == ["ocr_limit_reached"]


def test_ocr_text_is_truncated_to_configured_limit(client, monkeypatch):
    _enable_ocr(monkeypatch)
    monkeypatch.setattr(get_settings(), "topic_max_transcript_chars", 5)
    monkeypatch.setattr(
        "app.services.topic_images.run_tesseract",
        lambda image, suffix, settings: "abcdefghij",
    )

    response = _create(client, [("notes.png", build_png(64), PNG_CT)])

    assert response.status_code == 201
    body = response.json()
    assert body["images"][0]["text"] == "abcde"
    assert "ocr_text_truncated" in _codes(body["images"][0]["warnings"])
    assert "ocr_incomplete" not in _codes(body["warnings"])


def test_document_can_be_attached_and_must_be_owned(client, make_user):
    document_id = _upload_document(client)
    attached = _create(
        client,
        [("notes.png", build_png(64), PNG_CT)],
        document_id=document_id,
    )

    assert attached.status_code == 201
    assert attached.json()["document_available"] is True
    document = attached.json()["document"]
    assert document["document_id"] == document_id
    assert document["filename"] == "lecture.pdf"
    assert document["format"] == "pdf"
    assert document["page_count"] == 1

    other = make_user()
    hidden = _create(
        client,
        [("notes.png", build_png(64), PNG_CT)],
        document_id=document_id,
        headers=other["headers"],
    )
    missing = _create(
        client,
        [("notes.png", build_png(64), PNG_CT)],
        document_id="missing-document",
    )

    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "document_not_found"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "document_not_found"


def test_outline_image_interpretation_review_matching_and_missing_topics(client, monkeypatch):
    document = client.post(
        "/api/v1/documents", params={"wait": True},
        files={"file": ("lecture.pdf", build_pdf(["Photosynthesis converts light energy into chemical energy."]), PDF_CT)},
    )
    assert document.status_code == 201
    source = _create(client, [("outline.png", build_png(64), PNG_CT)], document_id=document.json()["document_id"]).json()
    monkeypatch.setattr(get_settings(), "gemini_api_key", "test-key")
    monkeypatch.setattr(get_settings(), "gemini_model", "gemini-3.8-flash")
    monkeypatch.setattr("app.services.ai_client.GeminiClient.interpret_outline_image", lambda self, image, mime: ["Photosynthesis", "Missing topic"])
    response = client.post(f"/api/v1/topic-sources/{source['source_id']}/interpret-outline")
    assert response.status_code == 200
    assert response.json()["topics"] == ["Photosynthesis", "Missing topic"]

    confirmed = client.post(
        f"/api/v1/topic-sources/{source['source_id']}/topic-review",
        json={"topics": ["Photosynthesis", "Missing topic"]},
    )
    assert confirmed.status_code == 200
    topics = confirmed.json()["topics"]
    assert topics[0]["status"] == "matched"
    assert topics[1]["status"] == "unmatched"


def test_outline_without_image_reports_missing_topics(client):
    source_response = client.post(
        "/api/v1/topic-sources", data={"jottings_text": "My lecturer outline"}
    )
    assert source_response.status_code == 201
    source = source_response.json()
    response = client.post(f"/api/v1/topic-sources/{source['source_id']}/interpret-outline")
    assert response.status_code == 200
    assert response.json()["topics"] == []
    assert response.json()["warnings"][0]["code"] == "outline_image_missing"


def test_outline_topic_limits_return_partial_topics_with_warnings(client, monkeypatch):
    source = _create(client, [("outline.png", build_png(64), PNG_CT)]).json()
    settings = get_settings()
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(settings, "gemini_model", "gemini-3.8-flash")
    monkeypatch.setattr(settings, "topic_max_topics", 2)
    monkeypatch.setattr(settings, "topic_max_topic_chars", 20)
    monkeypatch.setattr(
        "app.services.ai_client.GeminiClient.interpret_outline_image",
        lambda self, image, mime: [
            "Cell division",
            "Energy flow",
            "A detected topic that is much too long",
            "Ecology",
        ],
    )

    response = client.post(
        f"/api/v1/topic-sources/{source['source_id']}/interpret-outline"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["topics"] == ["Cell division", "Energy flow"]
    warning_codes = {warning["code"] for warning in body["warnings"]}
    assert warning_codes == {
        "outline_topic_too_long",
        "outline_topic_limit_reached",
    }


def test_document_availability_tracks_deleted_files(client, identity):
    document_id = _upload_document(client)
    source = _create(
        client,
        [("notes.png", build_png(64), PNG_CT)],
        document_id=document_id,
    ).json()
    url = f"/api/v1/topic-sources/{source['source_id']}"
    assert source["document_available"] is True

    deleted = client.delete(f"/api/v1/documents/{document_id}")
    assert deleted.status_code == 204

    fetched = client.get(url)
    assert fetched.status_code == 200
    body = fetched.json()
    assert body["document"] is not None
    assert body["document"]["document_id"] == document_id
    assert body["document_available"] is False


def test_document_availability_tracks_missing_file(client, identity):
    document_id = _upload_document(client)
    source = _create(
        client,
        [("notes.png", build_png(64), PNG_CT)],
        document_id=document_id,
    ).json()
    record = storage.get_for_owner(document_id, identity["user_id"])
    assert record is not None
    record.path.unlink()

    fetched = client.get(f"/api/v1/topic-sources/{source['source_id']}")

    assert fetched.status_code == 200
    assert fetched.json()["document_available"] is False


def _upload_document_pages(client, pages: list[str]) -> str:
    response = client.post(
        "/api/v1/documents",
        params={"wait": True},
        files={"file": ("lecture.pdf", build_pdf(pages), PDF_CT)},
    )
    assert response.status_code == 201, response.text
    return response.json()["document_id"]


def test_text_only_jottings_drive_topic_directed_source_output(client):
    document_id = _upload_document_pages(
        client,
        [
            "The Calvin cycle fixes carbon dioxide.",
            "Photosynthesis converts light energy into chemical energy.",
            "Mitosis separates duplicated chromosomes.",
        ],
    )

    created = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id, "jottings_text": "Calvin cycle"},
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["image_count"] == 0
    assert body["jottings_text"] == "Calvin cycle"
    assert body["status"] == "ok"
    assert body["total_chars"] == len("Calvin cycle")
    assert body["warnings"] == []

    source_id = body["source_id"]
    review = client.post(
        f"/api/v1/topic-sources/{source_id}/topic-review", json={}
    )
    assert review.status_code == 200, review.text
    reviewed = review.json()
    assert reviewed["segmentation"]["input"] == "jottings"
    assert [topic["text"] for topic in reviewed["topics"]] == ["Calvin cycle"]
    assert reviewed["topics"][0]["source_labels"] == ["Typed jottings"]
    assert reviewed["topics"][0]["status"] == "matched"
    assert "Calvin cycle" in reviewed["topics"][0]["matches"][0]["passage"]

    study = client.post(
        f"/api/v1/topic-sources/{source_id}/study-output",
        json={"mode": "source_passages"},
    )
    assert study.status_code == 200, study.text
    output = study.json()
    assert output["status"] == "completed"
    assert output["topic_count"] == 1
    passages = output["topics"][0]["source_passages"]
    assert len(passages) == 1
    assert "Calvin cycle" in passages[0]["passage"]
    assert "Photosynthesis" not in passages[0]["passage"]
    assert "Mitosis" not in passages[0]["passage"]


def test_typed_jottings_and_photo_transcripts_keep_provenance(client):
    document_id = _upload_document_pages(
        client,
        [
            "The Calvin cycle fixes carbon dioxide.",
            "Photosynthesis converts light energy into chemical energy.",
        ],
    )
    created = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id, "jottings_text": "Calvin cycle"},
        files={"images": ("notes.png", build_png(64), PNG_CT)},
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["status"] == "partial"

    source_id = source["source_id"]
    corrected = client.patch(
        f"/api/v1/topic-sources/{source_id}",
        json={"transcripts": [{"index": 0, "text": "Photosynthesis"}]},
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["status"] == "ok"

    review = client.post(
        f"/api/v1/topic-sources/{source_id}/topic-review", json={}
    )
    assert review.status_code == 200, review.text
    body = review.json()
    assert body["segmentation"]["input"] == "combined"
    topics = {topic["text"]: topic for topic in body["topics"]}
    assert topics["Calvin cycle"]["source_labels"] == ["Typed jottings"]
    assert topics["Photosynthesis"]["source_labels"] == ["Photo 1"]
    assert topics["Calvin cycle"]["status"] == "matched"
    assert topics["Photosynthesis"]["status"] == "matched"


def test_typed_jottings_can_be_corrected(client):
    document_id = _upload_document_pages(
        client, ["The Calvin cycle fixes carbon dioxide."]
    )
    source = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id, "jottings_text": "Calvin cycle"},
    ).json()

    updated = client.patch(
        f"/api/v1/topic-sources/{source['source_id']}",
        json={"jottings_text": "Photosynthesis"},
    )

    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["jottings_text"] == "Photosynthesis"
    assert body["total_chars"] == len("Photosynthesis")

    review = client.post(
        f"/api/v1/topic-sources/{source['source_id']}/topic-review", json={}
    )
    assert review.status_code == 200
    assert [topic["text"] for topic in review.json()["topics"]] == [
        "Photosynthesis"
    ]


def test_typed_jottings_update_length_limit(client, monkeypatch):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    monkeypatch.setattr(get_settings(), "topic_max_jottings_chars", 5)

    response = client.patch(
        f"/api/v1/topic-sources/{source['source_id']}",
        json={"jottings_text": "too long"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "jottings_too_long"


def test_editing_jottings_clears_saved_topics(client):
    document_id = _upload_document_pages(
        client, ["The Calvin cycle fixes carbon dioxide."]
    )
    source = client.post(
        "/api/v1/topic-sources",
        data={"document_id": document_id, "jottings_text": "Calvin cycle"},
    ).json()
    url = f"/api/v1/topic-sources/{source['source_id']}"

    saved = client.patch(url, json={"topics": ["Calvin cycle"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["topics"] == ["Calvin cycle"]

    corrected = client.patch(url, json={"jottings_text": "Photosynthesis"})
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["topics"] == []

    review = client.post(f"{url}/topic-review", json={})
    assert review.status_code == 200, review.text
    assert [topic["text"] for topic in review.json()["topics"]] == [
        "Photosynthesis"
    ]


def test_topics_are_trimmed_deduplicated_and_bounded(client, monkeypatch):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    url = f"/api/v1/topic-sources/{source['source_id']}"

    updated = client.patch(url, json={"topics": [" Photosynthesis ", "Respiration", "Photosynthesis"]})

    assert updated.status_code == 200
    assert updated.json()["topics"] == ["Photosynthesis", "Respiration"]

    monkeypatch.setattr(get_settings(), "topic_max_topics", 1)
    too_many = client.patch(url, json={"topics": ["One", "Two"]})
    assert too_many.status_code == 422
    assert too_many.json()["error"]["code"] == "too_many_topics"

    monkeypatch.setattr(get_settings(), "topic_max_topic_chars", 3)
    too_long = client.patch(url, json={"topics": ["Four"]})
    assert too_long.status_code == 422
    assert too_long.json()["error"]["code"] == "topic_too_long"

    empty = client.patch(url, json={"topics": ["   "]})
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "invalid_topic"


def test_transcripts_can_be_edited_and_recompute_status(client):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    assert source["status"] == "no_text"
    url = f"/api/v1/topic-sources/{source['source_id']}"

    updated = client.patch(
        url, json={"transcripts": [{"index": 0, "text": "Edited note text"}]}
    )

    assert updated.status_code == 200
    body = updated.json()
    assert body["status"] == "ok"
    assert body["images"][0]["text"] == "Edited note text"
    assert body["images"][0]["ocr_status"] == "manual"
    assert body["images"][0]["transcript_source"] == "manual"
    assert _codes(body["images"][0]["warnings"]) == [
        "transcript_manually_edited"
    ]
    assert body["total_chars"] == len("Edited note text")
    assert _codes(body["warnings"]) == ["ocr_handwriting_unreliable"]

    fetched = client.get(url)
    assert fetched.status_code == 200
    assert fetched.json()["images"][0]["text"] == "Edited note text"

    missing_index = client.patch(
        url, json={"transcripts": [{"index": 9, "text": "x"}]}
    )
    assert missing_index.status_code == 400
    assert missing_index.json()["error"]["code"] == "image_not_found"

    duplicate = client.patch(
        url,
        json={
            "transcripts": [
                {"index": 0, "text": "one"},
                {"index": 0, "text": "two"},
            ]
        },
    )
    assert duplicate.status_code == 400
    assert duplicate.json()["error"]["code"] == "duplicate_transcript_index"

    no_update = client.patch(url, json={})
    assert no_update.status_code == 400
    assert no_update.json()["error"]["code"] == "no_update"


@pytest.mark.parametrize("ocr_state", ["ok", "failed", "disabled", "empty"])
def test_manual_transcript_replaces_stale_ocr_metadata(
    client, monkeypatch, ocr_state
):
    if ocr_state != "disabled":
        _enable_ocr(monkeypatch)
        if ocr_state == "ok":
            monkeypatch.setattr(
                "app.services.topic_images.run_tesseract",
                lambda image, suffix, settings: "Recognized lecture text.",
            )
        elif ocr_state == "failed":
            def fail(image, suffix, settings):
                raise OcrFailed("bad")

            monkeypatch.setattr(
                "app.services.topic_images.run_tesseract", fail
            )
        else:
            monkeypatch.setattr(
                "app.services.topic_images.run_tesseract",
                lambda image, suffix, settings: "   \n",
            )

    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    assert source["images"][0]["transcript_source"] == "ocr"

    response = client.patch(
        f"/api/v1/topic-sources/{source['source_id']}",
        json={"transcripts": [{"index": 0, "text": "Corrected notes"}]},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    image = body["images"][0]
    assert body["status"] == "ok"
    assert image["text"] == "Corrected notes"
    assert image["ocr_status"] == "manual"
    assert image["transcript_source"] == "manual"
    assert _codes(image["warnings"]) == ["transcript_manually_edited"]
    assert _codes(body["warnings"]) == ["ocr_handwriting_unreliable"]
    assert "ocr_incomplete" not in _codes(body["warnings"])


def test_clearing_a_manual_transcript_removes_text_status(client):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    url = f"/api/v1/topic-sources/{source['source_id']}"

    response = client.patch(
        url, json={"transcripts": [{"index": 0, "text": "   "}]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "no_text"
    assert body["images"][0]["ocr_status"] == "empty"
    assert body["images"][0]["transcript_source"] == "manual"
    assert "ocr_incomplete" in _codes(body["warnings"])


def test_transcript_length_is_bounded(client, monkeypatch):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    monkeypatch.setattr(get_settings(), "topic_max_transcript_chars", 3)

    response = client.patch(
        f"/api/v1/topic-sources/{source['source_id']}",
        json={"transcripts": [{"index": 0, "text": "four"}]},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "transcript_too_long"


def test_topic_sources_are_owner_scoped(client, identity, make_user):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    source_id = source["source_id"]
    other = make_user()

    read = client.get(
        f"/api/v1/topic-sources/{source_id}", headers=other["headers"]
    )
    patch = client.patch(
        f"/api/v1/topic-sources/{source_id}",
        json={"topics": ["Stolen"]},
        headers=other["headers"],
    )
    own = client.get(
        f"/api/v1/topic-sources/{source_id}", headers=identity["headers"]
    )

    assert read.status_code == 404
    assert read.json()["error"]["code"] == "topic_source_not_found"
    assert patch.status_code == 404
    assert own.status_code == 200
    assert own.json()["topics"] == []
    assert topic_sources.get_for_owner(source_id, other["user_id"]) is None


def test_source_can_be_deleted_with_its_images(client, identity):
    first = _create(client, [("one.png", build_png(64), PNG_CT)]).json()
    second = _create(client, [("two.png", build_png(64), PNG_CT)]).json()
    record = topic_sources.get_for_owner(first["source_id"], identity["user_id"])
    assert record is not None
    directory = record.images[0].path.parent

    response = client.delete(f"/api/v1/topic-sources/{first['source_id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert not directory.exists()
    assert topic_sources.get_for_owner(first["source_id"], identity["user_id"]) is None
    assert not db.query(
        "SELECT source_id FROM topic_sources WHERE source_id = ?",
        (first["source_id"],),
    )
    assert (
        client.get(f"/api/v1/topic-sources/{second['source_id']}").status_code
        == 200
    )

    missing = client.delete(f"/api/v1/topic-sources/{first['source_id']}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "topic_source_not_found"


def test_source_deletion_is_owner_scoped(client, identity, make_user):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    record = topic_sources.get_for_owner(
        source["source_id"], identity["user_id"]
    )
    assert record is not None
    directory = record.images[0].path.parent
    other = make_user()

    denied = client.delete(
        f"/api/v1/topic-sources/{source['source_id']}",
        headers=other["headers"],
    )

    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "topic_source_not_found"
    assert directory.exists()
    assert topic_sources.get_for_owner(
        source["source_id"], identity["user_id"]
    ) is not None
    assert (
        client.get(f"/api/v1/topic-sources/{source['source_id']}").status_code
        == 200
    )


def test_expired_source_read_has_no_delete_side_effect(
    client, identity, make_user
):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    record = topic_sources.get_for_owner(source["source_id"], identity["user_id"])
    assert record is not None
    directory = record.images[0].path.parent

    db.execute(
        "UPDATE topic_sources SET expires_at = 0 WHERE source_id = ?",
        (source["source_id"],),
    )
    other = make_user()

    denied = client.get(
        f"/api/v1/topic-sources/{source['source_id']}",
        headers=other["headers"],
    )
    response = client.get(f"/api/v1/topic-sources/{source['source_id']}")

    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "topic_source_not_found"
    assert response.status_code == 404
    assert directory.exists()
    assert topic_sources.get_for_owner(source["source_id"], identity["user_id"]) is None
    assert db.query(
        "SELECT source_id FROM topic_sources WHERE source_id = ?",
        (source["source_id"],),
    )

    topic_sources.sweep()

    assert not directory.exists()
    assert not db.query(
        "SELECT source_id FROM topic_sources WHERE source_id = ?",
        (source["source_id"],),
    )


def test_sweep_removes_old_orphan_directories(client, identity):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    record = topic_sources.get_for_owner(source["source_id"], identity["user_id"])
    assert record is not None
    source_directory = record.images[0].path.parent

    orphan = topic_sources.root / "orphan-source"
    orphan.mkdir()
    (orphan / "000.png").write_bytes(b"orphan")
    old = time.time() - 7200
    os.utime(orphan, (old, old))

    removed = topic_sources.sweep()

    assert removed == 0
    assert not orphan.exists()
    assert source_directory.exists()

def test_source_survives_database_reconnect(client, monkeypatch):
    _enable_ocr(monkeypatch)
    monkeypatch.setattr(
        "app.services.topic_images.run_tesseract",
        lambda image, suffix, settings: "Persisted OCR transcript.",
    )
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()

    db.reset()
    db.configure(get_settings().database_path)

    fetched = client.get(f"/api/v1/topic-sources/{source['source_id']}")

    assert fetched.status_code == 200
    assert fetched.json()["images"][0]["text"] == "Persisted OCR transcript."
    assert fetched.json()["status"] == "ok"


def test_concurrent_topic_and_transcript_updates_merge(
    client, identity, monkeypatch
):
    source = _create(client, [("notes.png", build_png(64), PNG_CT)]).json()
    source_id = source["source_id"]
    original_get = topic_sources._get
    barrier = threading.Barrier(2)
    guard = threading.Lock()
    reads = 0

    def synchronized_get(candidate_id: str):
        nonlocal reads
        record = original_get(candidate_id)
        with guard:
            wait = reads < 2
            reads += 1
        if wait:
            barrier.wait(timeout=5)
        return record

    monkeypatch.setattr(topic_sources, "_get", synchronized_get)

    def update(payload):
        return topic_sources.update_for_owner(
            source_id, identity["user_id"], **payload
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        topics_future = pool.submit(update, {"topics": ["Merged topic"]})
        transcript_future = pool.submit(
            update, {"transcripts": {0: "Merged transcript"}}
        )
        topics_future.result(timeout=10)
        transcript_future.result(timeout=10)

    final = topic_sources.get_for_owner(source_id, identity["user_id"])
    assert final is not None
    assert final.topics == ["Merged topic"]
    assert final.images[0].text == "Merged transcript"
    assert final.images[0].ocr_status == "manual"


def test_quota_is_enforced_and_freed_by_delete(client, identity, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "topic_max_sources_per_user", 2)
    topic_sources.configure(
        settings.temp_dir, settings.retention_minutes, 2
    )

    first = _create(client, [("one.png", build_png(64), PNG_CT)])
    second = _create(client, [("two.png", build_png(64), PNG_CT)])
    rejected = _create(client, [("three.png", build_png(64), PNG_CT)])

    assert first.status_code == 201
    assert second.status_code == 201
    assert rejected.status_code == 429
    assert rejected.json()["error"]["code"] == "topic_source_limit_reached"
    assert rejected.json()["error"]["details"]["max_sources"] == 2

    assert (
        client.delete(
            f"/api/v1/topic-sources/{first.json()['source_id']}"
        ).status_code
        == 204
    )
    replacement = _create(client, [("four.png", build_png(64), PNG_CT)])
    assert replacement.status_code == 201


def test_parallel_creates_cannot_exceed_quota(client, identity):
    settings = get_settings()
    topic_sources.configure(
        settings.temp_dir, settings.retention_minutes, 2
    )
    barrier = threading.Barrier(3)

    def create():
        barrier.wait(timeout=5)
        try:
            return topic_sources.save(
                identity["user_id"],
                document=None,
                images=[_image_input()],
            )
        except ApiError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(create) for _ in range(3)]
        results = [future.result(timeout=15) for future in futures]

    created = [result for result in results if not isinstance(result, ApiError)]
    rejected = [result for result in results if isinstance(result, ApiError)]
    assert len(created) == 2
    assert len(rejected) == 1
    assert rejected[0].code == "topic_source_limit_reached"
    rows = db.query(
        "SELECT source_id FROM topic_sources WHERE owner_id = ?",
        (identity["user_id"],),
    )
    assert len(rows) == 2


def test_ocr_concurrency_is_bounded(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ocr_page_limit", 10)
    topic_images.configure_ocr_concurrency(2)
    active = 0
    peak = 0
    guard = threading.Lock()

    def fake_runner(data: bytes, suffix: str, settings) -> str:
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.02)
        with guard:
            active -= 1
        return "Concurrency test text."

    image = topic_images.ValidatedImage(
        filename="note.png",
        content_type=PNG_CT,
        suffix=".png",
        data=build_png(64),
        width=64,
        height=64,
    )
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(
                pool.map(
                    lambda _: topic_images.ocr_image(
                        image, settings, runner=fake_runner
                    ),
                    range(8),
                )
            )
    finally:
        topic_images.configure_ocr_concurrency(4)

    assert peak <= 2
    assert all(result.status == "ok" for result in results)
