"""case-worker: runs a requested case analysis (cross-document findings, risk, summary)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from .. import agent_versions, events, query_stats
from ..case_pipeline import repository as analysis_repository
from ..case_pipeline import service as analysis_service
from ..events import Event, Stopwatch, types
from ..events.envelope import officer_of
from . import recovery

logger = logging.getLogger("classifier_pipeline.case_worker")

_ACTOR = "case-worker"


async def on_analysis_requested(event: Event) -> None:
    detail = event.detail
    if event.case_id is None or not detail.get("run_id"):
        logger.error("Case analysis event %s is missing its case or run id", event.event_id)
        return
    # Pub/Sub delivers at least once: a repeat of a request already taken (or ended) must not run the Claude calls again.
    if not await asyncio.to_thread(analysis_repository.claim_run, str(detail["run_id"])):
        logger.info("Case analysis run %s was already taken or has ended; ignoring the repeat", detail["run_id"])
        return
    timer = Stopwatch()
    with query_stats.scope(f"case analysis case={event.case_id[:8]}", work="case_analysis") as unit:
        analysis = await analysis_service.run_requested(
            event.case_id, str(detail["run_id"]), str(detail.get("fingerprint") or ""),
            datetime.fromisoformat(str(detail["started_at"])) if detail.get("started_at") else event.occurred_at,
        )
    complete = analysis.status == "complete"
    await events.record(Event(
        type=types.CASE_ANALYSIS_COMPLETED, actor_type="agent", actor=_ACTOR, case_id=event.case_id,
        outcome="success" if complete else "failed",
        detail={
            "run_id": analysis.run_id,
            "status": analysis.status,
            "risk_level": analysis.risk.level if analysis.risk else None,
            "findings": len(analysis.findings),
            "error": analysis.error,
            "run": "case_analysis",
            **unit.llm_detail(),
            "agent_versions": agent_versions.for_run("case_analysis"),
            "requested_by": officer_of(event),
        },
        **timer.fields(),
    ))


async def on_recovery_requested(event: Event) -> None:
    """
    The sweeper found a run still marked running past CASE_ANALYSIS_TIMEOUT_SECONDS. It is marked failed, not
    rerun: a rerun costs Claude calls and is the officer's decision (the card offers "Run again").
    """
    run_id = event.detail.get("run_id")
    if not recovery.is_for(event, _ACTOR) or not run_id:
        return
    failed = await asyncio.to_thread(
        analysis_repository.fail_if_running, str(run_id), "The analysis stopped responding before it finished. Run it again.",
    )
    if failed:
        if event.case_id is not None:
            await analysis_service.sync_stage(event.case_id)
        await recovery.report(event, _ACTOR, types.RECOVERY_EXHAUSTED, "failed", reason="marked failed; not rerun automatically")


HANDLERS = {
    types.CASE_ANALYSIS_REQUESTED: on_analysis_requested,
    types.RECOVERY_REQUESTED: on_recovery_requested,
}
