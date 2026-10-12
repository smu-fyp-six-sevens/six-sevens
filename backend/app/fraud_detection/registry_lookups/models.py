"""Result types for the registry lookups."""

from __future__ import annotations

from pydantic import BaseModel

from ..numerical_validation.models import Severity, SkippedCheck


class RegistryFinding(BaseModel):
    check_code: str
    severity: Severity
    field: str | None = None
    stated: str | None = None
    expected: str | None = None
    # Which registry said so, e.g. "ACRA (data.gov.sg)", "OneMap (SLA)".
    source: str
    needs_review: bool = False
    severity_note: str | None = None
    summary: str
    # Other fields the same value was extracted from; needs_review uses the most confident of them.
    also_fields: list[str] = []


class RegistryResult(BaseModel):
    document_id: str
    document_type: str | None = None
    findings: list[RegistryFinding]
    skipped_checks: list[SkippedCheck]
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None
