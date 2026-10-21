"""detection-worker: one detection run (lead, specialists, checks, flags) per approved document."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import document_repository, events, query_stats
from ..config import settings
from ..events import Event, Stopwatch, types
from ..events.envelope import Outcome
from ..langgraph import trace as langgraph_trace
from ..langgraph.detection.graph import run_detection
from ..snapshots import push_case_snapshot

logger = logging.getLogger("classifier_pipeline.detection_worker")

_ACTOR = "detection-worker"
# A bulk approval waits here instead of starting every detection graph at once.
_slots = asyncio.Semaphore(settings.DETECTION_CONCURRENCY)
# document_id -> its running detection task here, so an officer's cancel can interrupt it.
_active: dict[str, asyncio.Task] = {}
_officer_cancels: set[str] = set()


async def run(document_id: str, case_id: str | None) -> None:
    """One detection run, its end event, and a live refresh. An interrupted run goes back to approved, retryable."""
    task = asyncio.current_task()
    if task is not None:
        _active[document_id] = task
    timer = Stopwatch()
    outcome: Outcome = "success"
    detail: dict[str, Any] = {}
    try:
        async with _slots:
            with query_stats.scope(f"detection doc={document_id[:8]}"):
                result = await run_detection(document_id)
        detail = {"verdict": result.get("verdict"), "findings": len(result.get("findings") or [])}
    except asyncio.CancelledError:
        by_officer = document_id in _officer_cancels
        _officer_cancels.discard(document_id)
        # Not an Exception, so run_detection's own handler never ran; revert DETECTING -> APPROVED here.
        logger.info("Detection cancelled for document %s", document_id)
        reason = "Cancelled by loan officer" if by_officer else "Interrupted: the service was stopping"
        langgraph_trace.record(document_id, "detection_error", error=reason)
        await asyncio.to_thread(document_repository.revert_to_approved, document_id)
        if not by_officer:
            raise
        outcome, detail = "cancelled", {"error": reason}
    except Exception as exc:
        logger.exception("Fraud detection failed for approved document %s", document_id)
        outcome, detail = "failed", {"error": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        _active.pop(document_id, None)
        if case_id:
            await push_case_snapshot(case_id)
    await events.record(Event(
        type=types.DOCUMENT_DETECTED if outcome == "success" else types.DETECTION_FAILED,
        actor_type="agent", actor=_ACTOR, case_id=case_id, document_id=document_id,
        outcome=outcome, detail=detail, **timer.fields(),
    ))


async def on_requested(event: Event) -> None:
    """An approval (unless the document has no type) or an officer's retry after a failed run."""
    if event.document_id is None or event.detail.get("skip_detection"):
        return
    await run(event.document_id, event.case_id)


async def on_cancel_requested(event: Event) -> None:
    """Interrupts the run if it is in this process."""
    task = _active.get(event.document_id or "")
    if task is not None and not task.done() and event.document_id is not None:
        _officer_cancels.add(event.document_id)
        task.cancel()


HANDLERS = {
    types.DOCUMENT_APPROVED: on_requested,
    types.DETECTION_RETRY_REQUESTED: on_requested,
    types.DETECTION_CANCEL_REQUESTED: on_cancel_requested,
}
