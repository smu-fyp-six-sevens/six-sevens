from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.health import check_db_connection
from app.main import app, lifespan


client = TestClient(app)


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_check_db_ok() -> None:
    with patch("app.health.check_db_connection") as mock_check:
        mock_check.return_value = None

        response = client.get("/health/db")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_check_db_unhealthy() -> None:
    with patch("app.health.check_db_connection") as mock_check:
        mock_check.side_effect = RuntimeError("connection refused")

        response = client.get("/health/db")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "unhealthy"
    assert "connection refused" in body["detail"]


def test_check_db_connection_queries_documents_table() -> None:
    # The /health/db tests above mock check_db_connection itself (to test
    # main.py's error-handling around it), so they never actually exercise
    # this function's own body — cover that directly here.
    with patch("app.health.supabase_client") as mock_supabase:
        check_db_connection()

    mock_supabase.table.assert_called_once_with("documents")
    mock_supabase.table.return_value.select.assert_called_once_with("id")
    mock_supabase.table.return_value.select.return_value.limit.assert_called_once_with(1)
    mock_supabase.table.return_value.select.return_value.limit.return_value.execute.assert_called_once_with()


def test_message_endpoint() -> None:
    response = client.get("/message")

    assert response.status_code == 200
    assert response.json() == {"message": "Hello from FastAPI"}


async def test_lifespan_starts_queue_and_passes_db_check() -> None:
    with (
        patch("app.main.document_queue") as mock_queue,
        patch("app.main.check_db_connection") as mock_check,
    ):
        async with lifespan(app):
            pass

    mock_queue.start.assert_called_once_with()
    mock_check.assert_called_once_with()


async def test_lifespan_tolerates_db_check_failure_at_startup() -> None:
    # A transient Supabase blip at boot shouldn't crash-loop the app — the
    # lifespan should log and continue rather than propagate.
    with (
        patch("app.main.document_queue") as mock_queue,
        patch("app.main.check_db_connection") as mock_check,
    ):
        mock_check.side_effect = RuntimeError("connection refused")

        async with lifespan(app):
            pass

    mock_queue.start.assert_called_once_with()


async def test_lifespan_tolerates_realtime_listener_start_failure() -> None:
    # Same non-fatal reasoning as the DB check above — e.g. SUPABASE_SECRET_KEY
    # not being configured in a given environment shouldn't crash-loop the
    # app, just degrade the case status WebSocket to "no live pushes".
    with (
        patch("app.main.document_queue") as mock_queue,
        patch("app.main.check_db_connection") as mock_check,
        patch("app.main.realtime_listener") as mock_realtime,
    ):
        mock_realtime.start = AsyncMock(side_effect=RuntimeError("SUPABASE_SECRET_KEY is not set"))
        mock_realtime.stop = AsyncMock()

        async with lifespan(app):
            pass

    mock_queue.start.assert_called_once_with()
    mock_check.assert_called_once_with()
    mock_realtime.start.assert_awaited_once_with()
    mock_realtime.stop.assert_awaited_once_with()
