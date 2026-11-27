"""
Pub/Sub delivers at least once, so every handler that spends money or changes state must find a repeated event
already taken (the idempotency fixes of 2026-11-07): the detection worker claims the document, the case worker claims
the run, and an approval goes through only once. Each test delivers the same event twice.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app import document_repository, document_service
from app.case_pipeline import repository as analysis_repository
from app.case_pipeline.models import CaseAnalysis
from app.events import Event, types
from app.langgraph.detection import graph as detection_graph
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


# --- A recovery can't start a second detection run -----------------------------------------
# The stamp-guarded status writes (mark_detected, end_unfinished_detection) are tested in test_document_repository.py.



def _detection_recovery() -> Event:
    return Event(
        type=types.RECOVERY_REQUESTED, document_id="doc-1", case_id="case-1",
        detail={"stage": "detecting", types.OWNER_ATTRIBUTE: "detection-worker", "attempt": 1},
    )


def test_a_recovery_does_nothing_while_the_run_is_alive_in_this_process() -> None:
    with (
        patch.object(detection_worker._runs, "is_running", return_value=True),
        patch("app.document_repository.update_status") as update,
        patch.object(detection_worker, "run", AsyncMock()) as run,
    ):
        asyncio.run(detection_worker.on_recovery_requested(_detection_recovery()))

    update.assert_not_called()  # not put back to approved under the live run
    run.assert_not_awaited()


def test_the_same_recovery_delivered_twice_starts_one_run() -> None:
    status = {"doc-1": DocumentStatus.DETECTING}  # the stuck document, as the sweeper found it

    def update_status(document_id, new_status, **kwargs):
        expected = kwargs.get("expected_current_statuses")
        if expected is not None and status[document_id] not in expected:
            return False
        status[document_id] = new_status
        return True

    async def both() -> None:
        await asyncio.gather(
            detection_worker.on_recovery_requested(_detection_recovery()),
            detection_worker.on_recovery_requested(_detection_recovery()),
        )

    with (
        patch("app.document_repository.update_status", side_effect=update_status),
        patch.object(detection_worker.spend_cap, "reached", return_value=None),
        patch.object(detection_worker, "run_detection", AsyncMock(return_value={"verdict": "clean", "findings": []}))
        as run_detection,
        patch.object(detection_worker, "_record_end", AsyncMock()),
        patch.object(detection_worker, "push_case_snapshot", AsyncMock()),
        patch("app.workers.recovery.report", AsyncMock()),
        patch.object(detection_worker, "_report_recovery", AsyncMock()),
    ):
        asyncio.run(both())

    run_detection.assert_awaited_once_with("doc-1")


def test_mark_detecting_moves_only_an_approved_document(monkeypatch) -> None:
    db = FakeSupabase()
    monkeypatch.setattr(document_repository, "supabase_client", db)
    monkeypatch.setattr(document_repository, "_sync_case", lambda case_id: None)
    for document_id, current in (("approved", "approved"), ("cancelled", "cancelled"), ("detected", "detected")):
        db.add("documents", {"id": document_id, "case_id": "case-1", "status": current})

    for document_id in ("approved", "cancelled", "detected"):
        document_repository.mark_detecting(document_id)

    assert {r["id"]: r["status"] for r in db.rows("documents")} == {
        "approved": "detecting", "cancelled": "cancelled", "detected": "detected",
    }


class _FinishedGraph:
    """Stands in for the detection graph: the run has finished, with a report to save."""

    async def ainvoke(self, state, config=None):
        return {"result": {"verdict": "flagged", "findings": [{"id": "f1"}], "summary": "s"}, "case_id": "case-1"}


@pytest.mark.parametrize("still_held", [False, True], ids=["a recovery took the claim", "the run still holds it"])
async def test_a_run_saves_its_report_only_while_it_still_holds_the_document(still_held) -> None:
    stamp = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)
    token = detection_graph.current_run_stamp.set(stamp)
    try:
        with (
            patch.object(detection_graph, "get_detection_graph", return_value=_FinishedGraph()),
            patch.object(detection_graph.trace, "resume"),
            patch.object(detection_graph.trace, "flush"),
            patch.object(detection_graph.detection_results, "mark_started"),
            patch.object(detection_graph.detection_results, "save_report") as save_report,
            patch.object(detection_graph.document_repository, "mark_detecting"),
            patch.object(detection_graph.document_repository, "holds_detection", return_value=still_held) as holds,
            patch.object(detection_graph.document_repository, "mark_detected") as mark_detected,
            patch.object(detection_graph.findings_repository, "sync_document", return_value=1) as sync_findings,
        ):
            if still_held:
                await detection_graph.run_detection("doc-1")
            else:
                with pytest.raises(detection_graph.DetectionSuperseded):
                    await detection_graph.run_detection("doc-1")
    finally:
        detection_graph.current_run_stamp.reset(token)

    holds.assert_called_once_with("doc-1", stamp)
    if still_held:
        save_report.assert_called_once()
        sync_findings.assert_called_once()
        mark_detected.assert_called_once_with("doc-1", run_stamp=stamp)
    else:
        # The newer run's report, findings and the officer's verdicts on them stand.
        save_report.assert_not_called()
        sync_findings.assert_not_called()
        mark_detected.assert_not_called()


async def test_a_superseded_run_ends_as_cancelled() -> None:
    with (
        patch.object(detection_worker.spend_cap, "reached", return_value=None),
        patch.object(detection_worker, "run_detection", AsyncMock(side_effect=detection_graph.DetectionSuperseded("doc-1"))),
        patch.object(detection_worker, "_record_end", AsyncMock()) as record_end,
        patch.object(detection_worker, "push_case_snapshot", AsyncMock()),
    ):
        await detection_worker.run("doc-1", "case-1")

    outcome, detail = record_end.call_args.args[4], record_end.call_args.args[5]
    assert outcome == "cancelled" and "Replaced by a newer run" in detail["error"]


# --- a repeated approval after its run ended ----------------------------------------------
# A failed run puts the document back to approved, so a redelivery would find it claimable again: the run's end event,
# which names the event that started it (trigger_event_id), is what says it already ran.


async def test_an_approval_whose_run_already_ended_runs_nothing(published_events) -> None:
    documents = Documents(DocumentStatus.APPROVED)  # the first run failed and put it back
    event = approved_event()
    run = AsyncMock(return_value=CLEAN)
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", run),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
        patch.object(detection_worker.audit, "ran_for", return_value=True) as ran_for,
    ):
        await detection_worker.on_requested(event)

    trigger, actions, document_id, since = ran_for.call_args.args
    assert (trigger, document_id, since) == (event.event_id, "doc-1", event.occurred_at)
    assert set(actions) == {types.DOCUMENT_DETECTED, types.DETECTION_FAILED}
    run.assert_not_awaited()
    assert documents.moves == [] and published_events == []


async def test_a_first_delivery_runs_and_its_end_event_names_the_event(published_events) -> None:
    documents = Documents(DocumentStatus.APPROVED)
    event = approved_event()
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", AsyncMock(return_value=CLEAN)) as run,
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
        patch.object(detection_worker.audit, "ran_for", return_value=False),
    ):
        await detection_worker.on_requested(event)

    run.assert_awaited_once()
    (end,) = published_events
    assert end.type == types.DOCUMENT_DETECTED and end.detail["trigger_event_id"] == event.event_id


async def test_a_failed_runs_end_event_names_the_event_too(published_events) -> None:
    documents = Documents(DocumentStatus.APPROVED)
    event = approved_event()
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", AsyncMock(side_effect=RuntimeError("model down"))),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
        patch.object(detection_worker.audit, "ran_for", return_value=False),
    ):
        await detection_worker.on_requested(event)

    (end,) = published_events
    assert end.type == types.DETECTION_FAILED and end.detail["trigger_event_id"] == event.event_id


async def test_an_unreadable_trail_still_runs(published_events) -> None:
    # The status claim still stops a run already under way; refusing here would lose the approval.
    documents = Documents(DocumentStatus.APPROVED)
    with (
        patch("app.workers.detection.document_repository", documents),
        patch("app.workers.detection.run_detection", AsyncMock(return_value=CLEAN)) as run,
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
        patch.object(detection_worker.audit, "ran_for", side_effect=RuntimeError("database down")),
    ):
        await detection_worker.on_requested(approved_event())

    run.assert_awaited_once()


# --- "Cancel detection" ends a lost run -----------------------------------------------------
# A run that died mid-scan (a restart) leaves the document detecting with no run anywhere to hear the cancel. The
# handler ends it as the officer's cancel would, unless with Pub/Sub the run may still be alive on another instance.


def _cancel_event() -> Event:
    return Event(type=types.DETECTION_CANCEL_REQUESTED, document_id="doc-1", case_id="case-1")


def _detecting(status: DocumentStatus = DocumentStatus.DETECTING, claimed_minutes_ago: float = 1) -> DocumentRecord:
    return DocumentRecord(
        id="doc-1", case_id="case-1", filename="statement.pdf", status=status, uploaded_at=NOW,
        processing_started_at=datetime.now(timezone.utc) - timedelta(minutes=claimed_minutes_ago),
    )


async def _cancel(document: DocumentRecord, *, transport: str = "in_process", has_report: bool = False,
                  ended: bool = True):
    with (
        patch.object(detection_worker.settings, "EVENT_TRANSPORT", transport),
        patch.object(detection_worker.document_repository, "get_document", return_value=document),
        patch.object(detection_worker.document_repository, "end_unfinished_detection", return_value=ended) as end,
        patch.object(detection_worker.detection_results, "save_error") as save_error,
        patch.object(detection_worker.detection_results, "has_report", return_value=has_report),
        patch.object(detection_worker.langgraph_trace, "record"),
        patch.object(detection_worker.langgraph_trace, "flush"),
        patch.object(detection_worker, "push_case_snapshot", AsyncMock()) as snapshot,
    ):
        await detection_worker.on_cancel_requested(_cancel_event())
    return end, save_error, snapshot


@pytest.mark.parametrize("has_report", [False, True], ids=["first run: back to approved", "earlier report: detected"])
async def test_in_process_a_lost_run_is_ended_as_the_officers_cancel(has_report) -> None:
    document = _detecting()

    end, save_error, snapshot = await _cancel(document, has_report=has_report)

    save_error.assert_called_once_with("doc-1", "Cancelled by loan officer")
    # The repository moves it to approved, or to detected when an earlier report stands; only while the stamp holds.
    end.assert_called_once_with("doc-1", has_report=has_report, run_stamp=document.processing_started_at)
    snapshot.assert_awaited_once_with("case-1", realtime_covers=True)


async def test_with_pubsub_a_recent_claim_is_left_to_the_instance_running_it() -> None:
    end, save_error, snapshot = await _cancel(_detecting(claimed_minutes_ago=1), transport="pubsub")

    end.assert_not_called()
    save_error.assert_not_called()
    snapshot.assert_not_awaited()


async def test_with_pubsub_a_claim_older_than_a_live_run_can_be_is_ended() -> None:
    old = detection_worker.settings.STUCK_RUNNING_SECONDS / 60 + 1
    end, save_error, _ = await _cancel(_detecting(claimed_minutes_ago=old), transport="pubsub")

    end.assert_called_once()
    save_error.assert_called_once()


@pytest.mark.parametrize("status", [DocumentStatus.APPROVED, DocumentStatus.DETECTED])
async def test_a_document_no_longer_detecting_is_left_alone(status) -> None:
    end, save_error, snapshot = await _cancel(_detecting(status=status))

    end.assert_not_called()
    save_error.assert_not_called()
    snapshot.assert_not_awaited()


async def test_a_document_claimed_again_meanwhile_is_not_reported_ended() -> None:
    # A new run took it between the read and the write: its stamp no longer matches, so nothing moved.
    end, _, snapshot = await _cancel(_detecting(), ended=False)

    end.assert_called_once()
    snapshot.assert_not_awaited()


async def test_a_run_in_this_process_is_cancelled_not_ended_from_outside() -> None:
    with (
        patch.object(detection_worker._runs, "is_running", return_value=True),
        patch.object(detection_worker._runs, "cancel") as cancel,
        patch.object(detection_worker.document_repository, "end_unfinished_detection") as end,
    ):
        await detection_worker.on_cancel_requested(_cancel_event())

    cancel.assert_called_once_with("doc-1")
    end.assert_not_called()
