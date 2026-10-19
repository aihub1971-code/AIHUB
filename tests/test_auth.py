from fastapi.testclient import TestClient
from app.main import app
import pytest


@pytest.mark.parametrize("path", ["/v1/agents", "/v1/reports", "/v1/usage", "/v1/schedules", "/v1/system", "/v1/schedule-occurrences"])
def test_private_routes_require_owner_credentials(monkeypatch, path):
    monkeypatch.setenv("APP_USERNAME", "owner")
    monkeypatch.setenv("APP_PASSWORD", "local-password")
    response = TestClient(app).get(path)
    assert response.status_code == 401
    assert 'realm="AI HUB"' in response.headers["www-authenticate"]


def test_wrong_password_and_legacy_bearer_are_rejected(client):
    assert client.get("/v1/agents", auth=("test-owner", "wrong")).status_code == 401
    assert client.get("/v1/agents", auth=None, headers={"Authorization": "Bearer old-token"}).status_code == 401


def test_missing_owner_config_fails_closed():
    response = TestClient(app).get("/v1/agents")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "auth_not_configured"


def test_owner_can_read_and_sensitive_responses_are_not_cached(client):
    response = client.get("/v1/agents")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
