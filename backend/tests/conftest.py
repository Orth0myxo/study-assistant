from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.db import db
from app.services.job_store import jobs
from app.services.storage import storage
from app.services.topic_results import topic_results
from app.services.topic_sources import topic_sources
from app.services.users import users

_DEFAULT_IDENTITIES: dict[int, dict[str, str]] = {}


@pytest.fixture(autouse=True)
def _disable_ocr(monkeypatch):
    monkeypatch.setattr(get_settings(), "ocr_page_limit", 0)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "temp_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "database_path", str(tmp_path / "study.db"))
    monkeypatch.setattr(settings, "retention_minutes", 60)
    db.reset()
    with TestClient(app) as test_client:
        registration = test_client.post("/api/v1/users")
        assert registration.status_code == 201, registration.text
        body = registration.json()
        headers = {"Authorization": f"Bearer {body['token']}"}
        test_client.headers.update(headers)
        identity = {
            "user_id": body["user_id"],
            "token": body["token"],
            "headers": headers,
        }
        _DEFAULT_IDENTITIES[id(test_client)] = identity
        try:
            yield test_client
        finally:
            _DEFAULT_IDENTITIES.pop(id(test_client), None)
            storage.clear()
            jobs.clear()
            topic_sources.clear()
            topic_results.clear()
            users.clear()
    db.reset()


@pytest.fixture()
def identity(client) -> dict[str, str]:
    """Identity and auth headers for the client fixture's default user."""
    return _DEFAULT_IDENTITIES[id(client)]


@pytest.fixture()
def make_user(client):
    """Register additional independent users for isolation tests."""

    def _make_user() -> dict[str, str]:
        response = client.post("/api/v1/users")
        assert response.status_code == 201, response.text
        body = response.json()
        return {
            "user_id": body["user_id"],
            "token": body["token"],
            "headers": {"Authorization": f"Bearer {body['token']}"},
        }

    return _make_user
