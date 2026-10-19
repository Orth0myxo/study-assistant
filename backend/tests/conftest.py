from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services.db import db
from app.services.job_store import jobs
from app.services.storage import storage


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
        yield test_client
        storage.clear()
        jobs.clear()
    db.reset()
