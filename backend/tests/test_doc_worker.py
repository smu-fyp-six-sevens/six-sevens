from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from app.events import Event, types
from app.models import DocumentRecord, DocumentStatus
from app.workers import documents as doc_worker


def _make_document(
    document_id: str = "doc-1", status: DocumentStatus = DocumentStatus.QUEUED
) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename="statement.pdf",
        storage_path=f"case-1/{document_id}_statement.pdf",
        status=status,
        uploaded_at="2026-08-26T00:00:00Z",
    )


def _uploaded(document_id: str = "doc-1") -> Event:
    return Event(type=types.DOCUMENT_UPLOADED, document_id=document_id, case_id="case-1")


async def test_missing_document_is_recorded_and_nothing_runs(published_events) -> None:
    doc_worker._recent_errors.clear()
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = None

        await doc_worker.on_queued(_uploaded("missing-doc"))

    mock_repo.update_status.assert_not_called()
    mock_pipeline.process_document.assert_not_called()
    assert [e.document_id for e in doc_worker.recent_errors()] == ["missing-doc"]
    assert published_events == []


async def test_queued_document_is_claimed_then_run_then_announced(published_events) -> None:
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.side_effect = [_make_document(), _make_document(status=DocumentStatus.ANALYZED)]
        mock_repo.update_status.return_value = True
        mock_pipeline.process_document = AsyncMock()

        await doc_worker.on_queued(_uploaded())

    args, kwargs = mock_repo.update_status.call_args
    assert args[:2] == ("doc-1", DocumentStatus.PROCESSING)
    assert kwargs["expected_current_statuses"] == [DocumentStatus.QUEUED]
    mock_pipeline.process_document.assert_awaited_once_with("doc-1")
    assert [(e.type, e.actor, e.outcome) for e in published_events] == [
        (types.DOCUMENT_ANALYZED, "doc-worker", "success")
    ]
    assert published_events[0].duration_ms is not None


async def test_a_repeat_delivery_of_a_started_document_is_skipped(published_events) -> None:
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = _make_document(status=DocumentStatus.PROCESSING)

        await doc_worker.on_queued(_uploaded())

    mock_repo.update_status.assert_not_called()
    mock_pipeline.process_document.assert_not_called()
    assert published_events == []


async def test_a_lost_claim_does_not_run_the_pipeline() -> None:
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_repo.update_status.return_value = False

        await doc_worker.on_queued(_uploaded())

    mock_pipeline.process_document.assert_not_called()


async def test_slow_start_still_runs() -> None:
    late = Event(
        type=types.DOCUMENT_UPLOADED, document_id="doc-1",
        occurred_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_repo.update_status.return_value = True
        mock_pipeline.process_document = AsyncMock()

        await doc_worker.on_queued(late)

    mock_pipeline.process_document.assert_awaited_once_with("doc-1")


async def test_an_unhandled_error_marks_the_document_failed_and_announces_it(published_events) -> None:
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        failed = _make_document(status=DocumentStatus.FAILED)
        failed.failure_reason = "Unhandled processing error: boom"
        mock_repo.get_document.side_effect = [_make_document(), failed]
        mock_repo.update_status.return_value = True
        mock_pipeline.process_document = AsyncMock(side_effect=RuntimeError("boom"))

        await doc_worker.on_queued(_uploaded())

    assert mock_repo.update_status.call_args.args[:2] == ("doc-1", DocumentStatus.FAILED)
    assert [(e.type, e.outcome) for e in published_events] == [(types.DOCUMENT_FAILED, "failed")]


async def test_retyped_reruns_extraction_only() -> None:
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = _make_document(status=DocumentStatus.ANALYZED)
        mock_pipeline.rerun_extraction = AsyncMock()

        await doc_worker.on_retyped(Event(type=types.DOCUMENT_RETYPED, document_id="doc-1"))

    mock_pipeline.rerun_extraction.assert_awaited_once_with("doc-1")
    mock_pipeline.process_document.assert_not_called()


async def test_an_officer_cancel_interrupts_the_run_here(published_events) -> None:
    started = asyncio.Event()

    async def slow(_: str) -> None:
        started.set()
        await asyncio.sleep(10)

    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_repo.update_status.return_value = True
        mock_pipeline.process_document = slow

        run = asyncio.create_task(doc_worker.on_queued(_uploaded()))
        await asyncio.wait_for(started.wait(), timeout=2)
        await doc_worker.on_cancelled(Event(type=types.DOCUMENT_CANCELLED, document_id="doc-1"))
        await asyncio.wait_for(run, timeout=2)

    assert not doc_worker._runs.is_running("doc-1")
    assert published_events == []
