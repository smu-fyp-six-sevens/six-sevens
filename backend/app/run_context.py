"""
What one agent run (a detection run, a case analysis) may spend, and the model outputs in it that
needed repair. Set once at the start of the run; every call inside it shares the same context,
including calls made by parallel graph nodes and worker threads.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any

from . import metrics

logger = logging.getLogger("classifier_pipeline.run_context")

_MAX_REPAIRS = 50


class RunBudgetExceeded(RuntimeError):
    pass


@dataclass
class RunContext:
    max_model_calls: int | None = None
    calls: int = 0
    exhausted: bool = False
    repairs: list[dict[str, Any]] = field(default_factory=list)

    def spend(self, site: str) -> None:
        """Counts one model call, or raises once the run's ceiling is reached."""
        if self.max_model_calls is not None and self.calls >= self.max_model_calls:
            if not self.exhausted:
                logger.warning("Run reached its ceiling of %d model calls (next: %s)", self.max_model_calls, site)
                metrics.llm_budget_exhausted.inc()
            self.exhausted = True
            raise RunBudgetExceeded(f"model call ceiling of {self.max_model_calls} reached")
        self.calls += 1


_current: ContextVar[RunContext | None] = ContextVar("run_context", default=None)


def current() -> RunContext | None:
    return _current.get()


def start_run(max_model_calls: int | None = None) -> tuple[RunContext, Token]:
    context = RunContext(max_model_calls=max_model_calls)
    return context, _current.set(context)


def end_run(token: Token) -> None:
    _current.reset(token)


@contextmanager
def run_context(max_model_calls: int | None = None) -> Iterator[RunContext]:
    context, token = start_run(max_model_calls)
    try:
        yield context
    finally:
        end_run(token)


def spend_model_call(site: str) -> None:
    """Outside a run (an on-demand check, a script) there is no ceiling."""
    context = _current.get()
    if context is not None:
        context.spend(site)


def record_repair(entry: dict[str, Any]) -> None:
    context = _current.get()
    if context is not None and len(context.repairs) < _MAX_REPAIRS:
        context.repairs.append(entry)
