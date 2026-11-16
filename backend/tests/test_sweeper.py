"""Stuck work and recovery (CLOUD_ARCHITECTURE_PLAN.md section 5): the sweeper, the owners, routing, cancels."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app import auth, detection_results, sweeper
from app.config import settings
from app.events import Event, types
from app.models import DocumentRecord, DocumentStatus
from app.workers import cases as case_worker
from app.workers import detection as detection_worker
from app.workers import documents as doc_worker
from tests.support.fake_supabase import FakeSupabase

# The real clock: sweep() reads it itself.
NOW = datetime.now(timezone.utc)


def _doc(doc_id: str, status: DocumentStatus, minutes_ago: int) -> DocumentRecord:
    at = NOW - timedelta(minutes=minutes_ago)
    return DocumentRecord(
        id=doc_id, case_id="case-1", filename=f"{doc_id}.pdf", status=status,
        uploaded_at=at, queued_at=at, processing_started_at=at if status != DocumentStatus.QUEUED else None,
    )


def _recovery(owner: str, stage: str, attempt: int, **detail) -> Event:
    return Event(
        type=types.RECOVERY_REQUESTED, document_id="doc-1", case_id="case-1",
        detail={"stage": stage, types.OWNER_ATTRIBUTE: owner, "attempt": attempt, **detail},
    )


def test_find_stuck_uses_each_stage_limit_and_the_detection_start():
    documents = [
        _doc("queued-old", DocumentStatus.QUEUED, 10),       # past 5 min: its event was lost
        _doc("queued-new", DocumentStatus.QUEUED, 2),
        _doc("processing-old", DocumentStatus.PROCESSING, 20),  # past 10 min: its run died
        _doc("processing-new", DocumentStatus.PROCESSING, 7),
        _doc("detecting-old", DocumentStatus.DETECTING, 1),  # the row's own times are old news for detection
        _doc("detecting-new", DocumentStatus.DETECTING, 60),
    ]
    detection_started = {"detecting-old": NOW - timedelta(minutes=20), "detecting-new": NOW - timedelta(minutes=3)}
    with (
        patch("app.document_repository.get_documents_in_status", return_value=documents),
        patch("app.audit.latest_times", side_effect=lambda actions, doc_ids: {d: detection_started[d] for d in doc_ids}),
        patch("app.case_pipeline.repository.running_started_before",
              return_value=[("run-1", "case-1", NOW - timedelta(minutes=30))]),
    ):
        stuck = sweeper.find_stuck(NOW)
    found = {(s.stage, s.document_id or s.run_id, s.owner) for s in stuck}
    assert found == {
        ("queued", "queued-old", "doc-worker"),
        ("processing", "processing-old", "doc-worker"),
        ("detecting", "detecting-old", "detection-worker"),
        ("case_analysis", "run-1", "case-worker"),
    }


def test_sweep_counts_attempts_and_waits_a_full_period_between_them():
    # Its own clock, not the module's NOW: sweep() reads the real time, and a long test run ages NOW past the limit.
    now = datetime.now(timezone.utc)
    item = sweeper.Stuck("processing", "doc-worker", now - timedelta(minutes=40), 900, "case-1", document_id="doc-1")
    recent = sweeper.Stuck("queued", "doc-worker", now - timedelta(minutes=40), 300, "case-1", document_id="doc-2")
    earlier = {
        "doc-1": [Event(type=types.RECOVERY_REQUESTED, occurred_at=now - timedelta(minutes=20)),
                  Event(type=types.RECOVERY_REQUESTED, occurred_at=now - timedelta(minutes=38))],
        "doc-2": [Event(type=types.RECOVERY_REQUESTED, occurred_at=now - timedelta(minutes=1))],
    }
    publish = AsyncMock()
    with (
        patch("app.sweeper.find_stuck", return_value=[item, recent]),
        patch("app.sweeper._earlier_attempts", side_effect=lambda items, now: {s: earlier[s.document_id] for s in items}),
        patch("app.sweeper.events.publish", publish),
    ):
        count = asyncio.run(sweeper.sweep())
    assert count == 1  # doc-2's last request is a minute old: still in hand
    event = publish.await_args.args[0]
    assert event.type == types.RECOVERY_REQUESTED and event.document_id == "doc-1"
    assert event.detail["attempt"] == 3 and event.detail[types.OWNER_ATTRIBUTE] == "doc-worker"


def test_sweep_requested_is_ignored_unless_enabled():
    with patch("app.sweeper.settings.SWEEPER_ENABLED", False), patch("app.sweeper.sweep", AsyncMock()) as sweep:
        asyncio.run(sweeper.on_sweep_requested(Event(type=types.SWEEP_REQUESTED)))
    sweep.assert_not_awaited()


def test_a_worker_ignores_another_workers_recovery():
    with patch("app.document_repository.update_status") as update:
        asyncio.run(doc_worker.on_recovery_requested(_recovery("detection-worker", "detecting", 1)))
    update.assert_not_called()


def test_doc_worker_requeues_and_reruns_a_stuck_document():
    with (
        patch("app.document_repository.update_status", return_value=True) as update,
        patch("app.workers.documents._run", AsyncMock()) as run,
        patch("app.document_repository.get_document", return_value=_doc("doc-1", DocumentStatus.ANALYZED, 0)),
        patch("app.workers.recovery.report", AsyncMock()) as report,
    ):
        asyncio.run(doc_worker.on_recovery_requested(_recovery("doc-worker", "processing", 1)))
    args, kwargs = update.call_args
    assert args[1] == DocumentStatus.QUEUED and kwargs["expected_current_statuses"] == [DocumentStatus.PROCESSING]
    run.assert_awaited_once()
    assert report.await_args.args[2:] == (types.DOCUMENT_RECOVERED, "success")


def test_doc_worker_fails_the_document_past_the_attempt_limit():
    with (
        patch("app.document_repository.update_status", return_value=True) as update,
        patch("app.workers.documents._run", AsyncMock()) as run,
        patch("app.workers.recovery.report", AsyncMock()) as report,
    ):
        asyncio.run(doc_worker.on_recovery_requested(_recovery("doc-worker", "queued", 4)))
    assert update.call_args.args[1] == DocumentStatus.FAILED
    run.assert_not_awaited()
    assert report.await_args.args[2] == types.RECOVERY_EXHAUSTED


def test_detection_worker_does_nothing_when_the_run_already_moved_on():
    with (
        patch("app.document_repository.update_status", return_value=False),
        patch("app.workers.detection.run", AsyncMock()) as run,
    ):
        asyncio.run(detection_worker.on_recovery_requested(_recovery("detection-worker", "detecting", 1)))
    run.assert_not_awaited()


def test_detection_worker_gives_up_past_the_attempt_limit_keeping_the_earlier_report(monkeypatch):
    # The document had a report from an earlier run; the rerun the sweeper found stuck never finished.
    results = FakeSupabase(keys={"detection_results": ("document_id",)})
    monkeypatch.setattr(detection_results, "service_supabase_client", lambda: results)
    detection_results.save_report("doc-1", {"verdict": "flagged", "summary": "Earlier run.", "findings": []})
    with (
        patch("app.document_repository.update_status", return_value=True) as update,
        patch("app.document_repository.end_unfinished_detection") as end_run,
        patch("app.workers.detection.run", AsyncMock()) as run,
        patch("app.workers.detection.langgraph_trace.flush"),
        patch("app.workers.recovery.report", AsyncMock()) as report,
        patch("app.workers.detection.push_case_snapshot", AsyncMock()) as snapshot,
    ):
        asyncio.run(detection_worker.on_recovery_requested(
            _recovery("detection-worker", "detecting", settings.RECOVERY_MAX_ATTEMPTS + 1)))

    args, kwargs = update.call_args
    assert args[1] == DocumentStatus.APPROVED
    assert kwargs["expected_current_statuses"] == [DocumentStatus.DETECTING]
    run.assert_not_awaited()
    report_kept, error = detection_results.get("doc-1")
    assert report_kept["summary"] == "Earlier run."
    assert error == f"Detection stopped responding; automatic recovery gave up after {settings.RECOVERY_MAX_ATTEMPTS} attempts."
    end_run.assert_called_once_with("doc-1", has_report=True)
    assert report.await_args.args[2:] == (types.RECOVERY_EXHAUSTED, "failed")
    snapshot.assert_awaited_once_with("case-1", realtime_covers=True)


def test_case_worker_marks_a_stuck_run_failed_without_rerunning_it():
    event = _recovery("case-worker", "case_analysis", 1, run_id="run-1")
    with (
        patch("app.case_pipeline.repository.fail_if_running", return_value=True) as fail,
        patch("app.workers.recovery.report", AsyncMock()) as report,
        patch("app.case_pipeline.service.run_requested", AsyncMock()) as rerun,
    ):
        asyncio.run(case_worker.on_recovery_requested(event))
    assert fail.call_args.args[0] == "run-1"
    assert report.await_args.args[2] == types.RECOVERY_EXHAUSTED
    rerun.assert_not_awaited()


def test_recovery_requests_reach_only_their_owner():
    doc_filter = types.subscription_filter(types.SUBSCRIPTIONS["doc-worker"], "doc-worker")
    assert 'attributes.owner = "doc-worker"' in doc_filter
    for role, subscribed in types.SUBSCRIPTIONS.items():
        assert len(types.subscription_filter(subscribed, role).encode()) <= types.MAX_FILTER_BYTES


def test_cancel_watch_stops_a_run_when_a_cancel_appears():
    async def scenario() -> tuple[bool, list[str]]:
        noted: list[str] = []
        checks = iter([False, True])
        work = asyncio.create_task(asyncio.sleep(10))
        with (
            patch("app.workers.recovery.settings.EVENT_TRANSPORT", "pubsub"),
            patch("app.workers.recovery.settings.CANCEL_POLL_SECONDS", 0.01),
        ):
            await doc_worker.recovery.cancel_when(work, AsyncMock(side_effect=lambda: next(checks)), lambda: noted.append("x"))
        await asyncio.gather(work, return_exceptions=True)
        return work.cancelled(), noted

    cancelled, noted = asyncio.run(scenario())
    assert cancelled and noted == ["x"]


def test_ws_ticket_is_signed_for_its_own_purpose():
    ticket = auth.create_ws_ticket()
    payload, _ = ticket.split(".", 1)
    assert not auth.consume_ws_ticket(f"{payload}.forged")
    assert not auth.consume_ws_ticket(auth.create_token("alice"))  # a login token is not a ticket
    assert auth._verify_token(ticket) is None  # nor a ticket a login token
    assert auth.consume_ws_ticket(ticket)
    assert not auth.consume_ws_ticket(ticket)
