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


def test_root_exposes_service_links() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["health"] == "/health"
