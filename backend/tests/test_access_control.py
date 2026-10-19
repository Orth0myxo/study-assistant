from __future__ import annotations

from app.config import get_settings


def test_api_is_open_when_no_token_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_access_token", None)

    assert client.get("/api/v1/study-jobs").status_code == 200


def test_token_gate_protects_api_but_not_health(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_access_token", "shared-token")

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/study-jobs").status_code == 401
    assert (
        client.get(
            "/api/v1/study-jobs", headers={"X-App-Token": "shared-token"}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/study-jobs?token=shared-token").status_code == 200
    assert (
        client.get(
            "/api/v1/study-jobs", headers={"X-App-Token": "wrong"}
        ).status_code
        == 401
    )
