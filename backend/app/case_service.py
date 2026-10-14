from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Optional

from . import case_repository, document_repository, extracted_fields_repository
from .case_match import CaseMatchResult, DocumentCaseMatch, check_case_match
from .case_pipeline import repository as analysis_repository, state as analysis_state
from .case_pipeline.models import CaseAnalysis
from .case_pipeline.readiness import is_ready_for_analysis
from .case_pipeline.risk import store as risk_store
from .case_pipeline.risk.case_risk import compute_case_risk
from .config import settings
from .langgraph import trace as langgraph_trace
from .langgraph.extraction_prompts import EXTRACTION_SCHEMAS

logger = logging.getLogger("classifier_pipeline.case_service")
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
    CaseAnalysisOverview,
    CaseDocumentsStatus,
    CaseRecord,
    CaseRisk,
    CaseSummary,
    DocumentRecord,
    DocumentStatus,
    DocumentType,
    IN_FLIGHT_STATUSES,
    IdDocumentCoverage,
    IDENTITY_DOCUMENT_REQUIREMENT,
    ProofOfAddressCoverage,
    RoutingStatus,
    counts_towards_checklist,
    is_classification_confirmed,
)
from .monitoring import is_stuck
from .case_pipeline.risk.supporting_documents import recommend_supporting_documents


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
    checklist item yet — and that a loan officer hasn't rejected (see
    models.counts_towards_checklist). A single document can fulfil more than one
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
        if counts_towards_checklist(doc):
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


# (reports by document_id, ids whose last detection run failed) — see
# langgraph_trace.get_detection_states.
DetectionStates = tuple[dict[str, dict[str, Any]], set[str]]


def _load_detection_states(documents: list[DocumentRecord]) -> Optional[DetectionStates]:
    """None when the trace table can't be read — the case still loads, just without a risk."""
    try:
        return langgraph_trace.get_detection_states([document.id for document in documents])
    except Exception:
        logger.exception("Failed to load detection reports for %d document(s)", len(documents))
        return None


def _load_analyses(case_ids: list[str]) -> dict[str, CaseAnalysis]:
    """Empty when the table can't be read: a case then simply shows no analysis."""
    try:
        return analysis_repository.get_latest_many(case_ids)
    except Exception:
        logger.exception("Failed to load case analyses for %d case(s)", len(case_ids))
        return {}


def _load_saved_risks(case_ids: list[str]) -> dict[str, CaseRisk]:
    """Empty when the saved scores can't be read: the list then computes each risk live, as before."""
    try:
        return risk_store.latest_many(case_ids)
    except Exception:
        logger.exception("Failed to load saved risk scores for %d case(s)", len(case_ids))
        return {}


def _save_risk(summary: CaseSummary) -> None:
    risk_store.save_if_changed(summary.id, summary.risk)


def build_summary(
    case,
    documents: list[DocumentRecord],
    detection_states: Optional[DetectionStates] = None,
    analyses: Optional[dict[str, CaseAnalysis]] = None,
    saved_risk: Optional[CaseRisk] = None,
) -> CaseSummary:
    """
    Public (not module-private) because routers/ws.py needs it too: it
    already has to fetch a case's documents itself (to enrich them with
    extracted_fields before pushing them over the socket), so calling
    get_case_summary there would fetch the same document list a second
    time. Calling this directly with the documents it already has avoids
    that duplicate query on every poll tick.

    risk is computed when detection_states is passed. saved_risk is the
    case's latest saved score, used as it is (no findings behind it) by the
    case list so it doesn't recompute every case.
    """
    fulfilled, missing = compute_requirement_coverage(documents)
    covered, total = compute_identity_proof_of_address_progress(documents)
    unmatched_names = compute_unmatched_proof_of_address_names(documents)
    id_covered, id_total = compute_id_document_identity_progress(documents)
    id_missing_side = compute_id_document_missing_side(documents)
    data = case.model_dump()
    data["recommended_additional_documents"] = missing
    risk: Optional[CaseRisk] = None
    overview: Optional[CaseAnalysisOverview] = None
    if saved_risk is not None:
        risk = saved_risk
        analysis = (analyses or {}).get(case.id)
        if analysis is not None:
            overview = CaseAnalysisOverview(
                run_id=analysis.run_id, status=analysis.status, stale=False,
                finished_at=analysis.finished_at, finding_count=len(analysis.findings),
            )
    elif detection_states is not None:
        reports, failed_ids = detection_states
        analysis = (analyses or {}).get(case.id)
        if analysis is not None:
            analysis = analysis_state.current_view(analysis, documents, reports)
            overview = CaseAnalysisOverview(
                run_id=analysis.run_id, status=analysis.status, stale=analysis.stale,
                finished_at=analysis.finished_at, finding_count=len(analysis.findings),
            )
        # A case that was never analysed (or whose analysis is stale) scores exactly as before.
        risk = compute_case_risk(documents, reports, failed_ids, analysis_state.usable_findings(analysis))
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
        risk=risk,
        supporting_document_recommendations=recommend_supporting_documents(documents),
        analysis_ready=is_ready_for_analysis(documents),
        analysis=overview,
    )


def is_empty_field_value(value) -> bool:
    """None, a blank string, or an empty list/dict: nothing was extracted."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


def has_extracted_information(extracted_fields) -> bool:
    """Whether Extraction (or the officer) produced at least one real value.
    A document with nothing extracted has nothing to verify, so it can't be
    approved."""
    return any(not is_empty_field_value(field.field_value) for field in extracted_fields)


def _is_individual_account(fields_by_name) -> bool:
    """A statement naming a person and no UEN is a personal account, the same
    rule compute_fulfilled_requirements uses for personal source of wealth."""
    def has_value(name: str) -> bool:
        field = fields_by_name.get(name)
        return field is not None and not is_empty_field_value(field.field_value)

    return has_value("account_holder_name") and not has_value("uen")


def missing_required_fields(document_type: str | None, extracted_fields) -> list[str]:
    """
    The document type's required schema fields that Extraction left absent
    or empty (see is_empty_field_value). Shared by GET /documents/{id}, the
    approve gate and the case snapshot, so the document page, the case
    overview and approval all agree on which documents need manual review.
    """
    schema = EXTRACTION_SCHEMAS.get(document_type or "", {})
    required_field_names = set(schema.get("required", []))
    empty_list_ok = set(schema.get("allow_empty", []))

    fields_by_name = {
        field.field_name: field
        for field in extracted_fields
    }

    if schema.get("required_when_individual") and _is_individual_account(fields_by_name):
        required_field_names = set(schema["required_when_individual"])

    if not required_field_names:
        return []

    return [
        field_name
        for field_name in sorted(required_field_names)
        if field_name not in fields_by_name
        or (
            is_empty_field_value(fields_by_name[field_name].field_value)
            and not (field_name in empty_list_ok and fields_by_name[field_name].field_value == [])
        )
    ]


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
        # Skipped while the pipeline is still running: Extraction hasn't
        # written anything yet, so every required field would read as
        # missing and the case overview would show Manual Review instead
        # of Pending.
        if document.status not in IN_FLIGHT_STATUSES:
            document.missing_required_fields = missing_required_fields(
                document.document_type, fields or []
            )
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
    detection_states = _load_detection_states(documents)
    summary = build_summary(case, documents, detection_states, _load_analyses([case_id]))
    _save_risk(summary)
    # Before enrichment: that rewrites extracted_fields in place into
    # {"value", "confidence"} wrappers, while case_match (and mapper.py, via
    # build_summary above) reads the plain values document_repository
    # already populated from the extracted_fields table.
    case_match = check_case_match(case, documents)
    enriched = _enrich_with_extracted_fields(documents)
    # has_detection_report isn't a documents-table column (detection
    # deliberately writes nothing there — see langgraph/detection/graph.py)
    # — it's computed here so the frontend can tell "approved, detection
    # still running" apart from "approved, detection done" without its own
    # poll loop; this snapshot already pushes over the case-status websocket.
    reports, failed_ids = detection_states or ({}, set())
    detected_ids = set(reports)
    document_dumps = []
    for document in enriched:
        dump = document.model_dump(mode="json")
        dump["has_detection_report"] = document.id in detected_ids
        dump["has_detection_error"] = document.id in failed_ids
        document_dumps.append(dump)
    return {
        "type": "snapshot",
        "case": summary.model_dump(mode="json"),
        "case_match": case_match.model_dump(mode="json"),
        "documents": document_dumps,
    }


def get_case_summary(case_id: str) -> Optional[CaseSummary]:
    case = case_repository.get_case(case_id)
    if case is None:
        return None
    documents = document_repository.list_documents(case_id=case_id)
    summary = build_summary(case, documents, _load_detection_states(documents), _load_analyses([case_id]))
    _save_risk(summary)
    return summary


def list_case_summaries() -> list[CaseSummary]:
    """
    Three queries total (all cases, all documents, their detection
    reports), not one per case — avoids N+1 as the case count grows. Fine at current scale; would need
    pagination/filtering if that stops being true.
    """
    cases = case_repository.list_cases(include_dev=not settings.IS_CLOUD_DEPLOYMENT)
    documents_by_case: dict[str, list[DocumentRecord]] = defaultdict(list)
    for doc in document_repository.list_documents():
        if doc.case_id is not None:
            documents_by_case[doc.case_id].append(doc)

    saved = _load_saved_risks([case.id for case in cases])
    # Detection reports are only read for cases with no saved score.
    unsaved = [case for case in cases if case.id not in saved]
    detection_states = _load_detection_states(
        [document for case in unsaved for document in documents_by_case.get(case.id, [])]
    ) if unsaved else None
    analyses = _load_analyses([case.id for case in cases])
    return [
        build_summary(
            case, documents_by_case.get(case.id, []),
            None if case.id in saved else detection_states, analyses, saved.get(case.id),
        )
        for case in cases
    ]
