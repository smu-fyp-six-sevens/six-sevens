"""Runs the registry lookups that apply to one document."""

from __future__ import annotations

import asyncio

from ..font_consistency import ExtractedField
from ..numerical_validation.common import _apply_needs_review, _confidence_by_name, _fields_by_name
from .acra import check_entity
from .addresses import check_addresses
from .models import RegistryResult

# Document types that never carry a UEN, company number or tax reference (their extraction
# schemas have none), so there is no entity to look up in ACRA. A bank statement's
# registration number is the bank's own, checked against the bank's published one elsewhere.
NO_UEN_TYPES = frozenset({"passport", "nric", "payslip", "bank_statement", "proof_of_address", "credit_report"})
# Document types with no address on them.
NO_ADDRESS_TYPES = frozenset({"passport", "certificate_of_incorporation"})


async def verify_against_registries(
    document_id: str, extracted_fields: list[ExtractedField] | None, document_type: str | None,
) -> RegistryResult:
    fields = _fields_by_name(extracted_fields)
    lookups = []
    if document_type not in NO_UEN_TYPES:
        lookups.append(check_entity(fields, document_type))
    if document_type not in NO_ADDRESS_TYPES:
        lookups.append(check_addresses(fields, document_type))
    if not lookups:
        kind = (document_type or "document").replace("_", " ")
        return RegistryResult(
            document_id=document_id,
            document_type=document_type,
            findings=[],
            skipped_checks=[],
            unable_to_assess=True,
            unable_to_assess_reason=f"Nothing to check: a {kind} has no UEN or address to look up in a public registry",
        )
    outcomes = await asyncio.gather(*lookups)

    findings = _apply_needs_review(
        [f for found, _ in outcomes for f in found], _confidence_by_name(extracted_fields),
    )
    skipped = [s for _, skips in outcomes for s in skips]
    # A lookup ran if it produced a finding or wasn't skipped.
    nothing_ran = not any(found or not skips for found, skips in outcomes)
    return RegistryResult(
        document_id=document_id,
        document_type=document_type,
        findings=findings,
        skipped_checks=skipped,
        unable_to_assess=nothing_ran,
        unable_to_assess_reason="; ".join(s.reason for s in skipped) if nothing_ran else None,
    )
