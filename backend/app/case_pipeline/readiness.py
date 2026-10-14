"""
Per-case analysis (cross-document validation and the risk outlook), run on
demand by an officer. See CROSS_DOCUMENT_AND_RISK_PLAN.md.
"""

from __future__ import annotations

from ..models import APPROVED_STATUSES, DocumentRecord, DocumentStatus

def is_ready_for_analysis(documents: list[DocumentRecord]) -> bool:
    """
    The case has at least one approved document and every approved document
    has finished detection. Documents not yet approved, rejected or cancelled
    don't hold it back. A document whose detection crashed sits at approved
    until it's retried, so it blocks.
    """
    approved = [d for d in documents if d.status in APPROVED_STATUSES]
    return bool(approved) and all(d.status == DocumentStatus.DETECTED for d in approved)
