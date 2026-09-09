from __future__ import annotations

from collections import defaultdict
from typing import Optional

from . import case_repository, document_repository
from .models import (
    REQUIRED_DOCUMENT_TYPES,
    CaseDocumentsStatus,
    CaseSummary,
    ClassificationOutcome,
    DocumentRecord,
    DocumentStatus,
)
from .monitoring import is_stuck


def compute_documents_status(documents: list[DocumentRecord]) -> CaseDocumentsStatus:
    """
    Worst-first rollup across a case's documents. A single stuck document
    outranks any number of analyzed ones (matches the reasoning: a stuck
    document is the one that actually needs someone's attention).
    """
    if not documents:
        return CaseDocumentsStatus.NO_DOCUMENTS
    if any(is_stuck(doc) for doc in documents):
        return CaseDocumentsStatus.STUCK
    if any(doc.status == DocumentStatus.FAILED for doc in documents):
        return CaseDocumentsStatus.FAILED
    if all(doc.status == DocumentStatus.ANALYZED for doc in documents):
        return CaseDocumentsStatus.ANALYZED
    return CaseDocumentsStatus.IN_PROGRESS


def compute_requirement_coverage(documents: list[DocumentRecord]) -> tuple[list[str], list[str]]:
    """
    A requirement counts as fulfilled only by a document whose
    classification was actually ACCEPTED — one sitting in manual review
    hasn't been confirmed to be what it claims, so it shouldn't retire a
    checklist item yet. A single document can fulfil more than one
    requirement (see DocumentRecord.fulfilled_requirements), so this is a
    union across all of a case's accepted documents, not a 1:1 count.

    Returns (fulfilled, missing) against the fixed REQUIRED_DOCUMENT_TYPES
    checklist, both in checklist order.
    """
    fulfilled: set[str] = set()
    for doc in documents:
        if doc.classification_outcome == ClassificationOutcome.ACCEPTED:
            fulfilled.update(doc.fulfilled_requirements)

    return (
        [req for req in REQUIRED_DOCUMENT_TYPES if req in fulfilled],
        [req for req in REQUIRED_DOCUMENT_TYPES if req not in fulfilled],
    )


def _build_summary(case, documents: list[DocumentRecord]) -> CaseSummary:
    fulfilled, missing = compute_requirement_coverage(documents)
    data = case.model_dump()
    data["recommended_additional_documents"] = missing
    return CaseSummary(
        **data,
        documents_status=compute_documents_status(documents),
        fulfilled_requirements=fulfilled,
    )


def get_case_summary(case_id: str) -> Optional[CaseSummary]:
    case = case_repository.get_case(case_id)
    if case is None:
        return None
    documents = document_repository.list_documents(case_id=case_id)
    return _build_summary(case, documents)


def list_case_summaries() -> list[CaseSummary]:
    """
    Two queries total (all cases, all documents), not one per case — avoids
    N+1 as the case count grows. Fine at current scale; would need
    pagination/filtering if that stops being true.
    """
    cases = case_repository.list_cases()
    documents_by_case: dict[str, list[DocumentRecord]] = defaultdict(list)
    for doc in document_repository.list_documents():
        if doc.case_id is not None:
            documents_by_case[doc.case_id].append(doc)

    return [_build_summary(case, documents_by_case.get(case.id, [])) for case in cases]
