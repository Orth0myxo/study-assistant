from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_expected_shape() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()

    assert body["status"] in {"ok", "degraded"}
    assert isinstance(body["version"], str) and body["version"]
    assert body["ai"]["provider"] in {"configured", "missing"}
    assert body["ai"]["reachable"] is None
    assert body["limits"]["max_upload_mb"] > 0
    assert body["limits"]["retention_minutes"] > 0


def test_health_accepts_head_for_uptime_monitors() -> None:
    response = client.head("/health")

    assert response.status_code == 200
    assert response.content == b""


def test_health_reports_the_deployed_commit(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "2e1d07a1b2c3d4e5f60718293a4b5c6d7e8f9012")

    body = client.get("/health").json()

    assert body["commit"] == "2e1d07a"


def test_health_commit_falls_back_to_git_commit(monkeypatch) -> None:
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.setenv("GIT_COMMIT", "abcdef1234567890")

    assert client.get("/health").json()["commit"] == "abcdef1"


def test_health_commit_is_null_when_unknown(monkeypatch) -> None:
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)

    assert client.get("/health").json()["commit"] is None


def test_health_lists_active_providers_and_their_models(monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings().model_copy(
        update={
            "groq_api_key": "test-groq-key",
            "groq_model": "groq-test-model",
            "gemini_api_key": "test-gemini-key",
            "gemini_model": "gemini-test-model",
            "ai_provider_order": "groq,gemini",
        }
    )
    monkeypatch.setattr("app.api.health.get_settings", lambda: settings)

    ai = client.get("/health").json()["ai"]

    assert ai["providers"] == ["groq", "gemini"]
    assert ai["models"] == {"groq": "groq-test-model", "gemini": "gemini-test-model"}


def test_health_omits_a_provider_left_out_of_the_order(monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings().model_copy(
        update={
            "groq_api_key": "test-groq-key",
            "gemini_api_key": "test-gemini-key",
            "ai_provider_order": "gemini",
        }
    )
    monkeypatch.setattr("app.api.health.get_settings", lambda: settings)

    ai = client.get("/health").json()["ai"]

    assert ai["providers"] == ["gemini"]
    assert "groq" not in ai["models"]


def test_health_never_exposes_keys(monkeypatch) -> None:
    from app.config import get_settings

    settings = get_settings().model_copy(
        update={"groq_api_key": "super-secret-groq", "gemini_api_key": "super-secret-gemini"}
    )
    monkeypatch.setattr("app.api.health.get_settings", lambda: settings)

    text = client.get("/health").text

    assert "super-secret" not in text


def test_root_exposes_service_links() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["health"] == "/health"


def test_openapi_has_a_single_health_operation() -> None:
    # HEAD has its own hidden handler, so there is no duplicate operation id.
    assert set(app.openapi()["paths"]["/health"]) == {"get"}
