"""The case analysis starts on its own once every document is decided and every approved one is scanned."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from postgrest.exceptions import APIError

from app.case_pipeline import repository, service
from app.case_pipeline.service import start_if_due as real_start_if_due  # bound before conftest's no-op
from app.config import settings
from app.events import types
from app.models import CaseRecord, DocumentRecord, DocumentStatus
from tests.support.fake_supabase import FakeSupabase, Row

NOW = datetime.now(timezone.utc)


def _case(status: str = "fraud_detection") -> CaseRecord:
    return CaseRecord(id="case-1", greenlink_case_id="GLDB-1", status=status, created_at=NOW, updated_at=NOW)


def _document(status: DocumentStatus, doc_id: str = "doc-1") -> DocumentRecord:
    # Typed: a document with no type is never scanned, so it isn't waited on.
    return DocumentRecord(id=doc_id, case_id="case-1", filename=f"{doc_id}.pdf", document_type="payslip", status=status, uploaded_at=NOW)


def _due(
    documents: list[DocumentRecord], latest: tuple[str, datetime, str] | None = None, *, runs_this_hour: int = 0,
    case: CaseRecord | None = None,
) -> str | None:
    with (
        patch.object(service.case_repository, "get_case", return_value=case or _case()),
        patch.object(service.document_repository, "list_documents", return_value=documents),
        patch.object(service.detection_results, "get_states", return_value=({}, set())),
        patch.object(service.state, "compute_fingerprint", return_value="now"),
        patch.object(service.repository, "latest_run_brief", return_value=latest),
        patch.object(service.repository, "count_runs_since", return_value=runs_this_hour),
    ):
        return service._due_fingerprint("case-1")


SCANNED = [_document(DocumentStatus.DETECTED), _document(DocumentStatus.REJECTED, "doc-2")]


@pytest.mark.parametrize(
    "documents",
    [
        [_document(DocumentStatus.DETECTED), _document(DocumentStatus.ANALYZED, "doc-2")],
        [_document(DocumentStatus.DETECTED), _document(DocumentStatus.DETECTING, "doc-2")],
        [_document(DocumentStatus.DETECTED), _document(DocumentStatus.PROCESSING, "doc-2")],
        [_document(DocumentStatus.REJECTED)],
    ],
    ids=["one waiting for the officer", "one still scanning", "one still processing", "none approved"],
)
def test_nothing_starts_until_every_document_is_decided_and_scanned(documents) -> None:
    assert _due(documents) is None


def test_the_last_document_scanned_makes_the_analysis_due() -> None:
    assert _due(SCANNED) == "now"


@pytest.mark.parametrize(
    ("latest", "expected"),
    [
        (("complete", NOW, "now"), None),
        (("failed", NOW, "now"), None),
        (("running", NOW, "now"), None),
        (("complete", NOW, "before"), "now"),
        (("failed", NOW, "before"), "now"),
        (("running", NOW, "before"), "now"),
        (("running", NOW - timedelta(hours=2), "now"), "now"),
    ],
    ids=[
        "a complete run over these inputs", "a failed run over these inputs (the officer reruns it)",
        "a run over these inputs still going", "the documents changed since a complete run",
        "the documents changed since a failed run", "the documents changed while a run is going (it is replaced)",
        "a run over these inputs that was lost",
    ],
)
def test_a_run_starts_only_when_the_newest_does_not_cover_these_inputs(latest, expected) -> None:
    assert _due(SCANNED, latest) == expected


def test_runs_per_hour_are_capped() -> None:
    assert _due(SCANNED, runs_this_hour=settings.CASE_ANALYSIS_AUTO_RUNS_PER_HOUR) is None


def test_a_finalised_case_is_left_alone() -> None:
    assert _due(SCANNED, case=_case("finalised")) is None


async def test_a_due_analysis_is_started_by_the_system(published_events) -> None:
    with (
        patch.object(service, "_due_fingerprint", return_value="now"),
        patch.object(service.repository, "insert_run") as insert,
        patch.object(service, "sync_stage", AsyncMock()),
    ):
        await real_start_if_due("case-1")

    insert.assert_called_once()
    (event,) = published_events
    assert event.type == types.CASE_ANALYSIS_REQUESTED
    assert (event.actor_type, event.case_id) == ("system", "case-1")
    assert event.detail["trigger"] == "automatic" and event.detail["fingerprint"] == "now"


async def test_nothing_is_started_when_none_is_due(published_events) -> None:
    with (
        patch.object(service, "_due_fingerprint", return_value=None),
        patch.object(service.repository, "insert_run") as insert,
    ):
        await real_start_if_due("case-1")

    insert.assert_not_called()
    assert published_events == []


async def test_a_failure_to_start_never_reaches_the_caller() -> None:
    with patch.object(service, "_due_fingerprint", side_effect=RuntimeError("database down")):
        await real_start_if_due("case-1")


async def test_the_kill_switch_stops_automatic_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "CASE_ANALYSIS_ENABLED", False)
    with patch.object(service, "_due_fingerprint") as due:
        await real_start_if_due("case-1")

    due.assert_not_called()


async def test_the_end_of_a_detection_run_checks_whether_the_analysis_is_due() -> None:
    from app.workers import detection as detection_worker

    with (
        patch("app.workers.detection.run_detection", AsyncMock(return_value={"verdict": "clean", "findings": []})),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
        patch.object(service, "start_if_due", AsyncMock()) as start,
    ):
        await detection_worker.run("doc-1", "case-1")

    start.assert_awaited_once_with("case-1")


async def test_rejecting_a_document_checks_whether_the_analysis_is_due() -> None:
    from app import document_service

    document = _document(DocumentStatus.ANALYZED)
    with (
        patch.object(document_service.document_repository, "reject_document", return_value=document),
        patch.object(document_service.extracted_fields_repository, "get_extracted_fields", return_value=[]),
        patch.object(document_service, "_audit", AsyncMock()),
        patch.object(service, "start_if_due", AsyncMock()) as start,
    ):
        await document_service.reject(document)

    start.assert_awaited_once_with("case-1")


# --- A case analysis can't start twice (PENDING_TESTS rows 1 and 19) ----------------------------------------------
# The database holds at most one running run per case and fingerprint (case_analysis_runs_one_running_idx in
# sql/schema.sql); a second insert fails with Postgres's unique-violation code. The fake below enforces that same
# rule, so these tests go through the real repository.insert_run, not a mock of it.

RUNS = "case_analysis_runs"


class _OneRunningRunPerCase(FakeSupabase):
    def add(self, table: str, values: Row) -> Row:
        if table == RUNS and values.get("status") == "running" and self.rows(
            RUNS, case_id=values.get("case_id"), fingerprint=values.get("fingerprint"), status="running",
        ):
            raise APIError({"code": "23505", "message": "duplicate key value violates unique constraint "
                            '"case_analysis_runs_one_running_idx"', "details": None, "hint": None})
        return super().add(table, values)


@pytest.fixture
def runs_db(monkeypatch: pytest.MonkeyPatch) -> _OneRunningRunPerCase:
    fake = _OneRunningRunPerCase(keys={RUNS: ("run_id",)})
    monkeypatch.setattr(repository, "supabase_client", fake)
    return fake


def _running(db: FakeSupabase, run_id: str, *, started: datetime, fingerprint: str = "now") -> None:
    db.add(RUNS, {"run_id": run_id, "case_id": "case-1", "status": "running", "fingerprint": fingerprint,
                  "started_at": started.isoformat()})


def test_a_second_running_run_over_the_same_inputs_is_refused(runs_db) -> None:
    _running(runs_db, "first", started=datetime.now(timezone.utc))

    with pytest.raises(repository.RunAlreadyStarted):
        repository.insert_run("second", "case-1", "now")

    assert [r["run_id"] for r in runs_db.rows(RUNS)] == ["first"]
    assert runs_db.rows(RUNS, run_id="first")[0]["status"] == "running"


def test_a_clashing_run_past_the_time_limit_is_failed_and_the_new_one_starts(runs_db) -> None:
    lost_at = datetime.now(timezone.utc) - timedelta(seconds=settings.CASE_ANALYSIS_TIMEOUT_SECONDS + 60)
    _running(runs_db, "lost", started=lost_at)

    repository.insert_run("fresh", "case-1", "now")

    (lost,) = runs_db.rows(RUNS, run_id="lost")
    assert lost["status"] == "failed" and lost["finished_at"]
    assert "stopped without finishing" in lost["error"]
    assert runs_db.rows(RUNS, run_id="fresh")[0]["status"] == "running"


def test_a_running_run_over_other_inputs_does_not_block_a_new_one(runs_db) -> None:
    _running(runs_db, "older-inputs", started=datetime.now(timezone.utc), fingerprint="before")

    repository.insert_run("new-inputs", "case-1", "now")

    assert {r["run_id"] for r in runs_db.rows(RUNS, status="running")} == {"older-inputs", "new-inputs"}


async def test_an_automatic_start_that_clashes_sends_nothing_and_does_not_raise(runs_db, published_events) -> None:
    # Another instance inserted the run between this one's due check and its insert.
    _running(runs_db, "other-instance", started=datetime.now(timezone.utc))
    with patch.object(service, "_due_fingerprint", return_value="now"), patch.object(service, "sync_stage", AsyncMock()):
        await real_start_if_due("case-1")

    assert published_events == []
    assert [r["run_id"] for r in runs_db.rows(RUNS)] == ["other-instance"]


async def test_an_officers_start_that_clashes_is_told_one_is_already_running(runs_db, published_events) -> None:
    # The pre-check saw no live run, but another instance's insert won the race: the database still refuses it.
    _running(runs_db, "other-instance", started=datetime.now(timezone.utc))
    with (
        patch.object(service.case_repository, "get_case", return_value=_case()),
        patch.object(service.document_repository, "list_documents", return_value=SCANNED),
        patch.object(service, "unapproved_requirements", return_value=[]),
        patch.object(service, "is_ready_for_analysis", return_value=True),
        patch.object(service.repository, "latest_run_state", return_value=None),
        patch.object(service.repository, "count_runs_since", return_value=0),
        patch.object(service.spend_cap, "reached", return_value=None),
        patch.object(service.detection_results, "get_states", return_value=({}, set())),
        patch.object(service.state, "compute_fingerprint", return_value="now"),
        pytest.raises(service.AnalysisAlreadyRunning),
    ):
        await service.start_analysis("case-1")

    assert published_events == []


async def test_two_automatic_starts_at_once_start_one_run(runs_db, published_events) -> None:
    with patch.object(service, "_due_fingerprint", return_value="now"), patch.object(service, "sync_stage", AsyncMock()):
        await asyncio.gather(real_start_if_due("case-1"), real_start_if_due("case-1"))

    assert len(runs_db.rows(RUNS, status="running")) == 1
    assert [e.type for e in published_events] == [types.CASE_ANALYSIS_REQUESTED]


async def test_the_case_lock_is_dropped_after_a_start_and_after_a_failure(runs_db) -> None:
    with patch.object(service, "_due_fingerprint", return_value="now"), patch.object(service, "sync_stage", AsyncMock()):
        await real_start_if_due("case-1")
    assert "case-1" not in service._auto_locks and "case-1" not in service._auto_users

    with patch.object(service, "_due_fingerprint", side_effect=RuntimeError("database down")):
        await real_start_if_due("case-1")
    assert "case-1" not in service._auto_locks and "case-1" not in service._auto_users
