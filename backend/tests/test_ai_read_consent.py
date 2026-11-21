from __future__ import annotations

import logging

import pytest

from app.config import get_settings
from app.errors import ApiError
from app.services.ai_read_consent import check_ai_read_consent
from app.services.db import db
from app.services.table_reader import TableRead
from app.services.users import users
from tests.helpers import build_png

PNG_CT = "image/png"


def _table_source(client) -> str:
    response = client.post(
        "/api/v1/topic-sources",
        data={"purpose": "table", "skip_ocr": "true"},
        files={"images": ("table.png", build_png(), PNG_CT)},
    )
    assert response.status_code == 201, response.text
    return response.json()["source_id"]


def _outline_source(client) -> str:
    response = client.post(
        "/api/v1/topic-sources",
        data={"purpose": "study", "skip_ocr": "true"},
        files={"images": ("outline.png", build_png(), PNG_CT)},
    )
    assert response.status_code == 201, response.text
    return response.json()["source_id"]


def _fake_table() -> TableRead:
    return TableRead(
        columns=["Value"],
        rows=[["7"]],
        confidence=[[1.0]],
        low_confidence=[[False]],
        engine="ai",
    )


def test_user_ai_reading_preference_defaults_and_persists(client, identity):
    response = client.get("/api/v1/users/preferences")
    assert response.status_code == 200
    assert response.json() == {"ai_reading_scanned_pages": "ask"}

    updated = client.put(
        "/api/v1/users/preferences",
        json={"ai_reading_scanned_pages": "always"},
    )
    assert updated.status_code == 200
    assert updated.json() == {"ai_reading_scanned_pages": "always"}
    assert users.get_ai_reading_consent(identity["user_id"]) == "always"
    row = db.query(
        "SELECT ai_reading_scanned_pages FROM users WHERE user_id = ?",
        (identity["user_id"],),
    )[0]
    assert row["ai_reading_scanned_pages"] == "always"


def test_ai_reading_preferences_are_owner_scoped(client, identity, make_user):
    other = make_user()
    assert identity["user_id"] != other["user_id"]
    saved = client.put(
        "/api/v1/users/preferences",
        json={"ai_reading_scanned_pages": "never"},
    )
    assert saved.status_code == 200

    other_preferences = client.get(
        "/api/v1/users/preferences", headers=other["headers"]
    )
    assert other_preferences.status_code == 200
    assert other_preferences.json()["ai_reading_scanned_pages"] == "ask"


def test_ai_reading_preference_rejects_unknown_values(client):
    response = client.put(
        "/api/v1/users/preferences",
        json={"ai_reading_scanned_pages": "sometimes"},
    )
    assert response.status_code == 422


def test_ai_read_consent_asks_before_image_is_sent(client, identity, caplog):
    settings = get_settings()
    with caplog.at_level(logging.INFO, logger="app.services.ai_read_consent"):
        allowed = check_ai_read_consent(
            identity["user_id"],
            None,
            settings,
            page_count=2,
            resource_id="document-a",
        )

    assert allowed is False
    assert "ai_read consent=ask decision=ask" in caplog.text
    assert "pages=2" in caplog.text
    assert "provider=groq" in caplog.text
    assert "resource_id=document-a" in caplog.text


def test_ai_table_read_requires_approval_before_provider_call(
    client, monkeypatch, caplog
):
    source_id = _table_source(client)
    called = False

    def read(*_args):
        nonlocal called
        called = True
        return _fake_table()

    monkeypatch.setattr("app.services.table_service.read_ai_table", read)
    with caplog.at_level(logging.INFO, logger="app.services.ai_read_consent"):
        response = client.post(
            "/api/v1/tables",
            json={"source_id": source_id, "engine": "ai"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ai_read_approval_required"
    assert response.json()["error"]["details"] == {
        "provider": "groq",
        "page_count": 1,
    }
    assert called is False
    assert "outcome=needs_approval" in caplog.text


def test_outline_image_read_requires_consent_and_names_groq(client, monkeypatch):
    source_id = _outline_source(client)
    called = False

    def reader(_settings):
        nonlocal called
        called = True
        raise AssertionError("the provider must not receive an image before consent")

    monkeypatch.setattr("app.api.topic_sources.get_image_reader", reader)
    response = client.post(
        f"/api/v1/topic-sources/{source_id}/interpret-outline"
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ai_read_approval_required"
    assert response.json()["error"]["details"] == {
        "provider": "groq",
        "page_count": 1,
    }
    assert called is False


def test_never_preference_blocks_outline_provider_even_allow_once(
    client, identity, monkeypatch
):
    source_id = _outline_source(client)
    users.set_ai_reading_consent(identity["user_id"], "never")
    called = False

    def reader(_settings):
        nonlocal called
        called = True
        raise AssertionError("Never must block every configured provider")

    monkeypatch.setattr("app.api.topic_sources.get_image_reader", reader)
    response = client.post(
        f"/api/v1/topic-sources/{source_id}/interpret-outline",
        json={"consent": "allow_once"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ai_read_never_allowed"
    assert called is False


def test_allow_and_remember_sets_always_before_ai_table_read(
    client, identity, monkeypatch, caplog
):
    source_id = _table_source(client)
    calls = []
    monkeypatch.setattr(
        "app.services.table_service.read_ai_table",
        lambda image, content_type, _settings: calls.append((image, content_type))
        or _fake_table(),
    )
    with caplog.at_level(logging.INFO, logger="app.services.ai_read_consent"):
        response = client.post(
            "/api/v1/tables",
            json={
                "source_id": source_id,
                "engine": "ai",
                "consent": "remember",
            },
        )

    assert response.status_code == 201, response.text
    assert len(calls) == 1
    assert users.get_ai_reading_consent(identity["user_id"]) == "always"
    assert "consent=always decision=allow remembered=true" in caplog.text
    assert "outcome=allowed" in caplog.text


def test_never_blocks_even_an_allow_once_request(client, monkeypatch):
    source_id = _table_source(client)
    client.put(
        "/api/v1/users/preferences",
        json={"ai_reading_scanned_pages": "never"},
    )
    called = False

    def read(*_args):
        nonlocal called
        called = True
        return _fake_table()

    monkeypatch.setattr("app.services.table_service.read_ai_table", read)
    response = client.post(
        "/api/v1/tables",
        json={
            "source_id": source_id,
            "engine": "ai",
            "consent": "allow_once",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ai_read_never_allowed"
    assert called is False


def test_dont_allow_records_decline_and_never_calls_provider(client, monkeypatch):
    source_id = _table_source(client)
    called = False

    def read(*_args):
        nonlocal called
        called = True
        return _fake_table()

    monkeypatch.setattr("app.services.table_service.read_ai_table", read)
    response = client.post(
        "/api/v1/tables",
        json={"source_id": source_id, "engine": "ai", "consent": "deny"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ai_read_declined"
    assert called is False


def test_user_preference_api_requires_identity(client):
    response = client.get(
        "/api/v1/users/preferences",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("value", ["ask", "always", "never"])
def test_user_store_accepts_only_supported_ai_reading_preferences(
    client, identity, value
):
    assert users.set_ai_reading_consent(identity["user_id"], value) == value
    assert users.get_ai_reading_consent(identity["user_id"]) == value


def test_user_store_rejects_unknown_ai_reading_preference(client, identity):
    with pytest.raises(ValueError, match="invalid AI reading consent preference"):
        users.set_ai_reading_consent(identity["user_id"], "sometimes")  # type: ignore[arg-type]
