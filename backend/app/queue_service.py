from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from . import document_repository
from .config import settings
from .dify_client import dify_client, DifyInvocationError
from .models import DocumentStatus

logger = logging.getLogger("classifier_pipeline.queue")


class DocumentQueue:
    """
    In-memory async queue + single worker loop.

    This satisfies AC1 (document enters a queue for async processing) and
    AC2/AC3 (dequeue -> invoke Classifier -> retry on failure) for a
    single-instance deployment. For multi-instance/production use, swap
    this for a durable queue (e.g. Cloud Pub/Sub, as scoped for the
    Cloud Native award) so queued items survive a process restart —
    the in-memory queue does not.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    def start(self) -> None:
        if self._worker_task is None:
            self._worker_task = asyncio.create_task(self._worker_loop())
            logger.info("Document queue worker started")

    async def enqueue(self, document_id: str) -> None:
        """AC1: mark the document queued and place it on the queue."""
        now = datetime.now(timezone.utc)
        document_repository.update_status(
            document_id, DocumentStatus.QUEUED, queued_at=now
        )
        await self._queue.put(document_id)

    async def _worker_loop(self) -> None:
        while True:
            document_id = await self._queue.get()
            dequeued_at = time.monotonic()
            try:
                await self._process_document(document_id, dequeued_at)
            except Exception:  # noqa: BLE001 — never let one bad item kill the worker
                logger.exception("Unhandled error processing document %s", document_id)
            finally:
                self._queue.task_done()

    async def _process_document(self, document_id: str, dequeued_at: float) -> None:
        document = document_repository.get_document(document_id)
        if document is None:
            logger.error("Document %s not found when dequeued", document_id)
            return

        document_info = {
            "filename": document.filename,
            "storage_path": document.storage_path,
            "case_id": document.case_id,
        }

        # AC2: mark processing and invoke the Classifier workflow.
        # invoke_time_seconds is logged so "within 1 second of dequeue" is
        # measurable in tests/monitoring, independent of Dify's own latency.
        processing_started_at = datetime.now(timezone.utc)
        document_repository.update_status(
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

        # AC3: retry on invocation failure, up to MAX_INVOCATION_RETRIES.
        last_error: Exception | None = None
        for attempt in range(1, settings.MAX_INVOCATION_RETRIES + 1):
            try:
                await dify_client.invoke_classifier_workflow(document_id, document_info)
                return  # success — downstream Classifier workflow now owns status updates
            except DifyInvocationError as exc:
                last_error = exc
                logger.warning(
                    "Document %s: invocation attempt %d/%d failed: %s",
                    document_id, attempt, settings.MAX_INVOCATION_RETRIES, exc,
                )
                document_repository.update_status(
                    document_id, DocumentStatus.PROCESSING, retry_count=attempt
                )
                if attempt < settings.MAX_INVOCATION_RETRIES:
                    await asyncio.sleep(settings.RETRY_BACKOFF_SECONDS * attempt)

        # AC3: retries exhausted — fail and flag for manual review.
        document_repository.update_status(
            document_id,
            DocumentStatus.FAILED,
            failure_reason=f"Classifier invocation failed after "
            f"{settings.MAX_INVOCATION_RETRIES} attempts: {last_error}",
        )
        logger.error("Document %s marked FAILED — manual review required", document_id)


document_queue = DocumentQueue()