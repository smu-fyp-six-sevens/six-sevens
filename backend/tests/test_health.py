from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_check_db_ok() -> None:
    with patch("app.main.check_db_connection") as mock_check:
        mock_check.return_value = None

        response = client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_check_db_unhealthy() -> None:
    with patch("app.main.check_db_connection") as mock_check:
        mock_check.side_effect = RuntimeError("connection refused")

        response = client.get("/health/db")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert "connection refused" in body["detail"]
