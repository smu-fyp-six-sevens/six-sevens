"""
Runs the checks at the end of each run and records them on the run's own trace as one "evals" step, inside the run.
Never raises and never changes the run: an error here is logged and the run carries on.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from typing import Any

from .. import metrics, telemetry
from .base import EVALS_VERSION, Check, run_steps

logger = logging.getLogger("classifier_pipeline.evals")

Sink = Callable[..., None]


def _record(sink: Sink, results: list[Check]) -> None:
    if not results:
        return
    for result in results:
        metrics.agent_eval_results.labels(result.agent, result.check, result.result).inc()
    counts = Counter(r.result for r in results)
    sink(
        "evals", evals_version=EVALS_VERSION, results=[r.as_dict() for r in results],
        passed=counts["pass"], warnings=counts["warn"], failures=counts["fail"],
    )


def _safely(what: str, run: Callable[[], None]) -> None:
    try:
        run()
    except Exception:
        logger.exception("Evals failed for %s; the run is unaffected", what)


def _document_steps(document_id: str) -> list[dict[str, Any]]:
    from ..langgraph import trace

    return run_steps((trace.get(document_id) or {}).get("steps") or [], telemetry.current_step_id())


def record_document_run(document_id: str) -> None:
    """Classification and extraction (and an extraction rerun). Call inside the run's root span."""
    def run() -> None:
        from ..langgraph import trace
        from . import classifier, extraction

        steps = _document_steps(document_id)
        _record(lambda step, **fields: trace.record(document_id, step, **fields),
                [*classifier.checks(steps), *extraction.checks(steps)])

    _safely(f"document {document_id}", run)


def record_detection_run(document_id: str, *, document_type: str | None, lead_summary: str | None) -> None:
    """The detection lead and specialists. Call inside the run's root span."""
    def run() -> None:
        from ..langgraph import trace
        from . import detection

        steps = _document_steps(document_id)
        _record(lambda step, **fields: trace.record(document_id, step, **fields),
                detection.checks(steps, document_type=document_type, lead_summary=lead_summary))

    _safely(f"detection of document {document_id}", run)


def record_case_run(trace: Any) -> None:
    """The case analysis agents, on a case_pipeline.trace.RunTrace. Call inside the run's root span."""
    def run() -> None:
        from . import case_analysis

        steps = run_steps(trace.events, telemetry.current_step_id())
        _record(trace.record, case_analysis.checks(steps))

    _safely(f"case analysis run {getattr(trace, 'run_id', '?')}", run)
