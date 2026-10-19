from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections import defaultdict
from typing import Any

from . import case_repository, case_stage, document_repository, extracted_fields_repository
from .case_match import CaseMatchResult, DocumentCaseMatch, check_case_match
from .case_pipeline import repository as analysis_repository, state as analysis_state
from .case_pipeline.models import CaseAnalysis
from .case_pipeline.readiness import is_ready_for_analysis
from .case_pipeline.risk import store as risk_store
from .config import settings
from .langgraph import trace as langgraph_trace
from .langgraph.extraction_prompts import EXTRACTION_SCHEMAS

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
    CaseListItem,
    CaseRecord,
    CaseRisk,
    CaseStage,
    CaseStageChange,
    CaseSummary,
    DocumentRecord,
    DocumentStatus,
    DocumentType,
    IN_FLIGHT_STATUSES,
    IdDocumentCoverage,
    IDENTITY_DOCUMENT_REQUIREMENT,
    ProofOfAddressCoverage,
    counts_towards_checklist,
)
from .monitoring import is_stuck

logger = logging.getLogger("classifier_pipeline.case_service")


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


def get_case(case_id: str) -> CaseRecord | None:
    return case_repository.get_case(case_id)


def is_case_finalised(case_id: str) -> bool:
    """A finalised case accepts no changes until an officer reopens it (see routers/_dependencies.py)."""
    case = case_repository.get_case(case_id)
    return case is not None and case.status == CaseStage.FINALISED.value


def create_case(company_name: str | None = None, created_by: str = case_stage.SYSTEM) -> CaseRecord:
    case = case_repository.create_case(company_name)
    case_stage.record_created(case, created_by)
    return case


def update_case(case_id: str, company_name: str) -> CaseRecord | None:
    return case_repository.update_case(case_id, company_name)


def delete_case(case_id: str) -> None:
    """
    Removes a case and every document that belongs to it. Documents go
    first (via document_repository.remove_documents, which batch-clears
    their extracted-field rows and stored files) so nothing is left orphaned
    if the case delete were to run first and fail partway through.
    """
    documents = document_repository.list_documents(case_id=case_id, include_fields=False)
    document_repository.remove_documents(documents)
    case_repository.delete_case(case_id)
    _synced_finalized.pop(case_id, None)


# The value last written per case by this process, so an unchanged flag costs no write (and no
# Realtime event, and doesn't restamp finalized_at). Only ever a shortcut: a miss just writes.
_synced_finalized: dict[str, bool] = {}


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
    finalized = not missing
    if _synced_finalized.get(case_id) == finalized:
        return
    case_repository.set_submission_finalized(case_id, finalized=finalized)
    _synced_finalized[case_id] = finalized


# (reports by document_id, ids whose last detection run failed) — see
# langgraph_trace.get_detection_states.
DetectionStates = tuple[dict[str, dict[str, Any]], set[str]]


def _load_detection_states(documents: list[DocumentRecord], *, slim: bool = False) -> DetectionStates | None:
    """None when the trace table can't be read — the case still loads, without detection states."""
    try:
        return langgraph_trace.get_detection_states([document.id for document in documents], slim=slim)
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


def _load_status_history(case_id: str) -> list[CaseStageChange]:
    """Empty when the history can't be read: the case still loads, with its current stage."""
    try:
        return case_repository.list_status_history(case_id)
    except Exception:
        logger.exception("Failed to load the stage history of case %s", case_id)
        return []


def _load_saved_risks(case_ids: list[str]) -> dict[str, CaseRisk]:
    """Empty when the saved scores can't be read: the list then shows those cases unscored."""
    try:
        return risk_store.latest_many(case_ids)
    except Exception:
        logger.exception("Failed to load saved risk scores for %d case(s)", len(case_ids))
        return {}


def build_summary(
    case,
    documents: list[DocumentRecord],
    reports: dict[str, dict[str, Any]] | None = None,
    analysis: CaseAnalysis | None = None,
    completed: CaseAnalysis | None = None,
    status_history: list[CaseStageChange] | None = None,
) -> CaseSummary:
    """
    The case's checklist and document rollup, computed from its documents,
    plus the risk and evidence requests of its latest complete case
    analysis. Those two only ever come from an analysis run
    (case_pipeline/service.py) and are never recomputed here. reports is
    needed to tell whether the latest analysis is stale.
    """
    fulfilled, missing = compute_requirement_coverage(documents)
    covered, total = compute_identity_proof_of_address_progress(documents)
    unmatched_names = compute_unmatched_proof_of_address_names(documents)
    id_covered, id_total = compute_id_document_identity_progress(documents)
    id_missing_side = compute_id_document_missing_side(documents)
    data = case.model_dump()
    data["recommended_additional_documents"] = missing
    overview: CaseAnalysisOverview | None = None
    if analysis is not None:
        if reports is not None:
            analysis = analysis_state.current_view(analysis, documents, reports)
        overview = CaseAnalysisOverview(
            run_id=analysis.run_id, status=analysis.status, stale=analysis.stale,
            finished_at=analysis.finished_at, finding_count=len(analysis.findings),
        )
    if completed is None and analysis is not None and analysis.status == "complete":
        completed = analysis
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
        risk=completed.risk if completed else None,
        supporting_document_recommendations=completed.supporting_documents if completed else [],
        additional_documents=completed.additional_documents if completed else None,
        analysis_ready=is_ready_for_analysis(documents),
        analysis=overview,
        status_history=status_history or [],
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


def _enrich_with_extracted_fields(
    documents: list[DocumentRecord], fields_by_document: dict | None = None
) -> list[DocumentRecord]:
    if fields_by_document is None:
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


def _fetch_case_and_documents(case_id: str) -> tuple[CaseRecord | None, list[DocumentRecord]]:
    """
    The "case + its documents" fetch every case-match/snapshot assembly
    needs, in one place — see get_case_match_for_case, build_case_snapshot.
    """
    case = case_repository.get_case(case_id)
    if case is None:
        return None, []
    return case, document_repository.list_documents(case_id=case_id)


def get_case_match_for_case(case_id: str) -> CaseMatchResult | None:
    """Whether each of the case's documents belongs to the case's company and individuals."""
    case, documents = _fetch_case_and_documents(case_id)
    if case is None:
        return None
    return check_case_match(case, documents)


def get_case_match_for_document(document_id: str) -> DocumentCaseMatch | None:
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


class _SnapshotSlot:
    """A case's build in progress, and the one follow-up build everyone who arrived meanwhile shares."""

    def __init__(self) -> None:
        self.waiting: concurrent.futures.Future | None = None


_snapshot_slots: dict[str, _SnapshotSlot] = {}
_snapshot_lock = threading.Lock()


def build_case_snapshot(case_id: str) -> dict | None:
    """
    Assembles the exact payload pushed over the case status WebSocket (see
    routers/ws.py) — both the immediate send on connect/refresh and the
    Realtime-triggered push in realtime_listener.py call this, so the two
    paths can't drift out of sync with each other. Runs on a worker thread
    (supabase-py's sync client) wherever it's called from.

    One build runs per case at a time. A caller that arrives mid-build is not
    handed that build's result, which may have been read before the change
    that made it ask; it waits for a single follow-up build, which everyone
    who arrived during the first one shares.
    """
    with _snapshot_lock:
        slot = _snapshot_slots.get(case_id)
        if slot is not None:
            if slot.waiting is None:
                slot.waiting = concurrent.futures.Future()
            follow_up = slot.waiting
        else:
            slot = _snapshot_slots[case_id] = _SnapshotSlot()
            follow_up = None
    if follow_up is not None:
        return follow_up.result()

    first_result, first_error = None, None
    try:
        first_result = _build_case_snapshot(case_id)
    except Exception as exc:
        first_error = exc
    while True:
        with _snapshot_lock:
            waiting, slot.waiting = slot.waiting, None
            if waiting is None:
                _snapshot_slots.pop(case_id, None)
                break
        try:
            waiting.set_result(_build_case_snapshot(case_id))
        except Exception as exc:
            waiting.set_exception(exc)
    if first_error is not None:
        raise first_error
    return first_result


def _build_case_snapshot(case_id: str) -> dict | None:
    case = case_repository.get_case(case_id)
    if case is None:
        return None
    documents = document_repository.list_documents(case_id=case_id, include_fields=False)
    # One fields query serves both the plain values (mapper/case_match) and the {value, confidence} wrappers.
    fields_by_document = extracted_fields_repository.get_extracted_fields_for_documents(
        [document.id for document in documents]
    )
    for document in documents:
        fields = fields_by_document.get(document.id)
        document.extracted_fields = {f.field_name: f.field_value for f in fields} if fields else None
    # Full reports only matter for the staleness check of an existing analysis; otherwise "was it scanned" is enough.
    analysis = _load_analyses([case.id]).get(case.id)
    detection_states = _load_detection_states(documents, slim=analysis is None)
    summary = _summary_with_analysis(case, documents, detection_states, analysis)
    # Before enrichment: that rewrites extracted_fields in place into
    # {"value", "confidence"} wrappers, while case_match (and mapper.py, via
    # build_summary above) reads the plain values document_repository
    # already populated from the extracted_fields table.
    case_match = check_case_match(case, documents)
    enriched = _enrich_with_extracted_fields(documents, fields_by_document)
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


def get_case_summary(case_id: str) -> CaseSummary | None:
    case = case_repository.get_case(case_id)
    if case is None:
        return None
    documents = document_repository.list_documents(case_id=case_id)
    analysis = _load_analyses([case.id]).get(case.id)
    return _summary_with_analysis(
        case, documents, _load_detection_states(documents, slim=analysis is None), analysis
    )


def _summary_with_analysis(
    case, documents: list[DocumentRecord], detection_states: DetectionStates | None,
    analysis: CaseAnalysis | None,
) -> CaseSummary:
    completed = None
    if analysis is not None and analysis.status != "complete":
        try:
            completed = analysis_repository.get_latest_complete(case.id)
        except Exception:
            logger.exception("Failed to load the last complete analysis of case %s", case.id)
    reports = detection_states[0] if detection_states is not None else None
    return build_summary(case, documents, reports, analysis, completed, _load_status_history(case.id))


def list_case_summaries() -> list[CaseListItem]:
    """
    The case queue: three queries in total (cases, their documents' statuses,
    the latest saved scores), not one per case. The score is whatever the
    case's last analysis run saved; nothing is recomputed here.
    """
    cases = case_repository.list_cases(include_dev=not settings.IS_CLOUD_DEPLOYMENT)
    documents_by_case: dict[str, list[DocumentRecord]] = defaultdict(list)
    for doc in document_repository.list_documents(include_fields=False):
        if doc.case_id is not None:
            documents_by_case[doc.case_id].append(doc)
    saved = _load_saved_risks([case.id for case in cases])
    return [
        CaseListItem(
            **case.model_dump(),
            documents_status=compute_documents_status(documents_by_case.get(case.id, [])),
            risk=saved.get(case.id),
        )
        for case in cases
    ]


def finalise_case(case_id: str, changed_by: str) -> CaseSummary | None:
    """Raises case_stage.StageChangeNotAllowed when the case isn't analysed."""
    case_stage.finalise(case_id, changed_by)
    return get_case_summary(case_id)


def reopen_case(case_id: str, changed_by: str) -> CaseSummary | None:
    """Raises case_stage.StageChangeNotAllowed when the case isn't finalised."""
    case_stage.reopen(case_id, changed_by)
    return get_case_summary(case_id)
