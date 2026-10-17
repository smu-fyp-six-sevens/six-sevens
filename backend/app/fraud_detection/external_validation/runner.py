"""
External validation: a document's facts against outside references.

Three kinds of check run here, all chosen by the document type alone:
* live registry lookups: ACRA, OneMap and HDB (registry_lookups/)
* static reference tables: CPF rates, GST rates, IRAS tax rules, bank identifiers
  (the numerical registry's "external" group)
* GIRO dates against the public holiday table (giro_dates.py)

A calculation on the document's own figures is a numerical check instead.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ..font_consistency import ExtractedField
from .giro import GIRO_DOCUMENT_TYPES, giro_on_closed_days
from ..numerical_validation.models import SkippedCheck
from ..numerical_validation.registry import Check, checks_for
from ..numerical_validation.runner import Binding, run_numerical_checks
from .registry_lookups import public_holidays, verify_against_registries
from .registry_lookups.models import RegistryFinding

GROUP = "external"
GIRO_CODE = "BS.GIRO_BUSINESS_DAY"


class ExternalValidationResult(BaseModel):
    document_id: str
    document_type: str | None = None
    findings: list[RegistryFinding]
    skipped_checks: list[SkippedCheck]
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None
    # Which fields were bound to which check roles, for the static checks.
    bindings_applied: list[dict] = []
    binding_issues: list[str] = []
    binding_error: str | None = None


_FALLBACK_SOURCE = "a published reference table"


def _source_of(code: str, checks: list[Check]) -> str:
    """The reference a finding came from: its own check's source, or the one source of its code family
    (a notice-of-assessment check reports NOA.REBATE, NOA.AMENDMENT and so on under one registered check)."""
    for entry in checks:
        if entry.code == code and entry.source:
            return entry.source
    family = {entry.source for entry in checks if entry.source and entry.code.split(".")[0] == code.split(".")[0]}
    return family.pop() if len(family) == 1 else _FALLBACK_SOURCE


async def _giro_closed_days(
    document_type: str | None, values: dict[str, Any], bindings: list[Binding],
) -> tuple[list[RegistryFinding], list[SkippedCheck]]:
    if document_type not in GIRO_DOCUMENT_TYPES:
        return [], []
    bound = {b.role: b.field for b in bindings if b.row is None}
    transactions = values.get(bound.get("transactions", "transactions"))
    if not transactions:
        return [], [SkippedCheck(check_code=GIRO_CODE, reason="Required field(s) not extracted: transactions")]

    holidays = await public_holidays()
    scan = giro_on_closed_days(transactions, holidays)
    findings = [
        RegistryFinding(
            check_code=GIRO_CODE,
            severity="inconsistent",
            source=holidays.source,
            field=f"transactions[{hit.index}].date",
            stated=hit.day.isoformat(),
            expected="a bank business day",
            summary=(
                f"Transaction {hit.index + 1} ({hit.description[:60]}) is a GIRO payment dated "
                f"{hit.day.isoformat()}, {hit.reason}. No Singapore bank processes GIRO on Sundays or public holidays."
            ),
        )
        for hit in scan.closed_days
    ]
    skipped = []
    if scan.unknown_holiday_years:
        years = ", ".join(str(y) for y in scan.unknown_holiday_years)
        skipped.append(SkippedCheck(
            check_code=GIRO_CODE,
            reason=f"Singapore's public holidays for {years} aren't known from {holidays.source}, "
                   "so GIRO dates in those years were only checked against Sundays",
        ))
    return findings, skipped


async def run_external_validation(
    document_id: str,
    document_type: str | None,
    fields: list[ExtractedField],
    bindings: list[Binding],
) -> ExternalValidationResult:
    """Runs every external check that applies to document_type. Which ones apply is fixed by the type."""
    values = {f.field_name: f.field_value for f in fields}

    live = await verify_against_registries(document_id, fields, document_type)
    static = run_numerical_checks(document_type, fields, bindings, group=GROUP)
    giro_findings, giro_skipped = await _giro_closed_days(document_type, values, bindings)

    findings: list[RegistryFinding] = [*live.findings]
    applicable = checks_for(document_type, GROUP)
    findings += [
        RegistryFinding(**finding.model_dump(), source=_source_of(finding.check_code, applicable))
        for finding in static.findings
    ]
    findings += giro_findings
    skipped = [*live.skipped_checks, *static.skipped, *giro_skipped]

    # Nothing ran when the type has no live lookup and every static check was skipped for missing inputs.
    skipped_static = {s.check_code for s in static.skipped}
    static_ran = any(c.code not in skipped_static for c in applicable)
    giro_ran = document_type in GIRO_DOCUMENT_TYPES and not any(s.check_code == GIRO_CODE for s in giro_skipped)
    nothing_ran = live.unable_to_assess and not static_ran and not giro_ran
    reason = None
    if nothing_ran:
        if not applicable and document_type not in GIRO_DOCUMENT_TYPES:
            reason = live.unable_to_assess_reason  # no external check exists for this type
        else:
            reasons = [live.unable_to_assess_reason, *(s.reason for s in static.skipped)]
            reason = "; ".join(dict.fromkeys(r for r in reasons if r))

    return ExternalValidationResult(
        document_id=document_id,
        document_type=document_type,
        findings=findings,
        skipped_checks=skipped,
        unable_to_assess=nothing_ran,
        unable_to_assess_reason=reason,
        bindings_applied=static.applied,
        binding_issues=static.issues,
    )
