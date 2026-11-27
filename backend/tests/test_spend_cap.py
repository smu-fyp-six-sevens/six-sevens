"""
The Claude spend cap per case (app/spend_cap.py): once a case's runs have cost
CASE_SPEND_CAP_USD at list price, no detection run or case analysis starts on it. It fails open: a spend that can't
be read never stops a run.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import spend_cap
from app.case_pipeline import service
from app.config import settings
from app.main import app
from app.models import CaseRecord
from app.workers import detection as detection_worker

client = TestClient(app)
NOW = datetime.now(timezone.utc)


@pytest.fixture
def cap(monkeypatch: pytest.MonkeyPatch) -> float:
    monkeypatch.setattr(settings, "CASE_SPEND_CAP_USD", 5.0)
    return 5.0


def _spend(amount: float):
    return patch.object(spend_cap.audit, "cost_usd_many", return_value={"case-1": amount})


# --- When the cap is reached --------------------------------------------------------------------------------------

@pytest.mark.parametrize(("spent", "expected"), [(4.99, None), (5.0, 5.0), (7.25, 7.25)],
                         ids=["under the cap", "exactly at it", "past it"])
def test_the_cap_is_reached_at_or_past_the_limit(cap, spent, expected) -> None:
    with _spend(spent):
        assert spend_cap.reached("case-1") == expected


def test_a_case_with_no_recorded_spend_is_under_the_cap(cap) -> None:
    with patch.object(spend_cap.audit, "cost_usd_many", return_value={}):
        assert spend_cap.reached("case-1") is None


def test_a_cap_of_zero_switches_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CASE_SPEND_CAP_USD", 0.0)
    with _spend(1_000.0) as read:
        assert spend_cap.reached("case-1") is None
    read.assert_not_called()


def test_a_document_with_no_case_is_never_capped(cap) -> None:
    with _spend(1_000.0) as read:
        assert spend_cap.reached(None) is None
    read.assert_not_called()


def test_a_spend_that_cant_be_read_never_stops_a_run(cap) -> None:
    with patch.object(spend_cap.audit, "cost_usd_many", side_effect=RuntimeError("database down")):
        assert spend_cap.reached("case-1") is None


def test_the_message_says_how_much_and_what_the_cap_is(cap) -> None:
    text = spend_cap.message(7.25)
    assert "$7.25" in text and "$5.00" in text and "CASE_SPEND_CAP_USD" in text


# --- Detection --------------------------------------------------------------------------------------------------

async def test_detection_on_a_capped_case_never_runs_and_says_why() -> None:
    with (
        patch.object(detection_worker.spend_cap, "reached", return_value=7.25),
        patch.object(detection_worker, "run_detection", AsyncMock()) as run_detection,
        patch.object(detection_worker.detection_results, "save_error") as save_error,
        patch.object(detection_worker, "_record_end", AsyncMock()) as record_end,
        patch.object(detection_worker, "push_case_snapshot", AsyncMock()),
    ):
        await detection_worker.run("doc-1", "case-1")

    run_detection.assert_not_called()
    save_error.assert_called_once()
    assert save_error.call_args.args[0] == "doc-1" and "$7.25" in save_error.call_args.args[1]
    record_end.assert_awaited_once()
    outcome, detail = record_end.call_args.args[4], record_end.call_args.args[5]
    assert outcome == "failed" and "$7.25" in detail["error"]


async def test_detection_on_a_case_under_the_cap_runs() -> None:
    with (
        patch.object(detection_worker.spend_cap, "reached", return_value=None),
        patch.object(detection_worker, "run_detection", AsyncMock(return_value={"verdict": "clean", "findings": []}))
        as run_detection,
        patch.object(detection_worker.detection_results, "save_error") as save_error,
        patch.object(detection_worker, "_record_end", AsyncMock()),
        patch.object(detection_worker, "push_case_snapshot", AsyncMock()),
    ):
        await detection_worker.run("doc-1", "case-1")

    run_detection.assert_awaited_once_with("doc-1")
    save_error.assert_not_called()


# --- Case analysis ----------------------------------------------------------------------------------------------

def _case() -> CaseRecord:
    return CaseRecord(id="case-1", greenlink_case_id="GLDB-1", status="fraud_detection", created_at=NOW, updated_at=NOW)


def test_an_officers_analysis_on_a_capped_case_is_refused_with_the_message(published_events) -> None:
    with (
        patch("app.routers._dependencies.case_service") as cases,
        patch.object(service.settings, "CASE_ANALYSIS_ENABLED", True),
        patch.object(service.case_repository, "get_case", return_value=_case()),
        patch.object(service.document_repository, "list_documents", return_value=[]),
        patch.object(service, "unapproved_requirements", return_value=[]),
        patch.object(service, "is_ready_for_analysis", return_value=True),
        patch.object(service.repository, "latest_run_state", return_value=None),
        patch.object(service.repository, "count_runs_since", return_value=0),
        patch.object(service.spend_cap, "reached", return_value=7.25),
        patch.object(service.repository, "insert_run") as insert_run,
    ):
        cases.get_case.return_value = _case()
        response = client.post("/cases/case-1/analysis/run")

    assert response.status_code == 409
    assert "$7.25" in response.json()["detail"]
    insert_run.assert_not_called()
    assert published_events == []


def test_an_automatic_analysis_does_not_start_on_a_capped_case() -> None:
    with (
        patch.object(service.case_repository, "get_case", return_value=_case()),
        patch.object(service.document_repository, "list_documents", return_value=[]),
        patch.object(service.case_stage, "document_stage", return_value=None),
        patch.object(service.detection_results, "get_states", return_value=({}, set())),
        patch.object(service.state, "compute_fingerprint", return_value="now"),
        patch.object(service.repository, "latest_run_brief", return_value=None),
        patch.object(service.repository, "count_runs_since", return_value=0),
        patch.object(service.spend_cap, "reached", return_value=7.25),
    ):
        assert service._due_fingerprint("case-1") is None
