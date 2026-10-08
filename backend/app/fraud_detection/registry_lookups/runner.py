"""Runs the registry lookups that apply to one document."""

from __future__ import annotations

import asyncio

from ..font_consistency import ExtractedField
from ..numerical_validation.common import _apply_needs_review, _confidence_by_name, _fields_by_name
from .acra import check_entity
from .addresses import check_addresses
from .models import RegistryResult


async def verify_against_registries(
    document_id: str, extracted_fields: list[ExtractedField] | None, document_type: str | None,
) -> RegistryResult:
    fields = _fields_by_name(extracted_fields)
    lookups = [check_entity(fields, document_type), check_addresses(fields, document_type)]
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
