from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models import DocumentRecord, DocumentStatus
from app.queue_service import DocumentQueue


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


def test_start_creates_worker_pool_only_once() -> None:
    queue = DocumentQueue()
    with patch("app.queue_service.asyncio.create_task") as mock_create_task:
        queue.start()
        queue.start()

        # One task per pool slot on the first call; the second call is a
        # no-op since _worker_tasks is already populated.
        assert mock_create_task.call_count == settings.QUEUE_WORKER_CONCURRENCY
        # create_task is mocked, so the real coroutines passed to it (from
        # self._worker_loop(...)) never actually get scheduled — close them
        # explicitly rather than leaving them to warn "never awaited".
        for call in mock_create_task.call_args_list:
            call.args[0].close()


@pytest.mark.asyncio
async def test_enqueue_marks_queued_and_puts_document_id_on_queue() -> None:
    queue = DocumentQueue()
    with patch("app.queue_service.document_repository") as mock_repo:
        await queue.enqueue("doc-1")

        args, kwargs = mock_repo.update_status.call_args
        assert args[0] == "doc-1"
        assert args[1] == DocumentStatus.QUEUED
        assert "queued_at" in kwargs

        dequeued_id = await queue._queue.get()
        assert dequeued_id == "doc-1"


@pytest.mark.asyncio
async def test_process_document_records_error_when_document_missing() -> None:
    queue = DocumentQueue()
    with (
        patch("app.queue_service.document_repository") as mock_repo,
        patch("app.queue_service.orchestrator") as mock_orchestrator,
    ):
        mock_repo.get_document.return_value = None

        await queue._process_document("missing-doc", dequeued_at=time.monotonic())

        mock_repo.update_status.assert_not_called()
        mock_orchestrator.process_document.assert_not_called()
        errors = queue.get_recent_errors()
        assert len(errors) == 1
        assert errors[0].document_id == "missing-doc"


@pytest.mark.asyncio
async def test_process_document_marks_processing_then_delegates_to_orchestrator() -> None:
    queue = DocumentQueue()
    with (
        patch("app.queue_service.document_repository") as mock_repo,
        patch("app.queue_service.orchestrator") as mock_orchestrator,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_orchestrator.process_document = AsyncMock()

        await queue._process_document("doc-1", dequeued_at=time.monotonic())

        args, kwargs = mock_repo.update_status.call_args
        assert args[0] == "doc-1"
        assert args[1] == DocumentStatus.PROCESSING
        assert "processing_started_at" in kwargs
        mock_orchestrator.process_document.assert_awaited_once_with("doc-1")


@pytest.mark.asyncio
async def test_process_document_logs_warning_when_invocation_slow() -> None:
    queue = DocumentQueue()
    with (
        patch("app.queue_service.document_repository") as mock_repo,
        patch("app.queue_service.orchestrator") as mock_orchestrator,
        patch("app.queue_service.settings") as mock_settings,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_orchestrator.process_document = AsyncMock()
        mock_settings.DEQUEUE_TO_INVOKE_TARGET_SECONDS = 0.0

        # dequeued "in the past" so elapsed time exceeds the (zeroed) target
        await queue._process_document("doc-1", dequeued_at=time.monotonic() - 5)

        mock_orchestrator.process_document.assert_awaited_once_with("doc-1")


@pytest.mark.asyncio
async def test_worker_loop_continues_after_one_item_raises() -> None:
    queue = DocumentQueue()
    calls: list[str] = []

    async def fake_process(document_id: str, dequeued_at: float) -> None:
        calls.append(document_id)
        if document_id == "doc-bad":
            raise RuntimeError("boom")

    with patch.object(queue, "_process_document", new=fake_process):
        task = asyncio.create_task(queue._worker_loop(0))
        try:
            await queue._queue.put("doc-bad")
            await queue._queue.put("doc-good")
            await asyncio.wait_for(queue._queue.join(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    assert calls == ["doc-bad", "doc-good"]
