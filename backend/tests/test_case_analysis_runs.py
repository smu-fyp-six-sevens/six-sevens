"""
A case's earlier analyses (GET /cases/{id}/analysis/runs and .../runs/{run_id}): newest first, with who started each,
its score and the change from the run before, its finding count and whether it is out of date; any one opened whole.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.case_pipeline import service
from app.case_pipeline.models import CaseAnalysis
from app.main import app
from app.models import CaseRecord

client = TestClient(app)
NOW = datetime.now(timezone.utc)
RUN_1, RUN_2, RUN_3 = (f"00000000-0000-0000-0000-00000000000{n}" for n in (1, 2, 3))


def _row(run_id: str, minutes_ago: int, *, status: str = "complete", score: int | None = None,
         level: str | None = None, findings: int = 0, fingerprint: str = "old") -> dict:
    return {
        "run_id": run_id, "status": status, "fingerprint": fingerprint,
        "started_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(), "finished_at": None,
        "risk_score": score, "risk_level": level, "findings": [{"id": f"f{n}"} for n in range(findings)],
    }


def _list(rows: list[dict], *, requested: list[SimpleNamespace] | None = None, audit_error: bool = False):
    with (
        patch.object(service.repository, "list_runs", return_value=rows),
        patch.object(service.document_repository, "list_documents", return_value=[]),
        patch.object(service.langgraph_trace, "get_detection_states", return_value=({}, set())),
        patch.object(service.state, "compute_fingerprint", return_value="now"),
        patch.object(service.audit, "list_events", side_effect=RuntimeError("down") if audit_error else None,
                     return_value=requested or []),
    ):
        return service.list_runs("case-1")


def test_runs_are_newest_first_with_their_starter_score_change_and_staleness() -> None:
    rows = [
        _row(RUN_3, 1, score=48, level="Medium", findings=1, fingerprint="now"),
        _row(RUN_2, 30, status="failed"),
        _row(RUN_1, 60, score=72, level="High", findings=3),
    ]
    requested = [
        SimpleNamespace(actor="bob", detail={"run_id": RUN_3}),
        SimpleNamespace(actor="demo", detail={"run_id": RUN_1}),
    ]

    runs = _list(rows, requested=requested)

    assert [r.run_id for r in runs] == [RUN_3, RUN_2, RUN_1]
    assert [r.started_by for r in runs] == ["bob", None, "demo"]
    assert [(r.risk_level, r.risk_score, r.score_change) for r in runs] == [
        ("Medium", 48, -24), (None, None, None), ("High", 72, None),
    ]
    assert [r.finding_count for r in runs] == [1, 0, 3]
    assert [r.stale for r in runs] == [False, True, True]


def test_a_run_still_marked_running_past_the_time_limit_reads_as_failed() -> None:
    runs = _list([_row(RUN_1, 60 * 24, status="running")])

    assert runs[0].status == "failed"


def test_the_list_still_loads_when_the_audit_trail_cannot_be_read() -> None:
    runs = _list([_row(RUN_1, 5, score=30, level="Low")], audit_error=True)

    assert runs[0].started_by is None and runs[0].risk_score == 30


def test_a_case_with_no_runs_has_an_empty_list() -> None:
    assert _list([]) == []


# --- routes ------------------------------------------------------------------------------------------


def _case() -> CaseRecord:
    return CaseRecord(id="case-1", greenlink_case_id="GLDB-1", created_at=NOW, updated_at=NOW)


def test_the_list_route_returns_the_runs() -> None:
    with (
        patch("app.routers._dependencies.case_service") as cases,
        patch.object(service, "list_runs", return_value=[]) as list_runs,
    ):
        cases.get_case.return_value = _case()
        response = client.get("/cases/case-1/analysis/runs")

    assert response.status_code == 200 and response.json() == []
    list_runs.assert_called_once_with("case-1")


def test_an_earlier_run_opens_whole() -> None:
    analysis = CaseAnalysis(run_id=RUN_1, case_id="case-1", status="complete", fingerprint="old", started_at=NOW,
                            summary="An older prose summary.")
    with (
        patch("app.routers._dependencies.case_service") as cases,
        patch.object(service, "get_run", return_value=analysis) as get_run,
    ):
        cases.get_case.return_value = _case()
        response = client.get(f"/cases/case-1/analysis/runs/{RUN_1}")

    assert response.status_code == 200
    assert response.json()["summary"] == "An older prose summary."
    get_run.assert_called_once_with("case-1", RUN_1)


def test_an_unknown_or_malformed_run_id_is_not_found() -> None:
    with (
        patch("app.routers._dependencies.case_service") as cases,
        patch.object(service, "get_run", return_value=None) as get_run,
    ):
        cases.get_case.return_value = _case()
        unknown = client.get(f"/cases/case-1/analysis/runs/{RUN_2}")
        malformed = client.get("/cases/case-1/analysis/runs/not-a-uuid")

    assert unknown.status_code == 404 and malformed.status_code == 404
    get_run.assert_called_once_with("case-1", RUN_2)



def test_who_started_a_run_is_read_by_the_event_type_column_the_audit_trail_stores() -> None:
    from unittest.mock import MagicMock

    from app import audit

    query = MagicMock()
    for step in ("select", "order", "limit", "eq"):
        getattr(query, step).return_value = query
    query.execute.return_value.data = []
    client = MagicMock()
    client.table.return_value = query
    with patch.object(audit, "service_supabase_client", return_value=client):
        audit.list_events(case_id="case-1", event_type="case.analysis.requested")

    # Event.to_audit_row stores the type as `action`; there is no `type` column.
    query.eq.assert_any_call("action", "case.analysis.requested")


def test_a_stored_structured_summary_and_clean_checks_are_read_back() -> None:
    from unittest.mock import MagicMock

    from app.case_pipeline import repository

    row = {
        "run_id": RUN_1, "case_id": "case-1", "status": "complete", "fingerprint": "f", "started_at": NOW.isoformat(),
        "result": {
            "structured_summary": {"headline": "H.", "reasons": [{"text": "R", "finding_id": "x", "document_id": "d"}],
                                   "would_help": None, "source": "model"},
            "clean_checks": ["metadata_tool"],
        },
    }
    query = MagicMock()
    query.execute.return_value.data = [row]
    for step in ("select", "eq", "limit"):
        getattr(query, step).return_value = query
    with patch.object(repository, "supabase_client") as supabase:
        supabase.table.return_value = query
        analysis = repository.get_run("case-1", RUN_1)

    assert analysis is not None and analysis.structured_summary is not None
    assert (analysis.structured_summary.reasons[0].document_id, analysis.structured_summary.reasons[0].finding_id) == ("d", "x")
    assert analysis.clean_checks == ["metadata_tool"]
