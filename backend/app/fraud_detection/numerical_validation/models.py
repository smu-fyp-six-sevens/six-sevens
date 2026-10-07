"""Result models for the numerical checks."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel


Severity = Literal["impossible", "inconsistent", "implausible", "signal"]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class NumericalFinding(BaseModel):
    check_code: str
    severity: Severity
    field: str | None = None
    stated: str | None = None
    expected: str | None = None
    # True when the failing value has low extraction confidence, or its raw
    # string can't be found anywhere in the document's own text — the
    # failure might be an extraction artefact rather than a genuine anomaly.
    needs_review: bool = False
    severity_note: str | None = None
    summary: str


class SkippedCheck(BaseModel):
    check_code: str
    reason: str


class BenfordDigit(BaseModel):
    digit: int
    count: int
    observed_percentage: float
    expected_percentage: float


class BenfordAnalysis(BaseModel):
    sample_size: int
    digits: list[BenfordDigit]
    mean_absolute_deviation: float | None = None
    chi_square: float | None = None
    chi_square_critical: float | None = None
    conforms: bool | None = None
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


class NumericalValidationResult(BaseModel):
    """
    Response for GET /documents/{id}/detection/numerical-validation.
    Nothing is persisted — a pure read/compute, like the spelling and
    layout checks.

    Two independent Benford passes over transaction amounts (all amounts,
    and amounts with likely bank fees excluded) are named benford /
    benford_excluding_fees; both are None when the document type has no
    transaction list to run them on.
    """

    document_id: str
    document_type: str | None = None
    values_analysed: int
    findings: list[NumericalFinding]
    skipped_checks: list[SkippedCheck]
    benford: BenfordAnalysis
    benford_excluding_fees: BenfordAnalysis | None = None
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None
    # Filled by the binder-driven endpoint; empty on the reference path.
    breakdown: list[dict] = []
    unassigned: list[dict] = []
    bindings_applied: list[dict] = []
    binding_issues: list[str] = []
    binding_error: str | None = None
