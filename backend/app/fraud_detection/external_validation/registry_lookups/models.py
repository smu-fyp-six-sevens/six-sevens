"""Result types for the registry lookups."""

from __future__ import annotations

from ...shared.check_result import CheckResult
from ...numerical_validation.models import NumericalFinding, SkippedCheck


class RegistryFinding(NumericalFinding):
    """A numerical finding that also names the outside reference that said so."""

    # e.g. "ACRA (data.gov.sg)", "OneMap (SLA)", "CPF Board contribution rate tables".
    source: str


class RegistryResult(CheckResult):
    document_id: str
    document_type: str | None = None
    findings: list[RegistryFinding]
    skipped_checks: list[SkippedCheck]
