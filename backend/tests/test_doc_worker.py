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


async def test_missing_document_runs_nothing(published_events) -> None:
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.return_value = None

        await doc_worker.on_queued(_uploaded("missing-doc"))

    mock_repo.update_status.assert_not_called()
    mock_pipeline.process_document.assert_not_called()
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


async def test_the_end_event_carries_the_versions_of_the_agents_it_used(published_events) -> None:
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
        patch("app.workers.recovery.agent_versions.for_run", return_value={"classifier": "fp-1"}) as for_run,
    ):
        mock_repo.get_document.side_effect = [_make_document(), _make_document(status=DocumentStatus.ANALYZED)]
        mock_repo.update_status.return_value = True
        mock_pipeline.process_document = AsyncMock()

        await doc_worker.on_queued(_uploaded())

    for_run.assert_called_once_with("document")
    assert published_events[0].detail["agent_versions"] == {"classifier": "fp-1"}


def test_a_metered_run_records_its_agent_versions_even_when_it_raises() -> None:
    # Detection's end event reads the same meter, including after a failed run.
    from app.workers import recovery

    meter = recovery.Meter()
    with patch.object(recovery.agent_versions, "for_run", return_value={"lead": "fp-2"}) as for_run:
        try:
            with recovery.metered("detection doc=doc-1", "detection", meter):
                raise RuntimeError("boom")
        except RuntimeError:
            pass

    for_run.assert_called_once_with("detection")
    assert meter.usage["agent_versions"] == {"lead": "fp-2"}


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

        await doc_worker.on_retyped(Event(
            type=types.DOCUMENT_RETYPED, document_id="doc-1", detail={"requested_at": "2026-11-16T00:00:00+00:00"},
        ))

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


# --- A repeated "type changed" event runs nothing -------------------------------------------
# set_type moves the document to extracting with processing_started_at = requested_at; the worker claims the rerun
# with that stamp (compare-and-swap), so only the first delivery gets it.

REQUESTED_AT = "2026-11-16T00:00:00+00:00"


class _RetypedDocument:
    """document_repository for one document set_type has just moved to extracting, as the database behaves."""

    def __init__(self, status: DocumentStatus = DocumentStatus.EXTRACTING) -> None:
        self.status = status
        self.stamp = datetime.fromisoformat(REQUESTED_AT)
        self.claims: list[datetime] = []

    def claim_extraction_rerun(self, document_id: str, requested_at: datetime) -> bool:
        self.claims.append(requested_at)
        if self.status != DocumentStatus.EXTRACTING or self.stamp != requested_at:
            return False
        self.stamp = datetime.now(timezone.utc)
        return True

    def get_document(self, document_id: str, include_fields: bool = True) -> DocumentRecord:
        return _make_document(status=self.status)

    def get_status(self, document_id: str) -> DocumentStatus:
        return self.status


def _retyped(requested_at: str | None = REQUESTED_AT) -> Event:
    detail = {"from_type": "payslip", "to_type": "nric"}
    if requested_at is not None:
        detail["requested_at"] = requested_at
    return Event(type=types.DOCUMENT_RETYPED, document_id="doc-1", case_id="case-1", detail=detail)


async def test_a_retype_delivered_twice_reruns_extraction_once(published_events) -> None:
    document = _RetypedDocument()

    async def rerun(document_id: str) -> None:
        document.status = DocumentStatus.ANALYZED

    with (
        patch("app.workers.documents.document_repository", document),
        patch("app.workers.documents.langgraph_pipeline.rerun_extraction", AsyncMock(side_effect=rerun)) as run,
    ):
        await doc_worker.on_retyped(_retyped())
        await doc_worker.on_retyped(_retyped())

    run.assert_awaited_once_with("doc-1")
    assert len(document.claims) == 2
    assert [e.type for e in published_events] == [types.DOCUMENT_ANALYZED]  # the repeat announces nothing


async def test_a_retype_arriving_after_the_document_reached_analyzed_runs_nothing(published_events) -> None:
    document = _RetypedDocument(status=DocumentStatus.ANALYZED)
    with (
        patch("app.workers.documents.document_repository", document),
        patch("app.workers.documents.langgraph_pipeline.rerun_extraction", AsyncMock()) as run,
    ):
        await doc_worker.on_retyped(_retyped())

    run.assert_not_awaited()
    assert published_events == []


async def test_a_second_retype_takes_over_from_the_first(published_events) -> None:
    # The officer retyped again: set_type wrote a newer stamp, so the first event's claim no longer matches.
    document = _RetypedDocument()
    document.stamp = datetime(2026, 11, 16, 0, 5, tzinfo=timezone.utc)
    with (
        patch("app.workers.documents.document_repository", document),
        patch("app.workers.documents.langgraph_pipeline.rerun_extraction", AsyncMock()) as run,
    ):
        await doc_worker.on_retyped(_retyped())

    run.assert_not_awaited()


async def test_a_retype_with_no_requested_at_runs_and_claims_nothing(published_events) -> None:
    document = _RetypedDocument()
    with (
        patch("app.workers.documents.document_repository", document),
        patch("app.workers.documents.langgraph_pipeline.rerun_extraction", AsyncMock()) as run,
    ):
        await doc_worker.on_retyped(_retyped(requested_at=None))

    run.assert_not_awaited()
    assert document.claims == [] and published_events == []


# --- A skipped repeat announces nothing ---------------------------------------------------
# Announcing an analyzed or failed event for a run that never happened would double the run in the audit trail, and
# re-report a failure the first delivery already reported.


async def test_an_upload_event_for_a_document_no_longer_queued_runs_and_announces_nothing(published_events) -> None:
    for status in (DocumentStatus.ANALYZED, DocumentStatus.FAILED, DocumentStatus.PROCESSING):
        published_events.clear()
        with (
            patch("app.workers.documents.document_repository") as mock_repo,
            patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
        ):
            mock_repo.get_document.return_value = _make_document(status=status)
            await doc_worker.on_queued(_uploaded())

        mock_repo.update_status.assert_not_called()
        mock_pipeline.process_document.assert_not_called()
        assert published_events == [], status


async def test_an_upload_event_whose_claim_is_lost_announces_nothing(published_events) -> None:
    # Queued when read, but another delivery claimed it first.
    with (
        patch("app.workers.documents.document_repository") as mock_repo,
        patch("app.workers.documents.langgraph_pipeline") as mock_pipeline,
    ):
        mock_repo.get_document.side_effect = [_make_document(), _make_document(status=DocumentStatus.ANALYZED)]
        mock_repo.update_status.return_value = False
        await doc_worker.on_queued(_uploaded())

    mock_pipeline.process_document.assert_not_called()
    assert published_events == []
