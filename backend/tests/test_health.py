import json
import os
import subprocess
import sys
from pathlib import Path
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
    assert "connection refused" not in body["detail"]


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
        started = False

        async with lifespan(app):
            started = True

    mock_check.assert_called_once_with()
    assert started



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


# --- API docs are hidden when deployed (PENDING_TESTS row 9) -----------------------------------------------------
# Deployed, Kong's catch-all would serve /docs and the schema to anyone. The app reads EVENT_TRANSPORT at import, so
# the deployed app is imported in a fresh process rather than reloaded here (a reload re-registers every handler).

_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")
_PROBE = """
import json
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)  # no `with`: the lifespan (Pub/Sub, the database) never starts
print(json.dumps({path: client.get(path).status_code for path in %r}))
""" % (_DOC_PATHS,)


def _doc_statuses(**env: str) -> dict[str, int]:
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=backend, env={**os.environ, **env},
        capture_output=True, text=True, timeout=120, check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_deployed_the_api_docs_and_schema_are_not_served() -> None:
    statuses = _doc_statuses(EVENT_TRANSPORT="pubsub", GOOGLE_CLOUD_PROJECT="test-project", EVENT_DELIVERY="push")

    assert statuses == {path: 404 for path in _DOC_PATHS}


def test_in_process_the_api_docs_and_schema_are_served() -> None:
    assert {path: client.get(path).status_code for path in _DOC_PATHS} == {path: 200 for path in _DOC_PATHS}



# --- Pull mode (PENDING_TESTS row 33) -----------------------------------------------------------------------------
# PUSHES and PULLS are read at import, so the pull-mode app is imported in a fresh process.

_PULL_PROBE = """
import json
from fastapi.testclient import TestClient
from app import main
client = TestClient(main.app)
print(json.dumps({
    "push_route": any(getattr(r, "path", "") == "/push" for r in main.app.routes),
    "pushes": main.PUSHES, "pulls": main.PULLS,
    "push_status": client.post("/push", json={}).status_code,
}))
try:
    main.check_push_verification()
    print(json.dumps({"verified": True}))
except RuntimeError as e:
    print(json.dumps({"verified": False, "error": str(e)}))
"""


def _pull_mode_probe() -> tuple[dict, dict]:
    backend = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if k not in ("PUSH_AUDIENCE", "PUSH_SERVICE_ACCOUNT", "PUBSUB_EMULATOR_HOST")}
    env.update(EVENT_TRANSPORT="pubsub", GOOGLE_CLOUD_PROJECT="test-project", EVENT_DELIVERY="pull", ROLE="doc-worker")
    result = subprocess.run(
        [sys.executable, "-c", _PULL_PROBE], cwd=backend, env=env,
        capture_output=True, text=True, timeout=120, check=True,
    )
    lines = result.stdout.strip().splitlines()
    return json.loads(lines[-2]), json.loads(lines[-1])


def test_pull_mode_has_no_push_route_and_needs_no_push_audience() -> None:
    wiring, verification = _pull_mode_probe()

    assert wiring == {"push_route": False, "pushes": False, "pulls": True, "push_status": 404}
    assert verification == {"verified": True}


async def test_lifespan_starts_and_stops_the_puller_in_pull_mode() -> None:
    calls: list[str] = []

    async def _start() -> None:
        calls.append("start")

    async def _stop() -> None:
        calls.append("stop")

    with (
        patch("app.main.check_db_connection"),
        patch("app.main.PULLS", True),
        patch("app.main.event_pull.start", _start),
        patch("app.main.event_pull.stop", _stop),
    ):
        async with lifespan(app):
            assert calls == ["start"]

    assert calls == ["start", "stop"]


async def test_lifespan_does_not_start_the_puller_when_not_pulling() -> None:
    with (
        patch("app.main.check_db_connection"),
        patch("app.main.PULLS", False),
        patch("app.main.event_pull.start", new_callable=AsyncMock) as start,
        patch("app.main.event_pull.stop", new_callable=AsyncMock) as stop,
    ):
        async with lifespan(app):
            pass

    start.assert_not_awaited()
    stop.assert_not_awaited()


# --- Metrics on their own port (PENDING_TESTS row 36) -------------------------------------------------------------


async def test_lifespan_serves_metrics_on_the_configured_port() -> None:
    with (
        patch("app.main.check_db_connection"),
        patch("app.main.settings.METRICS_PORT", 9464),
        patch("app.main.metrics.serve_on") as serve_on,
    ):
        async with lifespan(app):
            pass

    serve_on.assert_called_once_with(9464)


async def test_lifespan_serves_no_metrics_port_when_unset() -> None:
    with (
        patch("app.main.check_db_connection"),
        patch("app.main.settings.METRICS_PORT", 0),
        patch("app.main.metrics.serve_on") as serve_on,
    ):
        async with lifespan(app):
            pass

    serve_on.assert_not_called()
