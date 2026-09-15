import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.auth import require_auth
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _real_auth(monkeypatch: pytest.MonkeyPatch):
    """Undo conftest's global bypass so this file exercises the real gate."""
    app.dependency_overrides.pop(require_auth, None)
    monkeypatch.setattr(auth.settings, "AUTH_USERNAME", "demo")
    monkeypatch.setattr(auth.settings, "AUTH_PASSWORD", "s3cret")
    monkeypatch.setattr(auth.settings, "AUTH_SECRET_KEY", "test-signing-key")
    yield


def test_login_rejects_wrong_credentials():
    response = client.post("/auth/login", json={"username": "demo", "password": "wrong"})
    assert response.status_code == 401


def test_login_issues_token_that_unlocks_protected_routes():
    login = client.post("/auth/login", json={"username": "demo", "password": "s3cret"})
    assert login.status_code == 200
    token = login.json()["token"]

    response = client.get("/cases", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code != 401


def test_protected_route_rejects_missing_token():
    response = client.get("/cases")
    assert response.status_code == 401


def test_protected_route_rejects_garbage_token():
    response = client.get("/cases", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_health_does_not_require_auth():
    response = client.get("/health")
    assert response.status_code == 200


def test_verify_token_rejects_expired_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(auth, "TOKEN_TTL_SECONDS", -1)
    token = auth.create_token()
    assert auth._verify_token(token) is False


def test_verify_token_rejects_tampered_signature():
    token = auth.create_token()
    payload_b64, _sig = token.split(".", 1)
    assert auth._verify_token(f"{payload_b64}.tampered") is False
