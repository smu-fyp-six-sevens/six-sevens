"""
The sweeper (CLOUD_ARCHITECTURE_PLAN.md section 5): finds work stuck past its stage's limit and asks the worker
that owns the stage to recover it, with a recovery.requested event. It writes no state.

Pub/Sub redelivery already covers a worker that dies mid-push. This covers what redelivery can't: an event lost
after its database write (a document left uploaded or queued), and a run that ended without a final status.

Cloud Scheduler publishes sweep.requested every 5 minutes and audit-metrics runs this. Off unless SWEEPER_ENABLED,
because the database is shared and a local process must not sweep cloud work. To show it locally against one case:
    python -m app.sweeper --once --case <case_id>
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import audit, detection_results, document_repository, events, metrics
from .case_pipeline import repository as analysis_repository
from .config import settings
from .events import Event, types
from .models import DocumentRecord, DocumentStatus, skips_detection

logger = logging.getLogger("classifier_pipeline.sweeper")

_ACTOR = "sweeper"
CASE_ANALYSIS = "case_analysis"

# Document status -> (owning role, whether it is timed with the running limit). Approved waits for an event, but
# on the running limit: a bulk approval legitimately queues documents behind the detection slots for minutes.
_DOCUMENT_STAGES: dict[DocumentStatus, tuple[str, bool]] = {
    DocumentStatus.UPLOADED: ("doc-worker", False),
    DocumentStatus.QUEUED: ("doc-worker", False),
    DocumentStatus.PROCESSING: ("doc-worker", True),
    DocumentStatus.EXTRACTING: ("doc-worker", True),
    DocumentStatus.APPROVED: ("detection-worker", True),
    DocumentStatus.DETECTING: ("detection-worker", True),
}
STAGES = [*(s.value for s in _DOCUMENT_STAGES), CASE_ANALYSIS]
# What starts a detection run, since the document row records no detection start time.
_DETECTION_STARTS = [types.DOCUMENT_APPROVED, types.DETECTION_RETRY_REQUESTED, types.RECOVERY_REQUESTED]
# Attempts older than this don't count, so work an officer retries days later starts fresh.
_ATTEMPT_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Stuck:
    stage: str
    owner: str
    since: datetime
    limit_seconds: int
    case_id: str | None
    document_id: str | None = None
    run_id: str | None = None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _document_since(document: DocumentRecord, detection_started: dict[str, datetime]) -> datetime:
    started = detection_started.get(document.id)
    if document.status in (DocumentStatus.APPROVED, DocumentStatus.DETECTING) and started is not None:
        return _aware(started)
    return _aware(document.processing_started_at or document.queued_at or document.uploaded_at)


def find_stuck(now: datetime, case_id: str | None = None) -> list[Stuck]:
    """Every document and case analysis past its stage's limit. Sync: run on a worker thread."""
    stuck: list[Stuck] = []
    documents = [
        d for d in document_repository.get_documents_in_status(list(_DOCUMENT_STAGES), include_fields=False)
        if case_id is None or d.case_id == case_id
    ]
    # Approved with a type and neither a report nor an error: its approval event was lost (a failed run keeps
    # its error, and the officer retries it).
    waiting = [d.id for d in documents if d.status == DocumentStatus.APPROVED and not skips_detection(d)]
    reports, failed = detection_results.get_states(waiting, slim=True) if waiting else ({}, set())
    never_started = set(waiting) - set(reports) - failed
    documents = [d for d in documents if d.status != DocumentStatus.APPROVED or d.id in never_started]
    detecting = [d.id for d in documents if d.status in (DocumentStatus.APPROVED, DocumentStatus.DETECTING)]
    detection_started = audit.latest_times(_DETECTION_STARTS, detecting) if detecting else {}
    for document in documents:
        owner, running = _DOCUMENT_STAGES[document.status]
        limit = settings.STUCK_RUNNING_SECONDS if running else settings.STUCK_WAITING_SECONDS
        since = _document_since(document, detection_started)
        if (now - since).total_seconds() > limit:
            stuck.append(Stuck(document.status.value, owner, since, limit, document.case_id, document_id=document.id))
    cutoff = now - timedelta(seconds=settings.CASE_ANALYSIS_TIMEOUT_SECONDS)
    for run_id, run_case_id, started_at in analysis_repository.running_started_before(cutoff):
        if case_id is None or run_case_id == case_id:
            stuck.append(Stuck(
                CASE_ANALYSIS, "case-worker", _aware(started_at), settings.CASE_ANALYSIS_TIMEOUT_SECONDS,
                run_case_id, run_id=run_id,
            ))
    return stuck


def _earlier_attempts(items: list[Stuck], now: datetime) -> dict[Stuck, list[Event]]:
    """
    For each item, recovery.requested events already sent for this work to the same owner in the last day, newest
    first; read in one pass for the whole sweep. By owner, not stage: a rerun moves a document from queued back
    through processing, and each try counts.
    """
    document_ids = sorted({i.document_id for i in items if i.run_id is None and i.document_id})
    case_ids = sorted({i.case_id for i in items if i.run_id is not None and i.case_id})
    by_document = audit.find_many(
        types.RECOVERY_REQUESTED, document_ids=document_ids, since=now - _ATTEMPT_WINDOW,
    ) if document_ids else []
    by_case = audit.find_many(types.RECOVERY_REQUESTED, case_ids=case_ids) if case_ids else []
    attempts: dict[Stuck, list[Event]] = {}
    for item in items:
        if item.run_id is not None:
            attempts[item] = [e for e in by_case if e.detail.get("run_id") == item.run_id]
        else:
            attempts[item] = [
                e for e in by_document
                if e.document_id == item.document_id and e.detail.get(types.OWNER_ATTRIBUTE) == item.owner
            ]
    return attempts


async def sweep(case_id: str | None = None) -> int:
    """One pass; returns how many recovery requests it published."""
    now = datetime.now(timezone.utc)
    stuck = await asyncio.to_thread(find_stuck, now, case_id)
    if case_id is None:
        for stage in STAGES:
            metrics.stuck_work.labels(stage).set(sum(1 for item in stuck if item.stage == stage))
    published = 0
    attempts = await asyncio.to_thread(_earlier_attempts, stuck, now) if stuck else {}
    for item in stuck:
        earlier = attempts.get(item, [])
        # The last request is still being worked on, or waiting in the owner's queue: give it a full period.
        if earlier and (now - _aware(earlier[0].occurred_at)).total_seconds() < item.limit_seconds:
            continue
        attempt = len(earlier) + 1
        await events.publish(Event(
            type=types.RECOVERY_REQUESTED, actor_type="system", actor=_ACTOR,
            case_id=item.case_id, document_id=item.document_id,
            detail={
                "stage": item.stage, types.OWNER_ATTRIBUTE: item.owner, "attempt": attempt,
                "max_attempts": settings.RECOVERY_MAX_ATTEMPTS, "run_id": item.run_id,
                "stuck_seconds": int((now - item.since).total_seconds()),
            },
        ))
        metrics.recoveries.labels(item.stage, "requested").inc()
        published += 1
        logger.warning("Stuck %s %s (attempt %d): recovery requested from %s",
                       item.stage, item.document_id or item.run_id, attempt, item.owner)
    return published


async def on_sweep_requested(event: Event) -> None:
    """Cloud Scheduler's tick. Not stored in the audit trail: a tick every 5 minutes is noise."""
    if not settings.SWEEPER_ENABLED:
        logger.info("Sweep requested but SWEEPER_ENABLED is off; ignoring")
        return
    await sweep()


async def _once(case_id: str | None) -> None:
    from .events import bus, registry
    from .workers import cases, detection, documents

    if settings.EVENT_TRANSPORT == "inprocess":
        # No other process to deliver to: run the owning workers here and wait for them.
        registry.clear()
        registry.install("doc-worker", documents.HANDLERS)
        registry.install("detection-worker", detection.HANDLERS)
        registry.install("case-worker", cases.HANDLERS)
    count = await sweep(case_id)
    print(f"{count} recovery request(s) published")
    await bus.drain()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--once", action="store_true", required=True, help="one pass, then exit")
    parser.add_argument("--case", help="only this case's documents and analysis runs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(_once(args.case))


if __name__ == "__main__":
    main()
