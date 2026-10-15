"""
Step-by-step log of one analysis run, stored on its case_analysis_runs row
at milestones (after planning, after judging, and at the end). A debugging aid:
it records what was tried, including hypotheses that were dropped, and is
never used for scoring.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import repository

logger = logging.getLogger("classifier_pipeline.case_pipeline.trace")

# One worker so saves land in order (each carries the full log, so the last write wins).
_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="case-analysis-trace")


def _save(run_id: str, events: list[dict[str, Any]]) -> None:
    try:
        repository.write_trace(run_id, events)
    except Exception as exc:
        logger.warning("Failed to save the trace of run %s: %s", run_id, exc)


class RunTrace:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._started = time.monotonic()
        self.events: list[dict[str, Any]] = []

    def record(self, step: str, **fields: Any) -> None:
        self.events.append({"step": step, "at": round(time.monotonic() - self._started, 2), **fields})

    def save(self) -> None:
        """Writes the log so far. Called at milestones rather than per event, to keep database writes down."""
        _writer.submit(_save, self.run_id, list(self.events))
