from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.health import check_db_connection
from app.main import app, check_signing_key, lifespan


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


class _RecordingQuery:
    """Stands in for a Supabase query builder: remembers the query it was asked to run."""

    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.ran: list[dict] = []
        self._query: dict = {}

    def table(self, name: str):
        self._query = {"table": name}
        return self

    def select(self, columns: str):
        self._query["columns"] = columns
        return self

    def limit(self, rows: int):
        self._query["limit"] = rows
        return self

    def execute(self):
        if self.fail:
            raise self.fail
        self.ran.append(self._query)


def test_check_db_connection_runs_one_cheap_query_against_documents() -> None:
    # The /health/db tests above replace check_db_connection itself (to test main.py's
    # error handling around it), so cover its own body here.
    database = _RecordingQuery()
    with patch("app.health.supabase_client", database):
        check_db_connection()

    assert database.ran == [{"table": "documents", "columns": "id", "limit": 1}]


def test_check_db_connection_lets_a_database_failure_through() -> None:
    database = _RecordingQuery(fail=RuntimeError("project paused"))
    with patch("app.health.supabase_client", database), pytest.raises(RuntimeError, match="project paused"):
        check_db_connection()


@pytest.mark.parametrize(
    ("roles", "in_process", "key", "refused"),
    [
        ({"api"}, False, "", True),
        ({"audit-metrics"}, False, "", True),
        ({"api"}, False, "real-key", False),
        ({"api"}, True, "", False),
        ({"doc-worker"}, False, "", False),
    ],
)
def test_a_deployed_token_checking_role_refuses_an_empty_signing_key(roles, in_process, key, refused) -> None:
    with (
        patch("app.main.ROLES", frozenset(roles)),
        patch("app.main.IN_PROCESS", in_process),
        patch("app.main.settings.AUTH_SECRET_KEY", key),
    ):
        if refused:
            with pytest.raises(RuntimeError, match="AUTH_SECRET_KEY"):
                check_signing_key()
        else:
            check_signing_key()


async def test_lifespan_passes_db_check() -> None:
    with (
        patch("app.main.check_db_connection") as mock_check,
    ):
        async with lifespan(app):
            pass

    mock_check.assert_called_once_with()


async def test_lifespan_tolerates_db_check_failure_at_startup() -> None:
    # A transient Supabase blip at boot shouldn't crash-loop the app — the
    # lifespan should log and continue rather than propagate.
    with (
        patch("app.main.check_db_connection") as mock_check,
    ):
        mock_check.side_effect = RuntimeError("connection refused")

        async with lifespan(app):
            pass



async def test_lifespan_tolerates_realtime_listener_start_failure() -> None:
    # Same non-fatal reasoning as the DB check above — e.g. SUPABASE_SECRET_KEY
    # not being configured in a given environment shouldn't crash-loop the
    # app, just degrade the case status WebSocket to "no live pushes".
    with (
        patch("app.main.check_db_connection") as mock_check,
        patch("app.main.realtime_listener") as mock_realtime,
    ):
        mock_realtime.start = AsyncMock(side_effect=RuntimeError("SUPABASE_SECRET_KEY is not set"))
        mock_realtime.stop = AsyncMock()

        async with lifespan(app):
            pass

    mock_check.assert_called_once_with()
    mock_realtime.start.assert_awaited_once_with()
    mock_realtime.stop.assert_awaited_once_with()
