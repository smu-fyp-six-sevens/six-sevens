from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.case_pipeline import service
from app.case_pipeline.readiness import is_ready_for_analysis
from app.events import Event, types
from app.workers import cases as case_worker
from app.models import DocumentRecord, DocumentStatus


def _document(document_id: str, status: DocumentStatus, document_type: str = "bank_statement") -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename=f"{document_id}.pdf",
        document_type=document_type,
        status=status,
        uploaded_at="2026-10-01T00:00:00Z",
    )


def test_a_case_with_no_documents_is_not_ready() -> None:
    assert is_ready_for_analysis([]) is False


def test_all_approved_documents_detected_is_ready() -> None:
    documents = [_document("a", DocumentStatus.DETECTED), _document("b", DocumentStatus.DETECTED)]

    assert is_ready_for_analysis(documents) is True


@pytest.mark.parametrize("status", [DocumentStatus.APPROVED, DocumentStatus.DETECTING])
def test_an_approved_document_still_being_scanned_blocks(status: DocumentStatus) -> None:
    documents = [_document("a", DocumentStatus.DETECTED), _document("b", status)]

    assert is_ready_for_analysis(documents) is False


@pytest.mark.parametrize(
    "status",
    [DocumentStatus.ANALYZED, DocumentStatus.REJECTED, DocumentStatus.CANCELLED, DocumentStatus.FAILED],
)
def test_documents_that_are_not_approved_do_not_block(status: DocumentStatus) -> None:
    documents = [_document("a", DocumentStatus.DETECTED), _document("b", status)]

    assert is_ready_for_analysis(documents) is True


def test_only_unapproved_documents_is_not_ready() -> None:
    assert is_ready_for_analysis([_document("a", DocumentStatus.ANALYZED)]) is False


def test_a_scanned_other_document_counts_as_approved() -> None:
    documents = [_document("a", DocumentStatus.DETECTED, "unrecognised")]

    assert is_ready_for_analysis(documents) is True


# --- A run is labelled with what it read, and an unsaved result isn't reported complete (rows 13 and 14) ----------

STARTED = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)


def _completes(analysis, *args) -> None:
    analysis.status = "complete"


def _run(*, finish_run=None, fail_if_running=None, sync_case=None, analyse=_completes, read="as-read"):
    """service.run_requested with its reads, the analysis itself and every write stood in for."""
    patches = {
        "list_documents": patch.object(service.document_repository, "list_documents",
                                       return_value=[_document("doc-1", DocumentStatus.DETECTED)]),
        "get_states": patch.object(service.detection_results, "get_states", return_value=({}, set())),
        "fingerprint": patch.object(service.state, "compute_fingerprint", return_value=read),
        "analyse": patch.object(service, "_analyse", AsyncMock(side_effect=analyse)),
        "evals": patch.object(service.evals, "record_case_run"),
        "trace": patch.object(service, "RunTrace", MagicMock()),
        "finish_run": patch.object(service.repository, "finish_run", finish_run or MagicMock(return_value=True)),
        "fail_if_running": patch.object(service.repository, "fail_if_running",
                                        fail_if_running or MagicMock(return_value=True)),
        "sync_case": patch.object(service.findings_repository, "sync_case", sync_case or MagicMock()),
        "risk": patch.object(service.risk_store, "save"),
        "requested": patch.object(service.case_repository, "set_recommended_additional_documents"),
        "stage": patch.object(service, "sync_stage", AsyncMock()),
        "snapshot": patch.object(service, "push_case_snapshot", AsyncMock()),
    }

    async def go():
        with ExitStack() as stack:
            mocks = {name: stack.enter_context(p) for name, p in patches.items()}
            analysis = await service.run_requested("case-1", "run-1", "as-requested", STARTED)
        return analysis, mocks

    return go()


async def test_a_run_is_labelled_with_the_documents_it_read_not_the_request() -> None:
    analysis, mocks = await _run(read="as-read")

    assert analysis.fingerprint == "as-read"
    (stored,) = mocks["finish_run"].call_args.args
    assert stored.fingerprint == "as-read" and stored.run_id == "run-1" and stored.started_at == STARTED


async def test_a_saved_run_is_complete() -> None:
    analysis, mocks = await _run()

    assert analysis.status == "complete" and analysis.error is None
    mocks["fail_if_running"].assert_not_called()


async def test_a_result_that_couldnt_be_saved_is_failed_not_complete() -> None:
    analysis, mocks = await _run(finish_run=MagicMock(side_effect=RuntimeError("database down")))

    assert analysis.status == "failed"
    assert "couldn't be saved" in analysis.error
    run_id, error = mocks["fail_if_running"].call_args.args
    assert run_id == "run-1" and error == analysis.error
    mocks["snapshot"].assert_awaited_once()  # viewers still see where it ended


async def test_when_even_failing_it_cant_be_written_the_run_still_reads_failed() -> None:
    analysis, _ = await _run(finish_run=MagicMock(side_effect=RuntimeError("database down")),
                             fail_if_running=MagicMock(side_effect=RuntimeError("still down")))

    assert analysis.status == "failed"


async def test_a_later_step_failing_keeps_a_saved_run_complete() -> None:
    # The run row is the source of truth: its result is stored, so a findings-table sync failing doesn't undo it.
    analysis, mocks = await _run(sync_case=MagicMock(side_effect=RuntimeError("findings table down")))

    assert analysis.status == "complete"
    mocks["fail_if_running"].assert_not_called()
    mocks["risk"].assert_called_once()


async def test_the_case_worker_reports_an_unsaved_run_as_failed(published_events) -> None:
    unsaved = service.CaseAnalysis(run_id="run-1", case_id="case-1", status="failed", fingerprint="f",
                                   started_at=STARTED, error="The analysis finished but its result couldn't be saved.")
    event = Event(type=types.CASE_ANALYSIS_REQUESTED, case_id="case-1",
                  detail={"run_id": "run-1", "fingerprint": "f", "started_at": STARTED.isoformat()})
    with (
        patch.object(case_worker.analysis_repository, "claim_run", return_value=True),
        patch.object(case_worker.analysis_service, "run_requested", AsyncMock(return_value=unsaved)),
    ):
        await case_worker.on_analysis_requested(event)

    (end,) = published_events
    assert end.type == types.CASE_ANALYSIS_COMPLETED
    assert end.outcome == "failed" and end.detail["status"] == "failed"
    assert "couldn't be saved" in end.detail["error"]


async def test_the_case_workers_end_event_carries_the_case_agents_versions(published_events) -> None:
    done = service.CaseAnalysis(run_id="run-1", case_id="case-1", status="complete", fingerprint="f", started_at=STARTED)
    event = Event(type=types.CASE_ANALYSIS_REQUESTED, case_id="case-1",
                  detail={"run_id": "run-1", "fingerprint": "f", "started_at": STARTED.isoformat()})
    with (
        patch.object(case_worker.analysis_repository, "claim_run", return_value=True),
        patch.object(case_worker.analysis_service, "run_requested", AsyncMock(return_value=done)),
        patch.object(case_worker.agent_versions, "for_run", return_value={"case_planner": "fp-3"}) as for_run,
    ):
        await case_worker.on_analysis_requested(event)

    for_run.assert_called_once_with("case_analysis")
    (end,) = published_events
    assert end.detail["agent_versions"] == {"case_planner": "fp-3"}
