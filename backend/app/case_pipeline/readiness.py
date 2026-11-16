"""
When a case can be analysed: every approved document scanned, and, for a case that enforces the checklist,
every required document approved. See HOW_IT_WORKS.md, Step 6.
"""

from __future__ import annotations

from ..mapper import compute_requirement_coverage
from ..models import APPROVED_STATUSES, CaseRecord, DocumentRecord, DocumentStatus, skips_detection


def is_ready_for_analysis(documents: list[DocumentRecord]) -> bool:
    """
    The case has at least one approved document and every approved document
    has finished detection. Documents not yet approved, rejected or cancelled
    don't hold it back. A document whose detection crashed sits at approved
    until it's retried, so it blocks.
    """
    approved = [d for d in documents if d.status in APPROVED_STATUSES]
    # A document with no type is never scanned (models.skips_detection).
    return bool(approved) and all(d.status == DocumentStatus.DETECTED or skips_detection(d) for d in approved)


def unapproved_requirements(case: CaseRecord, documents: list[DocumentRecord]) -> list[str]:
    """
    The required checklist items no approved document covers, in checklist order: what the case analysis waits
    for. Only approved documents count, since only they are analysed; one uploaded but not yet approved doesn't.
    Empty when the case doesn't enforce the checklist (cases.checklist_enforced). `documents` must carry their
    extracted fields: proof of address and ID coverage are matched by name.
    """
    if not case.checklist_enforced:
        return []
    return compute_requirement_coverage([d for d in documents if d.status in APPROVED_STATUSES])[1]
