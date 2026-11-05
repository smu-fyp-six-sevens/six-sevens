"""detection-worker: one detection run (lead, specialists, checks, flags) per approved document."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from .. import audit, detection_results, document_repository, events, metrics, query_stats
from ..case_pipeline import service as analysis_service
from ..config import settings
from ..events import Event, Stopwatch, types
from ..events.envelope import Outcome, officer_of
from ..langgraph import trace as langgraph_trace
from ..langgraph.detection.graph import end_unfinished_run, run_detection
from ..models import DocumentStatus
from ..snapshots import push_case_snapshot
from . import recovery

logger = logging.getLogger("classifier_pipeline.detection_worker")

_ACTOR = "detection-worker"
# A bulk approval waits here instead of starting every detection graph at once.
_slots = asyncio.Semaphore(settings.DETECTION_CONCURRENCY)
_runs = recovery.RunTracker("detection")


async def _cancel_requested_since(document_id: str, since: datetime) -> bool:
    found = await asyncio.to_thread(audit.find, types.DETECTION_CANCEL_REQUESTED, document_id=document_id, since=since)
    return bool(found)


async def run(document_id: str, case_id: str | None, requested_by: str | None = None) -> None:
    """
    One detection run, its end event, and a live refresh. An interrupted run goes back to approved, retryable.
    `requested_by` is the officer whose approval or retry started it, for the audit trail.
    """
    timer = Stopwatch()
    outcome: Outcome = "success"
    detail: dict[str, Any] = {}
    usage: dict[str, float | int] = {}
    try:
        # The cancel event is the flag: the audit trail shows one made through another instance.
        with _runs.track(document_id, lambda: _cancel_requested_since(document_id, timer.started_at)):
            async with _slots:
                with query_stats.scope(f"detection doc={document_id[:8]}", work="detection") as unit:
                    try:
                        result = await run_detection(document_id)
                    finally:
                        usage = unit.llm_detail()
        detail = {"verdict": result.get("verdict"), "findings": len(result.get("findings") or [])}
        metrics.detection_verdicts.labels(str(detail["verdict"] or "none")).inc()
    except asyncio.CancelledError:
        by_officer = _runs.cancelled_by_officer(document_id)
        # Not an Exception, so run_detection's own handler never ran; revert DETECTING -> APPROVED here.
        logger.info("Detection cancelled for document %s", document_id)
        reason = "Cancelled by loan officer" if by_officer else "Interrupted: the service was stopping"
        langgraph_trace.record(document_id, "detection_error", error=reason)
        await asyncio.to_thread(detection_results.save_error, document_id, reason)
        await asyncio.to_thread(langgraph_trace.flush, document_id)
        await asyncio.to_thread(end_unfinished_run, document_id)
        outcome, detail = "cancelled", {"error": reason}
        if not by_officer:
            # Shutting down: what the run spent still goes on the record before the cancel carries on.
            await _record_end(document_id, case_id, requested_by, timer, outcome, detail, usage)
            raise
    except Exception as exc:
        logger.exception("Fraud detection failed for approved document %s", document_id)
        outcome, detail = "failed", {"error": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        if case_id:
            # Every ending writes the document's status last, which Realtime reports.
            await push_case_snapshot(case_id, realtime_covers=True)
    await _record_end(document_id, case_id, requested_by, timer, outcome, detail, usage)
    # The last approved document scanned starts the case analysis.
    await analysis_service.start_if_due(case_id)


async def _record_end(
    document_id: str, case_id: str | None, requested_by: str | None, timer: Stopwatch, outcome: Outcome,
    detail: dict[str, Any], usage: dict[str, float | int],
) -> None:
    if requested_by is not None:
        detail = {**detail, "requested_by": requested_by}
    await events.record(Event(
        type=types.DOCUMENT_DETECTED if outcome == "success" else types.DETECTION_FAILED,
        actor_type="agent", actor=_ACTOR, case_id=case_id, document_id=document_id,
        outcome=outcome, detail={**detail, "run": "detection", **usage}, **timer.fields(),
    ))


async def on_requested(event: Event) -> None:
    """An approval (unless the document has no type) or an officer's retry after a failed run."""
    if event.document_id is None or event.detail.get("skip_detection"):
        return
    await run(event.document_id, event.case_id, officer_of(event))


async def on_cancel_requested(event: Event) -> None:
    """Interrupts the run if it is in this process."""
    _runs.cancel(event.document_id)


async def on_recovery_requested(event: Event) -> None:
    """
    The sweeper found this document stuck detecting (its run died without reverting): back to approved and run
    again, or, past the attempt limit, back to approved with the error, as a failed run leaves it.
    """
    if not recovery.is_for(event, _ACTOR) or event.document_id is None:
        return
    document_id = event.document_id
    reverted = await asyncio.to_thread(
        document_repository.update_status, document_id, DocumentStatus.APPROVED,
        expected_current_statuses=[DocumentStatus.DETECTING],
    )
    if not reverted:
        logger.info("Document %s is no longer detecting; nothing to recover", document_id)
        return
    if recovery.exhausted(event):
        error = f"Detection stopped responding; automatic recovery gave up after {settings.RECOVERY_MAX_ATTEMPTS} attempts."
        langgraph_trace.record(document_id, "detection_error", error=error)
        await asyncio.to_thread(detection_results.save_error, document_id, error)
        await asyncio.to_thread(langgraph_trace.flush, document_id)
        # A rerun that never finished leaves the earlier report standing.
        await asyncio.to_thread(end_unfinished_run, document_id)
        await recovery.report(event, _ACTOR, types.RECOVERY_EXHAUSTED, "failed")
        if event.case_id:
            await push_case_snapshot(event.case_id, realtime_covers=True)
        return
    await run(document_id, event.case_id)
    document = await asyncio.to_thread(document_repository.get_document, document_id, include_fields=False)
    status = document.status.value if document else "deleted"
    await recovery.report(
        event, _ACTOR, types.DOCUMENT_RECOVERED,
        "success" if status == DocumentStatus.DETECTED.value else "failed", status=status,
    )


HANDLERS = {
    types.DOCUMENT_APPROVED: on_requested,
    types.DETECTION_RETRY_REQUESTED: on_requested,
    types.DETECTION_CANCEL_REQUESTED: on_cancel_requested,
    types.RECOVERY_REQUESTED: on_recovery_requested,
}
