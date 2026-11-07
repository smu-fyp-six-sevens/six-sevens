"""
Pub/Sub delivers at least once, so every handler that spends money or changes state must find a repeated event
already taken (the idempotency fixes of 2026-11-07): the detection worker claims the document, the case worker claims
the run, and an approval goes through only once. Each test delivers the same event twice.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app import document_repository, document_service
from app.case_pipeline import repository as analysis_repository
from app.case_pipeline.models import CaseAnalysis
from app.events import Event, types
from app.models import DocumentRecord, DocumentStatus, RoutingStatus
from app.workers import cases as case_worker
from app.workers import detection as detection_worker
from tests.support.fake_supabase import FakeSupabase

NOW = datetime(2026, 11, 7, 4, 0, tzinfo=timezone.utc)
CLEAN = {"verdict": "clean", "findings": []}


class Documents:
    """document_repository.update_status as the database does it: moves a document only from a status it expects."""

    def __init__(self, status: DocumentStatus) -> None:
        self.status = status
        self.moves: list[tuple[DocumentStatus, list[DocumentStatus] | None]] = []

    def update_status(self, document_id, status, *, expected_current_statuses=None, **_) -> bool:
        self.moves.append((status, expected_current_statuses))
        if expected_current_statuses is not None and self.status not in expected_current_statuses:
            return False
        self.status = status
        return True


def approved_event(event_type: str = types.DOCUMENT_APPROVED) -> Event:
    return Event(type=event_type, document_id="doc-1", case_id="case-1")


# --- the detection worker ---------------------------------------------------------------------------------------


async def test_an_approval_delivered_twice_runs_detection_once(published_events) -> None:
    documents = Documents(DocumentStatus.APPROVED)
    run = AsyncMock(return_value=CLEAN)
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", run),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
    ):
        await detection_worker.on_requested(approved_event())
        await detection_worker.on_requested(approved_event())  # the redelivery: the document is already detecting

    assert run.await_count == 1
    assert [(e.type, e.outcome) for e in published_events] == [(types.DOCUMENT_DETECTED, "success")]


async def test_a_repeat_that_finds_the_document_taken_changes_nothing(published_events) -> None:
    documents = Documents(DocumentStatus.DETECTED)  # the first delivery ran and finished
    run, push = AsyncMock(), AsyncMock()
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", run),
        patch("app.workers.detection.push_case_snapshot", push),
    ):
        await detection_worker.on_requested(approved_event())

    run.assert_not_awaited()
    push.assert_not_awaited()
    assert published_events == []  # no end event either: the first run's stands in the audit trail
    assert documents.status == DocumentStatus.DETECTED  # the finished report is not set back to detecting


async def test_an_approval_claims_the_document_from_approved_only() -> None:
    documents = Documents(DocumentStatus.APPROVED)
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", AsyncMock(return_value=CLEAN)),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
    ):
        await detection_worker.on_requested(approved_event())

    assert documents.moves == [(DocumentStatus.DETECTING, [DocumentStatus.APPROVED])]


async def test_an_officers_retry_may_also_replace_a_finished_report(published_events) -> None:
    documents = Documents(DocumentStatus.DETECTED)
    run = AsyncMock(return_value=CLEAN)
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", run),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
    ):
        await detection_worker.on_requested(approved_event(types.DETECTION_RETRY_REQUESTED))

    assert documents.moves == [(DocumentStatus.DETECTING, [DocumentStatus.APPROVED, DocumentStatus.DETECTED])]
    run.assert_awaited_once()


async def test_a_repeat_while_the_run_is_still_going_here_does_not_start_another(published_events) -> None:
    documents = Documents(DocumentStatus.APPROVED)
    started, release = asyncio.Event(), asyncio.Event()

    async def slow(_: str) -> dict:
        started.set()
        await release.wait()
        return CLEAN

    run = AsyncMock(side_effect=slow)
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", run),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
    ):
        first = asyncio.create_task(detection_worker.on_requested(approved_event()))
        await asyncio.wait_for(started.wait(), timeout=2)
        await detection_worker.on_requested(approved_event())  # redelivered before the first finished
        release.set()
        await asyncio.wait_for(first, timeout=2)

    assert run.await_count == 1
    assert len(documents.moves) == 1  # the repeat never reached the database
    assert [e.type for e in published_events] == [types.DOCUMENT_DETECTED]


async def test_the_recovery_rerun_does_not_need_a_claim() -> None:
    # on_recovery_requested has already moved the document back to approved, atomically, before it runs.
    documents = Documents(DocumentStatus.APPROVED)
    run = AsyncMock(return_value=CLEAN)
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", run),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
    ):
        await detection_worker.run("doc-1", "case-1")

    run.assert_awaited_once()
    assert documents.moves == []


# --- the case worker ----------------------------------------------------------------------------------------------


def analysis_event() -> Event:
    return Event(
        type=types.CASE_ANALYSIS_REQUESTED, case_id="case-1",
        detail={"run_id": "run-1", "fingerprint": "f", "started_at": NOW.isoformat()},
    )


@pytest.fixture
def runs(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase(keys={"case_analysis_runs": ("run_id",)})
    monkeypatch.setattr(analysis_repository, "supabase_client", fake)
    fake.add("case_analysis_runs", {
        "run_id": "run-1", "case_id": "case-1", "status": "running", "fingerprint": "f", "started_at": NOW.isoformat(),
    })
    return fake


async def test_an_analysis_request_delivered_twice_runs_once(runs, published_events) -> None:
    done = CaseAnalysis(run_id="run-1", case_id="case-1", status="complete", fingerprint="f", started_at=NOW)
    run = AsyncMock(return_value=done)
    with patch.object(case_worker.analysis_service, "run_requested", run):
        await case_worker.on_analysis_requested(analysis_event())
        await case_worker.on_analysis_requested(analysis_event())

    assert run.await_count == 1  # the second delivery found the run taken, so no Claude call was made for it
    assert [e.type for e in published_events] == [types.CASE_ANALYSIS_COMPLETED]


async def test_a_request_for_a_run_that_already_ended_is_ignored(runs, published_events) -> None:
    runs.rows("case_analysis_runs", run_id="run-1")[0]["status"] = "complete"
    run = AsyncMock()
    with patch.object(case_worker.analysis_service, "run_requested", run):
        await case_worker.on_analysis_requested(analysis_event())

    run.assert_not_awaited()
    assert published_events == []


def test_a_run_can_be_claimed_once(runs) -> None:
    assert analysis_repository.claim_run("run-1") is True
    assert analysis_repository.claim_run("run-1") is False
    [row] = runs.rows("case_analysis_runs", run_id="run-1")
    assert row["worker_started_at"] is not None and row["status"] == "running"


def test_a_missing_or_ended_run_cannot_be_claimed(runs) -> None:
    runs.add("case_analysis_runs", {"run_id": "run-2", "case_id": "case-1", "status": "failed", "fingerprint": "f"})

    assert analysis_repository.claim_run("run-2") is False
    assert analysis_repository.claim_run("no-such-run") is False


# --- approval -------------------------------------------------------------------------------------------------------


def document_row(status: str) -> dict:
    return {
        "id": "doc-1", "case_id": "case-1", "filename": "statement.pdf", "status": status,
        "uploaded_at": NOW.isoformat(),
    }


@pytest.fixture
def documents_table(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase()
    monkeypatch.setattr(document_repository, "supabase_client", fake)
    monkeypatch.setattr(document_repository, "_sync_case", lambda case_id: None)
    return fake


def test_two_approvals_at_once_approve_a_document_once(documents_table) -> None:
    documents_table.add("documents", document_row("analyzed"))

    first = document_repository.approve_document("doc-1")
    second = document_repository.approve_document("doc-1")  # the double click, or a second officer

    assert first is not None and first.status == DocumentStatus.APPROVED
    assert first.routing_status == RoutingStatus.ACCEPTED
    assert second is None


def test_a_document_that_moved_on_is_not_approved_again(documents_table) -> None:
    documents_table.add("documents", document_row("rejected"))

    assert document_repository.approve_document("doc-1") is None
    assert documents_table.rows("documents", id="doc-1")[0]["status"] == "rejected"


async def test_the_second_of_two_approvals_is_refused_and_publishes_nothing(published_events) -> None:
    document = DocumentRecord(
        id="doc-1", case_id="case-1", filename="statement.pdf", status=DocumentStatus.ANALYZED, uploaded_at=NOW,
        document_type="payslip",
    )
    with (
        patch.object(document_service, "is_classification_confirmed", return_value=True),
        patch.object(document_service.extracted_fields_repository, "get_extracted_fields", return_value=[]),
        patch.object(document_service.case_service, "has_extracted_information", return_value=True),
        patch.object(document_service.case_service, "missing_required_fields", return_value=[]),
        patch.object(document_service.document_repository, "approve_document", return_value=None),  # lost the race
    ):
        with pytest.raises(document_service.Refused) as refused:
            await document_service.approve(document)

    assert refused.value.kind == "conflict"
    assert published_events == []  # no second detection run starts
