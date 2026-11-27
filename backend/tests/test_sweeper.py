"""Stuck work and recovery (HOW_IT_WORKS.md §8, "Stuck work"): the sweeper, the owners, routing, cancels."""

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app import auth, detection_results, monitoring, sweeper
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


# --- A lost approval event is recovered ------------------------------------------------------


def _approved(doc_id: str, document_type: str | None = "payslip") -> DocumentRecord:
    return _doc(doc_id, DocumentStatus.APPROVED, 60).model_copy(update={"document_type": document_type})


def test_an_approved_document_whose_detection_never_started_is_stuck():
    documents = [
        _approved("lost"),                      # approved long ago: no report, no error
        _approved("has-error"),                 # a failed run keeps its error; the officer retries it
        _approved("has-report"),                # detection ran (a retry may be waiting, not lost)
        _approved("untyped", document_type=None),  # detection never runs on it
        _approved("recent"),                    # approved a minute ago, still in the queue behind the slots
    ]
    over = timedelta(seconds=settings.STUCK_RUNNING_SECONDS + 60)
    approved_at = {"lost": NOW - over, "has-error": NOW - over, "has-report": NOW - over,
                   "untyped": NOW - over, "recent": NOW - timedelta(minutes=1)}
    with (
        patch("app.document_repository.get_documents_in_status", return_value=documents),
        patch("app.detection_results.get_states", return_value=({"has-report": {}}, {"has-error"})) as states,
        patch("app.audit.latest_times",
              side_effect=lambda actions, ids: {d: approved_at[d] for d in ids}) as latest,
        patch("app.case_pipeline.repository.running_started_before", return_value=[]),
    ):
        stuck = sweeper.find_stuck(NOW)

    assert [(s.stage, s.document_id, s.owner) for s in stuck] == [("approved", "lost", "detection-worker")]
    assert set(states.call_args.args[0]) == {"lost", "has-error", "has-report", "recent"}  # not the untyped one
    actions, _ = latest.call_args.args
    assert types.DOCUMENT_APPROVED in actions  # timed from the approval, not the upload


def test_the_detection_worker_runs_detection_for_a_lost_approval():
    with (
        patch("app.workers.detection.run", AsyncMock()) as run,
        patch("app.workers.detection._report_recovery", AsyncMock()) as reported,
        patch("app.workers.detection.detection_results.save_error") as save_error,
    ):
        asyncio.run(detection_worker.on_recovery_requested(_recovery("detection-worker", "approved", 1)))

    # Claimed from approved like the original would have: a late original then finds it taken.
    run.assert_awaited_once_with("doc-1", "case-1", start_from=[DocumentStatus.APPROVED])
    reported.assert_awaited_once()
    save_error.assert_not_called()


def test_past_the_attempt_limit_a_lost_approval_gets_an_error_the_officer_can_retry_from():
    with (
        patch("app.workers.detection.run", AsyncMock()) as run,
        patch("app.workers.detection.detection_results.has_report", return_value=False),
        patch("app.workers.detection.detection_results.save_error") as save_error,
        patch("app.workers.recovery.report", AsyncMock()) as report,
        patch("app.workers.detection.push_case_snapshot", AsyncMock()) as snapshot,
    ):
        asyncio.run(detection_worker.on_recovery_requested(
            _recovery("detection-worker", "approved", settings.RECOVERY_MAX_ATTEMPTS + 1)))

    run.assert_not_awaited()
    document_id, error = save_error.call_args.args
    assert document_id == "doc-1" and error.startswith("Detection never started")
    assert report.await_args.args[2:] == (types.RECOVERY_EXHAUSTED, "failed")
    snapshot.assert_awaited_once()


def test_past_the_attempt_limit_a_document_that_got_a_report_meanwhile_is_left_alone():
    with (
        patch("app.workers.detection.run", AsyncMock()) as run,
        patch("app.workers.detection.detection_results.has_report", return_value=True),
        patch("app.workers.detection.detection_results.save_error") as save_error,
        patch("app.workers.recovery.report", AsyncMock()) as report,
    ):
        asyncio.run(detection_worker.on_recovery_requested(
            _recovery("detection-worker", "approved", settings.RECOVERY_MAX_ATTEMPTS + 1)))

    run.assert_not_awaited()
    save_error.assert_not_called()
    report.assert_not_awaited()


# --- One "stuck" limit ------------------------------------------------------------------
# monitoring.is_stuck (the case queue and an officer's retry) and sweeper.find_stuck (the recovery) must agree.


WAITING, RUNNING = settings.STUCK_WAITING_SECONDS, settings.STUCK_RUNNING_SECONDS


def _aged(status: DocumentStatus, seconds: float) -> DocumentRecord:
    at = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    return DocumentRecord(id="doc-1", case_id="case-1", filename="f.pdf", status=status, uploaded_at=at,
                          queued_at=at, processing_started_at=at if status not in (
                              DocumentStatus.UPLOADED, DocumentStatus.QUEUED) else None)


def test_the_running_limit_is_ten_minutes_by_default() -> None:
    assert (WAITING, RUNNING) == (300, 600)


@pytest.mark.parametrize("status, limit", [
    (DocumentStatus.UPLOADED, WAITING), (DocumentStatus.QUEUED, WAITING),
    (DocumentStatus.PROCESSING, RUNNING), (DocumentStatus.EXTRACTING, RUNNING),
])
def test_a_document_is_stuck_only_past_its_stages_limit(status, limit) -> None:
    assert not monitoring.is_stuck(_aged(status, limit - 30))
    assert monitoring.is_stuck(_aged(status, limit + 30))


@pytest.mark.parametrize("status", [DocumentStatus.APPROVED, DocumentStatus.DETECTED, DocumentStatus.ANALYZED,
                                    DocumentStatus.FAILED, DocumentStatus.REJECTED, DocumentStatus.CANCELLED])
def test_a_document_in_any_other_status_is_never_stuck_however_old(status) -> None:
    assert not monitoring.is_stuck(_aged(status, 7 * 86_400))


def test_a_retry_takes_a_processing_document_only_past_the_running_limit() -> None:
    assert not monitoring.is_stuck_processing_for_retry(_aged(DocumentStatus.PROCESSING, RUNNING - 30))
    assert monitoring.is_stuck_processing_for_retry(_aged(DocumentStatus.PROCESSING, RUNNING + 30))
    # Queued past its limit is stuck, but a retry only re-queues a processing one.
    assert not monitoring.is_stuck_processing_for_retry(_aged(DocumentStatus.QUEUED, WAITING + 30))


@pytest.mark.parametrize("status, seconds", [
    (DocumentStatus.QUEUED, WAITING - 30), (DocumentStatus.QUEUED, WAITING + 30),
    (DocumentStatus.PROCESSING, RUNNING - 30), (DocumentStatus.PROCESSING, RUNNING + 30),
    (DocumentStatus.EXTRACTING, RUNNING + 30),
    # Between the two limits: a running stage timed with the waiting limit would read as stuck here.
    (DocumentStatus.PROCESSING, WAITING + 30), (DocumentStatus.EXTRACTING, WAITING + 30),
])
def test_the_queue_and_the_sweeper_agree_at_the_same_moment(status, seconds) -> None:
    document = _aged(status, seconds)
    with (
        patch("app.document_repository.get_documents_in_status", return_value=[document]),
        patch("app.audit.latest_times", return_value={}),
        patch("app.case_pipeline.repository.running_started_before", return_value=[]),
    ):
        swept = bool(sweeper.find_stuck(datetime.now(timezone.utc)))

    assert swept == monitoring.is_stuck(document)


# --- The sweeper reads in one pass, and only status columns --------------------------


def test_find_stuck_reads_detection_starts_once_and_documents_in_brief():
    documents = [_approved(f"d{i}") for i in range(5)] + [_doc("det", DocumentStatus.DETECTING, 60)]
    with (
        patch("app.document_repository.get_documents_in_status", return_value=documents) as get_docs,
        patch("app.detection_results.get_states", return_value=({}, set())),
        patch("app.audit.latest_times", return_value={}) as latest,
        patch("app.case_pipeline.repository.running_started_before", return_value=[]),
    ):
        sweeper.find_stuck(NOW)

    latest.assert_called_once()
    assert set(latest.call_args.args[1]) == {"d0", "d1", "d2", "d3", "d4", "det"}
    get_docs.assert_called_once()
    assert get_docs.call_args.kwargs["brief"] is True and get_docs.call_args.kwargs["include_fields"] is False


def test_earlier_attempts_reads_at_most_twice_and_splits_per_item_and_owner():
    docs = [sweeper.Stuck("processing", "doc-worker", NOW, 600, "case-1", document_id=f"doc-{i}") for i in range(30)]
    other_owner = sweeper.Stuck("approved", "detection-worker", NOW, 600, "case-1", document_id="doc-0")
    run = sweeper.Stuck(sweeper.CASE_ANALYSIS, "case-worker", NOW, 600, "case-1", run_id="run-1")
    other_run = sweeper.Stuck(sweeper.CASE_ANALYSIS, "case-worker", NOW, 600, "case-2", run_id="run-2")

    def _event(document_id, owner, **detail):
        return Event(type=types.RECOVERY_REQUESTED, document_id=document_id, case_id="case-1",
                     detail={types.OWNER_ATTRIBUTE: owner, **detail})

    by_document = [_event("doc-0", "doc-worker"), _event("doc-0", "detection-worker"), _event("doc-7", "doc-worker")]
    by_case = [_event(None, "case-worker", run_id="run-1"), _event(None, "case-worker", run_id="run-2")]
    with patch("app.audit.find_many", side_effect=[by_document, by_case]) as find_many:
        attempts = sweeper._earlier_attempts([*docs, other_owner, run, other_run], NOW)

    assert find_many.call_count == 2
    assert find_many.call_args_list[0].kwargs["document_ids"] == sorted(f"doc-{i}" for i in range(30))
    assert find_many.call_args_list[1].kwargs["case_ids"] == ["case-1", "case-2"]
    assert attempts[docs[0]] == [by_document[0]] and attempts[other_owner] == [by_document[1]]
    assert attempts[docs[7]] == [by_document[2]] and attempts[docs[1]] == []
    assert attempts[run] == [by_case[0]] and attempts[other_run] == [by_case[1]]


def test_earlier_attempts_reads_nothing_for_no_items():
    with patch("app.audit.find_many") as find_many:
        assert sweeper._earlier_attempts([], NOW) == {}
    find_many.assert_not_called()
