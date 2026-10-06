"""The numerical checks, registered from their signatures.

A check is a function taking keyword-only arguments. Each argument is an input:

* a plain name (e.g. `loan_amount`) is the document field with that name, or one bound to it
* `loan_amount: Num` is converted to a number (None if it isn't one)
* a row role (e.g. `depreciation_line`) is one row of an income statement, passed as the row dict
* `fields` is every field of the document (for checks that scan the whole document)
* `today` is the run's date

An argument without a default is required: if it's missing, the runner records a skip and the
check isn't called. An argument with a default of None is optional.

@check(code=..., applies=...) takes the document types the check runs on (None = every type).
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import Any, Callable

# Marks an argument that the runner converts to a number before the check sees it.
Num = float

ROW_ROLE_NAMES = frozenset({
    "depreciation_line", "profit_before_tax_line", "tax_expense_line", "finance_costs_line", "cost_of_sales_line",
})


@dataclass(frozen=True)
class Param:
    name: str
    numeric: bool
    required: bool
    kind: str  # "field", "row", "fields" or "today"


@dataclass(frozen=True)
class Check:
    code: str
    fn: Callable[..., Any]
    applies: tuple[str, ...] | None
    params: tuple[Param, ...]

    @property
    def roles(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.kind == "field")

    @property
    def rows(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.params if p.kind == "row")

    @property
    def scan_all(self) -> bool:
        return any(p.kind == "fields" for p in self.params)


CHECKS: list[Check] = []


def _param_kind(name: str) -> str:
    if name == "fields":
        return "fields"
    if name == "today":
        return "today"
    if name in ROW_ROLE_NAMES:
        return "row"
    return "field"


def check(*, code: str, applies: tuple[str, ...] | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def register(fn: Callable[..., Any]) -> Callable[..., Any]:
        params = []
        for param in inspect.signature(fn).parameters.values():
            annotation = param.annotation if isinstance(param.annotation, str) else getattr(param.annotation, "__name__", "")
            params.append(Param(
                name=param.name,
                numeric=annotation == "Num",
                required=param.default is inspect.Parameter.empty,
                kind=_param_kind(param.name),
            ))
        CHECKS.append(Check(code=code, fn=fn, applies=applies, params=tuple(params)))
        return fn

    return register


def checks_for(document_type: str | None) -> list[Check]:
    """Every check that applies to this document type, in registration order."""
    return [c for c in CHECKS if c.applies is None or document_type in c.applies]
