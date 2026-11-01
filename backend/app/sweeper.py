"""
The sweeper (CLOUD_ARCHITECTURE_PLAN.md section 16): finds work stuck past its stage's limit and asks the worker
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

from . import audit, document_repository, events, metrics
from .case_pipeline import repository as analysis_repository
from .config import settings
from .events import Event, types
from .models import DocumentRecord, DocumentStatus

logger = logging.getLogger("classifier_pipeline.sweeper")

_ACTOR = "sweeper"
CASE_ANALYSIS = "case_analysis"

# Document status -> (owning role, whether it waits for an event or runs).
_DOCUMENT_STAGES: dict[DocumentStatus, tuple[str, bool]] = {
    DocumentStatus.UPLOADED: ("doc-worker", False),
    DocumentStatus.QUEUED: ("doc-worker", False),
    DocumentStatus.PROCESSING: ("doc-worker", True),
    DocumentStatus.EXTRACTING: ("doc-worker", True),
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


def _document_since(document: DocumentRecord) -> datetime:
    if document.status == DocumentStatus.DETECTING:
        started = audit.latest_time(_DETECTION_STARTS, document.id)
        if started is not None:
            return _aware(started)
    return _aware(document.processing_started_at or document.queued_at or document.uploaded_at)


def find_stuck(now: datetime, case_id: str | None = None) -> list[Stuck]:
    """Every document and case analysis past its stage's limit. Sync: run on a worker thread."""
    stuck: list[Stuck] = []
    documents = document_repository.get_documents_in_status(list(_DOCUMENT_STAGES), include_fields=False)
    for document in documents:
        if case_id is not None and document.case_id != case_id:
            continue
        owner, running = _DOCUMENT_STAGES[document.status]
        limit = settings.STUCK_RUNNING_SECONDS if running else settings.STUCK_WAITING_SECONDS
        since = _document_since(document)
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


def _earlier_attempts(item: Stuck, now: datetime) -> list[Event]:
    """
    recovery.requested events already sent for this work to the same owner in the last day, newest first.
    By owner, not stage: a rerun moves a document from queued back through processing, and each try counts.
    """
    if item.run_id is not None:
        found = audit.find(types.RECOVERY_REQUESTED, case_id=item.case_id)
        return [e for e in found if e.detail.get("run_id") == item.run_id]
    found = audit.find(types.RECOVERY_REQUESTED, document_id=item.document_id, since=now - _ATTEMPT_WINDOW)
    return [e for e in found if e.detail.get(types.OWNER_ATTRIBUTE) == item.owner]


async def sweep(case_id: str | None = None) -> int:
    """One pass; returns how many recovery requests it published."""
    now = datetime.now(timezone.utc)
    stuck = await asyncio.to_thread(find_stuck, now, case_id)
    if case_id is None:
        for stage in STAGES:
            metrics.stuck_work.labels(stage).set(sum(1 for item in stuck if item.stage == stage))
    published = 0
    for item in stuck:
        earlier = await asyncio.to_thread(_earlier_attempts, item, now)
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
