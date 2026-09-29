from __future__ import annotations

from collections import defaultdict
from typing import Optional

from . import case_repository, document_repository, extracted_fields_repository
from .case_match import CaseMatchResult, DocumentCaseMatch, check_case_match
from .config import settings
from .mapper import (
    compute_id_document_identity_coverage,
    compute_id_document_identity_progress,
    compute_id_document_missing_side,
    compute_identity_proof_of_address_coverage,
    compute_identity_proof_of_address_progress,
    compute_unmatched_proof_of_address_names,
)
from .models import (
    ADDITIONAL_DOCUMENT_TYPES,
    REQUIRED_DOCUMENT_TYPES,
    CaseDocumentsStatus,
    CaseRecord,
    CaseSummary,
    DocumentRecord,
    DocumentStatus,
    DocumentType,
    IdDocumentCoverage,
    IDENTITY_DOCUMENT_REQUIREMENT,
    ProofOfAddressCoverage,
    RoutingStatus,
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
        if doc.routing_status == RoutingStatus.ACCEPTED:
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
    # own ID to be complete (front+back, matched by nric_number — not just
    # any front and any back existing somewhere in the case), mirroring
    # proof_of_address's own per-identity requirement above. See
    # compute_id_document_identity_coverage. id_document isn't a real
    # DocumentType (passport/nric are the real classifiable types — see
    # models.IDENTITY_DOCUMENT_TYPES), so it's not DocumentType.X.value
    # like proof_of_address above, just the shared requirement string.
    id_document = IDENTITY_DOCUMENT_REQUIREMENT
    if compute_id_document_identity_coverage(documents):
        fulfilled.add(id_document)
    else:
        fulfilled.discard(id_document)

    # Additional items are reported as fulfilled (so the UI can tick them) but
    # never as missing, so they can't block submission_finalized.
    return (
        [req for req in [*REQUIRED_DOCUMENT_TYPES, *ADDITIONAL_DOCUMENT_TYPES] if req in fulfilled],
        [req for req in REQUIRED_DOCUMENT_TYPES if req not in fulfilled],
    )


def get_case(case_id: str) -> Optional[CaseRecord]:
    return case_repository.get_case(case_id)


def create_case(company_name: Optional[str] = None) -> CaseRecord:
    return case_repository.create_case(company_name)


def update_case(case_id: str, company_name: str) -> Optional[CaseRecord]:
    return case_repository.update_case(case_id, company_name)


def delete_case(case_id: str) -> None:
    """
    Removes a case and every document that belongs to it. Documents go
    first (via document_repository.remove_documents, which batch-clears
    their extracted-field rows and stored files) so nothing is left orphaned
    if the case delete were to run first and fail partway through.
    """
    documents = document_repository.list_documents(case_id=case_id)
    document_repository.remove_documents(documents)
    case_repository.delete_case(case_id)


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


def _enrich_with_extracted_fields(documents: list[DocumentRecord]) -> list[DocumentRecord]:
    fields_by_document = extracted_fields_repository.get_extracted_fields_for_documents(
        [document.id for document in documents]
    )
    for document in documents:
        fields = fields_by_document.get(document.id)
        if fields:
            document.extracted_fields = {
                field.field_name: {"value": field.field_value, "confidence": field.confidence}
                for field in fields
            }
    return documents


def _fetch_case_and_documents(case_id: str) -> tuple[Optional[CaseRecord], list[DocumentRecord]]:
    """
    The "case + its documents" fetch every case-match/snapshot assembly
    needs, in one place — see get_case_match_for_case, build_case_snapshot.
    """
    case = case_repository.get_case(case_id)
    if case is None:
        return None, []
    return case, document_repository.list_documents(case_id=case_id)


def get_case_match_for_case(case_id: str) -> Optional[CaseMatchResult]:
    """Whether each of the case's documents belongs to the case's company and individuals."""
    case, documents = _fetch_case_and_documents(case_id)
    if case is None:
        return None
    return check_case_match(case, documents)


def get_case_match_for_document(document_id: str) -> Optional[DocumentCaseMatch]:
    """
    One document's entry from its case's match check. The document is
    appended to its case's document list if the fetch didn't happen to
    include it (defensive, not expected in practice) so check_case_match
    always has something to report on it.
    """
    document = document_repository.get_document(document_id)
    if document is None or document.case_id is None:
        return None
    case, documents = _fetch_case_and_documents(document.case_id)
    if case is None:
        return None
    if all(d.id != document.id for d in documents):
        documents.append(document)
    result = check_case_match(case, documents)
    return next((match for match in result.documents if match.document_id == document_id), None)


def build_case_snapshot(case_id: str) -> Optional[dict]:
    """
    Assembles the exact payload pushed over the case status WebSocket (see
    routers/ws.py) — both the immediate send on connect/refresh and the
    Realtime-triggered push in realtime_listener.py call this, so the two
    paths can't drift out of sync with each other. Runs on a worker thread
    (supabase-py's sync client) wherever it's called from.
    """
    case, documents = _fetch_case_and_documents(case_id)
    if case is None:
        return None
    summary = build_summary(case, documents)
    # Before enrichment: that rewrites extracted_fields in place into
    # {"value", "confidence"} wrappers, while case_match (and mapper.py, via
    # build_summary above) reads the plain values document_repository
    # already populated from the extracted_fields table.
    case_match = check_case_match(case, documents)
    enriched = _enrich_with_extracted_fields(documents)
    return {
        "type": "snapshot",
        "case": summary.model_dump(mode="json"),
        "case_match": case_match.model_dump(mode="json"),
        "documents": [document.model_dump(mode="json") for document in enriched],
    }


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
    cases = case_repository.list_cases(include_dev=not settings.IS_CLOUD_DEPLOYMENT)
    documents_by_case: dict[str, list[DocumentRecord]] = defaultdict(list)
    for doc in document_repository.list_documents():
        if doc.case_id is not None:
            documents_by_case[doc.case_id].append(doc)

    return [build_summary(case, documents_by_case.get(case.id, [])) for case in cases]
