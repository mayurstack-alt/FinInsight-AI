import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "x-request-id" in response.headers
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


def test_readiness_check() -> None:
    response = client.get("/api/v1/ready")
    assert response.status_code in (200, 503)
    data = response.json()
    assert "database" in data


def test_users_me_unauthorized_without_token() -> None:
    response = client.get("/api/v1/users/me")
    assert response.status_code == 401
    assert "detail" in response.json()

    patch_res = client.patch("/api/v1/users/me", json={"full_name": "Test User"})
    assert patch_res.status_code == 401


def test_users_me_invalid_token() -> None:
    response = client.get("/api/v1/users/me", headers={"Authorization": "Bearer invalid.token.value"})
    assert response.status_code in (401, 503)
