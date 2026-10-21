"""case-worker: runs a requested case analysis (cross-document findings, risk, summary)."""

from __future__ import annotations

import logging
from datetime import datetime

from .. import events
from ..case_pipeline import service as analysis_service
from ..events import Event, Stopwatch, types

logger = logging.getLogger("classifier_pipeline.case_worker")

_ACTOR = "case-worker"


async def on_analysis_requested(event: Event) -> None:
    detail = event.detail
    if event.case_id is None or not detail.get("run_id"):
        logger.error("Case analysis event %s is missing its case or run id", event.event_id)
        return
    timer = Stopwatch()
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
        },
        **timer.fields(),
    ))


HANDLERS = {types.CASE_ANALYSIS_REQUESTED: on_analysis_requested}
