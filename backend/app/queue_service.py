from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone

from . import document_repository, orchestrator
from .config import settings
from .models import DocumentStatus, ProcessingErrorEntry

logger = logging.getLogger("classifier_pipeline.queue")


class DocumentQueue:
    """
    In-memory async queue + a pool of worker loops pulling from it
    concurrently (size: settings.QUEUE_WORKER_CONCURRENCY).

    This satisfies AC1 (document enters a queue for async processing) and
    AC2 (dequeue -> hand off within a target window) for a single-instance
    deployment. Once handed off, orchestrator.process_document owns
    classification/extraction/retry/terminal-status — this layer no longer
    retries invocations itself (see orchestrator.py for why). Concurrent
    workers are safe here: DifyClient opens a fresh httpx.AsyncClient per
    call (no shared connection to race on), and the shared Supabase client
    is pinned to HTTP/1.1 (see supabase_client.py) specifically so
    concurrent requests don't corrupt a shared HTTP/2 connection. For
    multi-instance/production use, swap this for a durable queue (e.g.
    Cloud Pub/Sub, as scoped for the Cloud Native award) so queued items
    survive a process restart — the in-memory queue does not.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_tasks: list[asyncio.Task] = []
        # Bounded in-memory log for failures that have no document row to
        # write to (or that are worth surfacing outside per-document status).
        # Same durability trade-off as the queue itself — lost on restart.
        self._recent_errors: deque[ProcessingErrorEntry] = deque(maxlen=100)

    def start(self) -> None:
        if self._worker_tasks:
            return
        for worker_id in range(settings.QUEUE_WORKER_CONCURRENCY):
            self._worker_tasks.append(asyncio.create_task(self._worker_loop(worker_id)))
        logger.info(
            "Document queue worker pool started (%d workers)",
            settings.QUEUE_WORKER_CONCURRENCY,
        )

    def _record_error(self, document_id: str, error: str) -> None:
        self._recent_errors.append(
            ProcessingErrorEntry(
                document_id=document_id,
                error=error,
                occurred_at=datetime.now(timezone.utc),
            )
        )

    def get_recent_errors(self) -> list[ProcessingErrorEntry]:
        return list(self._recent_errors)

    async def enqueue(self, document_id: str) -> None:
        """AC1: mark the document queued and place it on the queue."""
        now = datetime.now(timezone.utc)
        await asyncio.to_thread(
            document_repository.update_status,
            document_id, DocumentStatus.QUEUED, queued_at=now
        )
        await self._queue.put(document_id)

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            document_id = await self._queue.get()
            dequeued_at = time.monotonic()
            try:
                await self._process_document(document_id, dequeued_at)
            except Exception as exc:  # noqa: BLE001 — never let one bad item kill the worker
                logger.exception(
                    "Worker %d: unhandled error processing document %s", worker_id, document_id
                )
                self._record_error(document_id, str(exc))
                # Last-resort safety net: orchestrator.process_document already
                # catches its own exceptions and marks FAILED, but errors raised
                # before/outside that call (e.g. get_document here) would
                # otherwise leave the document stuck at a non-terminal status
                # forever, with the frontend polling indefinitely.
                try:
                    await asyncio.to_thread(
                        document_repository.update_status,
                        document_id,
                        DocumentStatus.FAILED,
                        failure_reason=f"Unhandled processing error: {exc}",
                    )
                except Exception:
                    logger.exception(
                        "Worker %d: failed to mark document %s FAILED after unhandled error",
                        worker_id, document_id,
                    )
            finally:
                self._queue.task_done()

    async def _process_document(self, document_id: str, dequeued_at: float) -> None:
        document = await asyncio.to_thread(document_repository.get_document, document_id)
        if document is None:
            message = f"Document {document_id} not found when dequeued (row may have been deleted)"
            logger.error(message)
            self._record_error(document_id, message)
            return

        # AC2: mark processing before handing off to the orchestrator.
        # invoke_time_seconds is logged so "within 1 second of dequeue" is
        # measurable in tests/monitoring, independent of Dify's own latency.
        processing_started_at = datetime.now(timezone.utc)
        await asyncio.to_thread(
            document_repository.update_status,
            document_id,
            DocumentStatus.PROCESSING,
            processing_started_at=processing_started_at,
        )
        invoke_time_seconds = time.monotonic() - dequeued_at
        if invoke_time_seconds > settings.DEQUEUE_TO_INVOKE_TARGET_SECONDS:
            logger.warning(
                "Document %s: invocation started %.3fs after dequeue (target %.1fs)",
                document_id, invoke_time_seconds, settings.DEQUEUE_TO_INVOKE_TARGET_SECONDS,
            )

        # orchestrator.process_document owns classify -> extract -> escalate
        # -> terminal status (ANALYZED/FAILED) from here. It catches its own
        # DifyInvocationErrors and translates them into a FAILED status
        # rather than raising, so no retry loop is needed at this layer.
        await orchestrator.process_document(document_id)


document_queue = DocumentQueue()