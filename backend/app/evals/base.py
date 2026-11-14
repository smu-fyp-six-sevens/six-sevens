"""
The shape every sensibility check shares, and how a run's own steps are picked out of its trace.

A check reads only what the run already recorded. It never changes what the run decided: a guard in the agent code
stops a bad move, a check records that the agent tried it (CLOUD_ARCHITECTURE_PLAN.md §13).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any, Literal

# Raised when a check changes, so older traces can still be read for what they meant.
EVALS_VERSION = 1

Result = Literal["pass", "warn", "fail", "n/a"]
Kind = Literal["output", "trajectory"]


@dataclass(frozen=True)
class Check:
    agent: str
    check: str
    kind: Kind
    result: Result
    reason: str = ""
    # The trace span the result belongs to, when the agent has more than one (each specialist's review).
    span: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in (None, "")}


def judged(
    agent: str, check: str, kind: Kind, ok: bool | None, problem: str = "", *,
    broken: Result = "warn", span: str | None = None,
) -> Check:
    """pass when ok, `broken` (with the problem) when not, n/a when the check doesn't apply (ok is None)."""
    if ok is None:
        return Check(agent, check, kind, "n/a", span=span)
    return Check(agent, check, kind, "pass" if ok else broken, "" if ok else problem, span=span)


def run_steps(steps: Iterable[dict[str, Any]], root_id: str | None) -> list[dict[str, Any]]:
    """The steps recorded inside span `root_id`, at any depth; every step when there is no root."""
    steps = list(steps)
    if root_id is None:
        return steps
    parents = {s.get("id"): s.get("parent") for s in steps if s.get("step") == "span"}

    def inside(span_id: Any) -> bool:
        seen: set[Any] = set()
        while span_id is not None and span_id not in seen:
            if span_id == root_id:
                return True
            seen.add(span_id)
            span_id = parents.get(span_id)
        return False

    return [s for s in steps if inside(s.get("id") if s.get("step") == "span" else s.get("in_span"))]


def of(steps: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [s for s in steps if s.get("step") == name]


def last(steps: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    found = of(steps, name)
    return found[-1] if found else None


def spans(steps: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
    return [s for s in steps if s.get("step") == "span" and s.get("name") == name]


def names(items: Iterable[Any]) -> str:
    return ", ".join(str(i) for i in items)
