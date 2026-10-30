"""Result models for the numerical checks."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel

from ..shared.check_result import CheckResult


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
    # True when the failing value has low extraction confidence: the failure
    # might be an extraction artefact rather than a genuine anomaly.
    needs_review: bool = False
    severity_note: str | None = None
    summary: str
    # Other fields the same value was extracted from (a number printed in both nric_number and a
    # shareholder table). needs_review uses the most confident of them.
    also_fields: list[str] = []


class SkippedCheck(BaseModel):
    check_code: str
    reason: str


class BenfordDigit(BaseModel):
    digit: int
    count: int
    observed_percentage: float
    expected_percentage: float


class BenfordAnalysis(CheckResult):
    sample_size: int
    digits: list[BenfordDigit]
    mean_absolute_deviation: float | None = None
    chi_square: float | None = None
    chi_square_critical: float | None = None
    conforms: bool | None = None


class NumericalValidationResult(CheckResult):
    """
    Response for GET /documents/{id}/detection/numerical-validation. A pure
    compute: no findings are stored, only a step entry on the document's
    trace (routers/detection.py).

    Two independent Benford passes over transaction amounts (all amounts,
    and amounts with likely bank fees excluded) are named benford /
    benford_excluding_fees. Without a transaction list, benford says why it
    couldn't run (unable_to_assess) and benford_excluding_fees is None.
    """

    document_id: str
    document_type: str | None = None
    values_analysed: int
    findings: list[NumericalFinding]
    skipped_checks: list[SkippedCheck]
    benford: BenfordAnalysis
    benford_excluding_fees: BenfordAnalysis | None = None
    # Filled by the binder-driven endpoint; empty on the reference path.
    breakdown: list[dict] = []
    unassigned: list[dict] = []
    bindings_applied: list[dict] = []
    binding_issues: list[str] = []
    binding_error: str | None = None
