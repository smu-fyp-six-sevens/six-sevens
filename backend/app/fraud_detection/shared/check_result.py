"""The base of every check's result."""

from __future__ import annotations

from pydantic import BaseModel


class CheckResult(BaseModel):
    """Whether the check could run on this document at all, and if not, why."""

    unable_to_assess: bool
    unable_to_assess_reason: str | None = None
