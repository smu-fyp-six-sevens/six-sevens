from __future__ import annotations

from collections import defaultdict
from typing import Optional

from . import case_repository, document_repository
from .models import CaseDocumentsStatus, CaseSummary, DocumentRecord, DocumentStatus
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


def get_case_summary(case_id: str) -> Optional[CaseSummary]:
    case = case_repository.get_case(case_id)
    if case is None:
        return None
    documents = document_repository.list_documents(case_id=case_id)
    return CaseSummary(
        **case.model_dump(), documents_status=compute_documents_status(documents)
    )


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

    return [
        CaseSummary(
            **case.model_dump(),
            documents_status=compute_documents_status(documents_by_case.get(case.id, [])),
        )
        for case in cases
    ]
