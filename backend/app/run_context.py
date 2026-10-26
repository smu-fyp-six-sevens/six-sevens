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
    # Tokens (input, cached or not, plus output) the run may use. Checked before each call against what finished
    # calls used, so calls already in flight can take a run past it: a soft cap.
    max_tokens: int | None = None
    calls: int = 0
    tokens: int = 0
    exhausted: bool = False
    exhausted_by: str | None = None  # "model calls" or "tokens": which limit stopped the run
    repairs: list[dict[str, Any]] = field(default_factory=list)

    def spend(self, site: str) -> None:
        """Counts one model call, or raises once the run's call ceiling or token budget is reached."""
        if self.max_model_calls is not None and self.calls >= self.max_model_calls:
            if not self.exhausted:
                logger.warning("Run reached its ceiling of %d model calls (next: %s)", self.max_model_calls, site)
                metrics.llm_budget_exhausted.inc()
                self.exhausted_by = "model calls"
            self.exhausted = True
            raise RunBudgetExceeded(f"model call ceiling of {self.max_model_calls} reached")
        if self.max_tokens is not None and self.tokens >= self.max_tokens:
            if not self.exhausted:
                logger.warning(
                    "Run reached its token budget of %d with %d used (next: %s)", self.max_tokens, self.tokens, site,
                )
                metrics.llm_token_budget_reached.inc()
                self.exhausted_by = "tokens"
            self.exhausted = True
            raise RunBudgetExceeded(f"token budget of {self.max_tokens:,} reached ({self.tokens:,} used)")
        self.calls += 1

    def limit_reached(self) -> str:
        """The limit that stopped the run, as a report says it."""
        if self.exhausted_by == "tokens":
            return f"its token budget of {self.max_tokens:,} tokens"
        return f"its limit of {self.max_model_calls} model calls"


_current: ContextVar[RunContext | None] = ContextVar("run_context", default=None)


def current() -> RunContext | None:
    return _current.get()


def start_run(max_model_calls: int | None = None, max_tokens: int | None = None) -> tuple[RunContext, Token]:
    context = RunContext(max_model_calls=max_model_calls, max_tokens=max_tokens)
    return context, _current.set(context)


def end_run(token: Token) -> None:
    _current.reset(token)


@contextmanager
def run_context(max_model_calls: int | None = None, max_tokens: int | None = None) -> Iterator[RunContext]:
    context, token = start_run(max_model_calls, max_tokens)
    try:
        yield context
    finally:
        end_run(token)


def spend_model_call(site: str) -> None:
    """Outside a run (an on-demand check, a script) there is no ceiling."""
    context = _current.get()
    if context is not None:
        context.spend(site)


def add_model_tokens(tokens: int) -> None:
    """A finished call's tokens, counted into the run so the next spend_model_call can stop at the budget."""
    context = _current.get()
    if context is not None:
        context.tokens += tokens


def record_repair(entry: dict[str, Any]) -> None:
    context = _current.get()
    if context is not None and len(context.repairs) < _MAX_REPAIRS:
        context.repairs.append(entry)
