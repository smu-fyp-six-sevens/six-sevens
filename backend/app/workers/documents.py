"""
doc-worker: OCR, classify and extract one document per event. Replaces the in-memory queue: the event
is the queue entry, Pub/Sub (or this process, in-process) is the queue.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from .. import document_repository, events, metrics, query_stats
from ..config import settings
from ..events import Event, Stopwatch, types
from ..langgraph import pipeline as langgraph_pipeline
from ..models import DocumentStatus, ProcessingErrorEntry
from . import recovery

logger = logging.getLogger("classifier_pipeline.doc_worker")

_ACTOR = "doc-worker"
_slots = asyncio.Semaphore(settings.QUEUE_WORKER_CONCURRENCY)
# document_id -> the task running it here, so an officer's cancel can interrupt it.
_active: dict[str, asyncio.Task] = {}
_officer_cancels: set[str] = set()
# Failures with no document row to write to; lost on restart.
_recent_errors: deque[ProcessingErrorEntry] = deque(maxlen=100)


def recent_errors() -> list[ProcessingErrorEntry]:
    return list(_recent_errors)


def _record_error(document_id: str, error: str) -> None:
    _recent_errors.append(ProcessingErrorEntry(document_id=document_id, error=error, occurred_at=datetime.now(timezone.utc)))


async def _process(document_id: str, queued_at: datetime) -> None:
    document = await asyncio.to_thread(document_repository.get_document, document_id, include_fields=False)
    if document is None:
        message = f"Document {document_id} not found when its event arrived (row may have been deleted)"
        logger.error(message)
        _record_error(document_id, message)
        return
    if document.status != DocumentStatus.QUEUED:
        # Cancelled, or a repeat delivery of an event already handled.
        logger.info("Document %s no longer queued (status=%s); skipping", document_id, document.status.value)
        return

    started_at = datetime.now(timezone.utc)
    claimed = await asyncio.to_thread(
        document_repository.update_status, document_id, DocumentStatus.PROCESSING,
        processing_started_at=started_at, expected_current_statuses=[DocumentStatus.QUEUED],
    )
    if not claimed:
        logger.info("Document %s left queued before it could start; skipping", document_id)
        return
    waited = (started_at - queued_at).total_seconds()
    if waited > settings.DEQUEUE_TO_INVOKE_TARGET_SECONDS:
        logger.info("Document %s: processing started %.1fs after it was queued", document_id, waited)
    # Owns classify -> extract -> ANALYZED/FAILED from here, and never raises.
    await langgraph_pipeline.process_document(document_id)


async def _cancelled_elsewhere(document_id: str) -> bool:
    document = await asyncio.to_thread(document_repository.get_document, document_id, include_fields=False)
    return document is not None and document.status == DocumentStatus.CANCELLED


def _note_remote_cancel(document_id: str) -> None:
    _officer_cancels.add(document_id)
    metrics.cancels_seen_remotely.labels("document").inc()


async def _run(document_id: str, work: Callable[[], Awaitable[None]], label: str, kind: str = "document") -> None:
    """`label` names the unit in the perf log; `kind` is its work label in the cost metrics."""
    task = asyncio.current_task()
    watcher: asyncio.Task | None = None
    if task is not None:
        _active[document_id] = task
        # The cancel route writes CANCELLED; a run in another instance notices it here.
        watcher = asyncio.create_task(recovery.cancel_when(
            task, lambda: _cancelled_elsewhere(document_id), lambda: _note_remote_cancel(document_id),
        ))
    timer = Stopwatch()
    usage: dict[str, float | int] = {}
    try:
        async with _slots:
            with query_stats.scope(f"{label} doc={document_id[:8]}", work=kind) as unit:
                try:
                    await work()
                finally:
                    usage = unit.llm_detail()
    except asyncio.CancelledError:
        if document_id not in _officer_cancels:
            raise
        # The officer's cancel already wrote CANCELLED and its own event.
        _officer_cancels.discard(document_id)
        logger.info("Processing of document %s was cancelled", document_id)
        return
    except Exception as exc:
        logger.exception("Unhandled error processing document %s", document_id)
        _record_error(document_id, str(exc))
        try:
            await asyncio.to_thread(
                document_repository.update_status, document_id, DocumentStatus.FAILED,
                failure_reason=f"Unhandled processing error: {exc}",
            )
        except Exception:
            logger.exception("Failed to mark document %s FAILED after an unhandled error", document_id)
    finally:
        _active.pop(document_id, None)
        if watcher is not None:
            watcher.cancel()
    await _announce(document_id, timer, {"run": kind, **usage})


async def _announce(document_id: str, timer: Stopwatch, usage: dict) -> None:
    """
    Publishes how the run ended (analyzed or failed), with its Claude usage; anything else, such as cancelled,
    was announced by its cause.
    """
    document = await asyncio.to_thread(document_repository.get_document, document_id, include_fields=False)
    if document is None or document.status not in (DocumentStatus.ANALYZED, DocumentStatus.FAILED):
        return
    failed = document.status == DocumentStatus.FAILED
    detail = (
        {"document_type": document.document_type, "failure_reason": document.failure_reason} if failed
        else {"document_type": document.document_type, "classification_confidence": document.classification_confidence}
    )
    await events.record(Event(
        type=types.DOCUMENT_FAILED if failed else types.DOCUMENT_ANALYZED,
        actor_type="agent", actor=_ACTOR, case_id=document.case_id, document_id=document_id,
        outcome="failed" if failed else "success", detail={**detail, **usage}, **timer.fields(),
    ))


async def on_queued(event: Event) -> None:
    """A new upload, or an officer's retry of a failed document."""
    if event.document_id is None:
        return
    document_id = event.document_id
    await _run(document_id, lambda: _process(document_id, event.occurred_at), "pipeline")


async def on_retyped(event: Event) -> None:
    """The officer changed the type: extraction only, against the stored type."""
    if event.document_id is None:
        return
    document_id = event.document_id
    await _run(document_id, lambda: langgraph_pipeline.rerun_extraction(document_id), "rerun extraction", "extraction_rerun")


async def on_cancelled(event: Event) -> None:
    """Interrupts the run if it is in this process. Elsewhere, its compare-and-swap writes no longer match."""
    task = _active.get(event.document_id or "")
    if task is not None and not task.done() and event.document_id is not None:
        _officer_cancels.add(event.document_id)
        task.cancel()


async def on_recovery_requested(event: Event) -> None:
    """
    The sweeper found this document stuck in a doc-worker stage (its event lost, or its run died): back to
    queued and run again, or, past the attempt limit, failed so the officer can retry it.
    """
    if not recovery.is_for(event, _ACTOR) or event.document_id is None:
        return
    document_id = event.document_id
    stuck_in = DocumentStatus(str(event.detail.get("stage")))
    if recovery.exhausted(event):
        failed = await asyncio.to_thread(
            document_repository.update_status, document_id, DocumentStatus.FAILED,
            failure_reason=f"Stuck while {stuck_in.value}; automatic recovery gave up after "
                           f"{settings.RECOVERY_MAX_ATTEMPTS} attempts. Retry it.",
            expected_current_statuses=[stuck_in],
        )
        if failed:
            await recovery.report(event, _ACTOR, types.RECOVERY_EXHAUSTED, "failed")
        return
    queued_at = datetime.now(timezone.utc)
    reset = await asyncio.to_thread(
        document_repository.update_status, document_id, DocumentStatus.QUEUED,
        queued_at=queued_at, expected_current_statuses=[stuck_in],
    )
    if not reset:
        logger.info("Document %s left %s before its recovery; nothing to do", document_id, stuck_in.value)
        return
    await _run(document_id, lambda: _process(document_id, queued_at), "pipeline")
    document = await asyncio.to_thread(document_repository.get_document, document_id, include_fields=False)
    status = document.status.value if document else "deleted"
    await recovery.report(
        event, _ACTOR, types.DOCUMENT_RECOVERED,
        "success" if status == DocumentStatus.ANALYZED.value else "failed", status=status,
    )


HANDLERS = {
    types.DOCUMENT_UPLOADED: on_queued,
    types.DOCUMENT_RETRY_REQUESTED: on_queued,
    types.DOCUMENT_RETYPED: on_retyped,
    types.DOCUMENT_CANCELLED: on_cancelled,
    types.RECOVERY_REQUESTED: on_recovery_requested,
}
