from __future__ import annotations

from app.config import get_settings


def test_api_is_open_when_no_token_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_access_token", None)

    assert client.get("/api/v1/study-jobs").status_code == 200


def test_user_identity_is_required_when_no_shared_token_configured(
    client, monkeypatch
):
    monkeypatch.setattr(get_settings(), "app_access_token", None)

    response = client.get("/api/v1/study-jobs", headers={"Authorization": ""})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_user_token"


def test_registration_is_exempt_from_shared_token(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_access_token", "shared-token")

    response = client.post("/api/v1/users")

    assert response.status_code == 201
    assert response.json()["token"]


def test_user_token_allows_api_without_shared_token(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "app_access_token", "shared-token")

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/study-jobs").status_code == 200
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


def test_shared_token_still_gates_unauthenticated_and_non_api_routes(
    client, monkeypatch
):
    monkeypatch.setattr(get_settings(), "app_access_token", "shared-token")

    unauthenticated = client.get(
        "/api/v1/study-jobs", headers={"Authorization": ""}
    )
    with_app_token = client.get(
        "/api/v1/study-jobs",
        headers={"Authorization": "", "X-App-Token": "shared-token"},
    )
    docs = client.get("/openapi.json")
    docs_with_token = client.get(
        "/openapi.json", headers={"X-App-Token": "shared-token"}
    )

    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["error"]["code"] == "unauthorized"
    assert with_app_token.status_code == 401
    assert with_app_token.json()["error"]["code"] == "invalid_user_token"
    assert docs.status_code == 401
    assert docs_with_token.status_code == 200
