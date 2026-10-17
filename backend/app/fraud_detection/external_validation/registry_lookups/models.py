"""Result types for the registry lookups."""

from __future__ import annotations

from pydantic import BaseModel

from ...numerical_validation.models import NumericalFinding, SkippedCheck


class RegistryFinding(NumericalFinding):
    """A numerical finding that also names the outside reference that said so."""

    # e.g. "ACRA (data.gov.sg)", "OneMap (SLA)", "CPF Board contribution rate tables".
    source: str


class RegistryResult(BaseModel):
    document_id: str
    document_type: str | None = None
    findings: list[RegistryFinding]
    skipped_checks: list[SkippedCheck]
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None
