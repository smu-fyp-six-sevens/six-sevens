from __future__ import annotations

from collections import defaultdict
from typing import Optional

from . import case_repository, document_repository
from .mapper import (
    compute_id_document_identity_coverage,
    compute_id_document_identity_progress,
    compute_id_document_missing_side,
    compute_identity_proof_of_address_coverage,
    compute_identity_proof_of_address_progress,
    compute_unmatched_proof_of_address_names,
)
from .models import (
    REQUIRED_DOCUMENT_TYPES,
    CaseDocumentsStatus,
    CaseSummary,
    ClassificationOutcome,
    DocumentRecord,
    DocumentStatus,
    DocumentType,
    IdDocumentCoverage,
    ProofOfAddressCoverage,
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
    requirement (see DocumentRecord.fulfilled_requirements), so most
    requirements are a union across all of a case's accepted documents, not
    a 1:1 count — proof_of_address is the one exception, requiring coverage
    per identity rather than just once anywhere in the case (see
    compute_identity_proof_of_address_coverage).

    Returns (fulfilled, missing) against the fixed REQUIRED_DOCUMENT_TYPES
    checklist, both in checklist order.
    """
    fulfilled: set[str] = set()
    for doc in documents:
        if doc.classification_outcome == ClassificationOutcome.ACCEPTED:
            fulfilled.update(doc.fulfilled_requirements)

    # Overrides whatever individual documents self-reported for this one
    # requirement — see compute_identity_proof_of_address_coverage for why
    # proof_of_address can't just be unioned like every other requirement.
    proof_of_address = DocumentType.PROOF_OF_ADDRESS.value
    if compute_identity_proof_of_address_coverage(documents):
        fulfilled.add(proof_of_address)
    else:
        fulfilled.discard(proof_of_address)

    # Same idea for id_document: it requires every identified individual's
    # own ID to be complete (front+back, matched by id_number — not just
    # any front and any back existing somewhere in the case), mirroring
    # proof_of_address's own per-identity requirement above. See
    # compute_id_document_identity_coverage.
    id_document = DocumentType.ID_DOCUMENT.value
    if compute_id_document_identity_coverage(documents):
        fulfilled.add(id_document)
    else:
        fulfilled.discard(id_document)

    return (
        [req for req in REQUIRED_DOCUMENT_TYPES if req in fulfilled],
        [req for req in REQUIRED_DOCUMENT_TYPES if req not in fulfilled],
    )


def sync_submission_finalized(case_id: str) -> None:
    """
    Persists whether this case's requirement checklist is currently fully
    satisfied. Called right after every classification result is recorded
    (a document's first classification, or a /retry reclassification —
    either can change coverage), rather than only computing this at read
    time like documents_status. Reversible by design: recomputes from
    scratch every call, so if coverage has dropped back below 100% since
    it was last synced (e.g. a retry reclassifies a document away from a
    requirement nothing else covers), this correctly flips back to
    False/None rather than leaving a stale True in place.
    """
    documents = document_repository.list_documents(case_id=case_id)
    _, missing = compute_requirement_coverage(documents)
    case_repository.set_submission_finalized(case_id, finalized=not missing)


def build_summary(case, documents: list[DocumentRecord]) -> CaseSummary:
    """
    Public (not module-private) because routers/ws.py needs it too: it
    already has to fetch a case's documents itself (to enrich them with
    extracted_fields before pushing them over the socket), so calling
    get_case_summary there would fetch the same document list a second
    time. Calling this directly with the documents it already has avoids
    that duplicate query on every poll tick.
    """
    fulfilled, missing = compute_requirement_coverage(documents)
    covered, total = compute_identity_proof_of_address_progress(documents)
    unmatched_names = compute_unmatched_proof_of_address_names(documents)
    id_covered, id_total = compute_id_document_identity_progress(documents)
    id_missing_side = compute_id_document_missing_side(documents)
    data = case.model_dump()
    data["recommended_additional_documents"] = missing
    return CaseSummary(
        **data,
        documents_status=compute_documents_status(documents),
        fulfilled_requirements=fulfilled,
        proof_of_address_coverage=ProofOfAddressCoverage(
            covered=covered, total=total, unmatched_names=unmatched_names
        ),
        id_document_coverage=IdDocumentCoverage(
            covered=id_covered, total=id_total, missing_side=id_missing_side
        ),
    )


def get_case_summary(case_id: str) -> Optional[CaseSummary]:
    case = case_repository.get_case(case_id)
    if case is None:
        return None
    documents = document_repository.list_documents(case_id=case_id)
    return build_summary(case, documents)


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

    return [build_summary(case, documents_by_case.get(case.id, [])) for case in cases]
