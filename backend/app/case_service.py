from __future__ import annotations

import concurrent.futures
import logging
import threading
from datetime import date
from collections import Counter, defaultdict
from typing import Any, Literal

from . import (
    audit,
    case_repository,
    case_stage,
    detection_results,
    document_repository,
    extracted_fields_repository,
    findings_repository,
)
from .case_match import CaseMatchResult, DocumentCaseMatch, check_case_match
from .case_pipeline import repository as analysis_repository, state as analysis_state
from .case_pipeline.models import CaseAnalysis
from .case_pipeline.readiness import is_ready_for_analysis, unapproved_requirements
from .case_pipeline.risk import store as risk_store
from .config import settings
from .langgraph.extraction_prompts import EXTRACTION_SCHEMAS

from .mapper import (
    compute_id_document_identity_progress,
    compute_id_document_missing_side,
    compute_identity_proof_of_address_progress,
    compute_requirement_coverage,
    compute_unmatched_proof_of_address_names,
)
from .models import (
    CaseAnalysisOverview,
    CaseCounts,
    CaseDocumentsStatus,
    CaseFlagCount,
    CaseListItem,
    CaseListPage,
    CaseRecord,
    CaseRisk,
    CaseSearchResult,
    CaseStage,
    CaseSummary,
    DocumentRecord,
    DocumentStatus,
    FindingReview,
    IN_FLIGHT_STATUSES,
    IdDocumentCoverage,
    ProofOfAddressCoverage,
)
from .monitoring import is_stuck
from .read_pool import submit as _submit

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


def get_case(case_id: str) -> CaseRecord | None:
    return case_repository.get_case(case_id)


# The refusal for any change to a finalised case: shared by routers/_dependencies.py and document_service.py.
FINALISED_DETAIL = "This case is finalised and view-only, so it can't be changed."


def is_case_finalised(case_id: str) -> bool:
    """A finalised case is view-only and accepts no changes (see routers/_dependencies.py)."""
    case = case_repository.get_case(case_id)
    return case is not None and case.status == CaseStage.FINALISED.value


def create_case(company_name: str | None = None, *, reviews_required: bool = True) -> CaseRecord:
    return case_repository.create_case(
        company_name, checklist_enforced=settings.REQUIRE_COMPLETE_CHECKLIST, reviews_required=reviews_required,
    )


def update_case(case_id: str, company_name: str) -> CaseRecord | None:
    return case_repository.update_case(case_id, company_name)


def delete_case(case_id: str) -> None:
    """
    Removes a case and every document that belongs to it. Documents go
    first (document_repository.remove_documents), so their stored files are
    removed; the database cascades the rest.
    """
    documents = document_repository.list_documents(case_id=case_id, include_fields=False)
    document_repository.remove_documents(documents)
    case_repository.delete_case(case_id)


# (reports by document_id, ids whose last detection run failed) — see
# detection_results.get_states.
DetectionStates = tuple[dict[str, dict[str, Any]], set[str]]


def _load_detection_states(documents: list[DocumentRecord], *, slim: bool = False) -> DetectionStates | None:
    """None when the trace table can't be read — the case still loads, without detection states."""
    try:
        return detection_results.get_states([document.id for document in documents], slim=slim)
    except Exception:
        logger.exception("Failed to load detection reports for %d document(s)", len(documents))
        return None


def _load_analysis(case_id: str) -> CaseAnalysis | None:
    """None when the table can't be read: the case then simply shows no analysis."""
    try:
        return analysis_repository.get_latest_summary(case_id)
    except Exception:
        logger.exception("Failed to load the latest analysis of case %s", case_id)
        return None


def _load_last_complete(case_id: str, analysis: CaseAnalysis | None) -> CaseAnalysis | None:
    """The newest finished run while the latest one isn't finished: its risk is still the case's."""
    if analysis is None or analysis.status == "complete":
        return None
    try:
        return analysis_repository.get_latest_complete(case_id)
    except Exception:
        logger.exception("Failed to load the last complete analysis of case %s", case_id)
        return None


def _load_reviews(case: CaseRecord) -> list[FindingReview] | None:
    """
    The officer's finding verdicts, only while the case is being evaluated; None when they can't be read, or when the
    case doesn't take reviews (nothing is counted then: see case_stage.analysis_stage).
    """
    if not case.reviews_required:
        return None
    if case.status not in (CaseStage.CASE_ANALYSED.value, CaseStage.PENDING_EVALUATION.value):
        return []
    try:
        return findings_repository.list_reviews_for_case(case.id)
    except Exception:
        logger.exception("Failed to load the finding reviews of case %s", case.id)
        return None


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
    failed_detection_ids: set[str] | None = None,
    reviews: list[FindingReview] | None = None,
) -> CaseSummary:
    """
    The case's checklist and document rollup, computed from its documents,
    plus the risk and evidence requests of its latest complete case
    analysis. Those two only ever come from an analysis run
    (case_pipeline/service.py) and are never recomputed here. reports is
    needed to tell whether the latest analysis is stale.
    """
    fulfilled, missing = compute_requirement_coverage(documents)
    unapproved = unapproved_requirements(case, documents)
    covered, total = compute_identity_proof_of_address_progress(documents)
    unmatched_names = compute_unmatched_proof_of_address_names(documents)
    id_covered, id_total = compute_id_document_identity_progress(documents)
    id_missing_side = compute_id_document_missing_side(documents)
    overview: CaseAnalysisOverview | None = None
    if analysis is not None:
        if reports is not None:
            analysis = analysis_state.current_view(analysis, documents, reports)
        overview = CaseAnalysisOverview(
            run_id=analysis.run_id, status=analysis.status, stale=analysis.stale,
            finished_at=analysis.finished_at, finding_count=len(analysis.findings),
            # Without full reports, a document counts as scanned once its status says so.
            waiting_documents=analysis_state.waiting_documents(
                documents, reports if reports is not None else {d.id: {} for d in documents if d.status == "detected"},
            ),
        )
    if completed is None and analysis is not None and analysis.status == "complete":
        completed = analysis
    return CaseSummary(
        **case.model_dump(),
        documents_status=compute_documents_status(documents),
        fulfilled_requirements=fulfilled,
        missing_requirements=missing,
        unapproved_requirements=unapproved,
        proof_of_address_coverage=ProofOfAddressCoverage(
            covered=covered, total=total, unmatched_names=unmatched_names
        ),
        id_document_coverage=IdDocumentCoverage(
            covered=id_covered, total=id_total, missing_side=id_missing_side
        ),
        risk=completed.risk if completed else None,
        additional_documents=case.additional_documents,
        # A case that enforces the checklist also waits for every required document to be approved.
        analysis_ready=is_ready_for_analysis(documents) and not unapproved,
        analysis=overview,
        failure=(
            case_stage.failure_of(documents, failed_detection_ids or set())
            if case.status == CaseStage.FAILED.value else None
        ),
        findings_to_review=(
            case_stage.count_unreviewed(completed.risk if completed else None, reviews)
            if reviews is not None and case.status in (CaseStage.CASE_ANALYSED.value, CaseStage.PENDING_EVALUATION.value)
            else None
        ),
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


def get_case_match_for_document(document: DocumentRecord) -> DocumentCaseMatch | None:
    """
    One document's entry from its case's match check. `document` is the caller's row (fields not needed): the
    case and its documents are read side by side. The document is appended to its case's document list if the
    fetch didn't happen to include it (defensive, not expected in practice) so check_case_match always has
    something to report on it.
    """
    if document.case_id is None:
        return None
    case_read = _submit(case_repository.get_case, document.case_id)
    documents = document_repository.list_documents(case_id=document.case_id)
    case = case_read.result()
    if case is None:
        return None
    if all(d.id != document.id for d in documents):
        documents.append(document_repository.get_document(document.id) or document)
    result = check_case_match(case, documents)
    return next((match for match in result.documents if match.document_id == document.id), None)


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
    case_read = _submit(case_repository.get_case, case_id)
    documents_read = _submit(document_repository.list_documents, case_id=case_id, include_fields=False)
    analysis_read = _submit(_load_analysis, case_id)
    case = case_read.result()
    if case is None:
        return None
    documents, analysis = documents_read.result(), analysis_read.result()
    # Second round: the reads that need the document ids or the analysis.
    # One fields query serves both the plain values (mapper/case_match) and the {value, confidence} wrappers.
    fields_read = _submit(
        extracted_fields_repository.get_extracted_fields_for_documents, [document.id for document in documents]
    )
    # Full reports only matter for the staleness check of an existing analysis; otherwise "was it scanned" is enough.
    states_read = _submit(_load_detection_states, documents, slim=analysis is None)
    completed_read = _submit(_load_last_complete, case_id, analysis)
    reviews_read = _submit(_load_reviews, case)
    fields_by_document = fields_read.result()
    for document in documents:
        fields = fields_by_document.get(document.id)
        document.extracted_fields = {f.field_name: f.field_value for f in fields} if fields else None
    detection_states = states_read.result()
    summary = build_summary(
        case, documents, detection_states[0] if detection_states is not None else None,
        analysis, completed_read.result(),
        detection_states[1] if detection_states is not None else None, reviews_read.result(),
    )
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
    case_read = _submit(case_repository.get_case, case_id)
    documents_read = _submit(document_repository.list_documents, case_id=case_id)
    analysis_read = _submit(_load_analysis, case_id)
    case = case_read.result()
    if case is None:
        return None
    documents, analysis = documents_read.result(), analysis_read.result()
    states_read = _submit(_load_detection_states, documents, slim=analysis is None)
    reviews_read = _submit(_load_reviews, case)
    completed = _load_last_complete(case_id, analysis)
    detection_states = states_read.result()
    return build_summary(
        case, documents, detection_states[0] if detection_states is not None else None, analysis, completed,
        detection_states[1] if detection_states is not None else None, reviews_read.result(),
    )


CaseSort = Literal["newest", "oldest", "risk", "lowest_risk"]
# "unscored" is a case no analysis run has scored yet.
CaseRiskFilter = Literal["Critical", "High", "Medium", "Low", "unscored"]
_RISK_RANK = {"Critical": 4, "High": 3, "Medium": 2, "Low": 1}


def _group_by_case(documents: list[DocumentRecord]) -> dict[str, list[DocumentRecord]]:
    grouped: dict[str, list[DocumentRecord]] = defaultdict(list)
    for doc in documents:
        if doc.case_id is not None:
            grouped[doc.case_id].append(doc)
    return grouped


def _load_processing_times(case_ids: list[str]) -> dict[str, int]:
    """Empty when the audit trail can't be read: the list then shows no processing times."""
    try:
        return audit.processing_ms_many(case_ids)
    except Exception:
        logger.exception("Failed to load processing times for %d case(s)", len(case_ids))
        return {}


_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}


def _load_flag_counts(documents_by_case: dict[str, list[DocumentRecord]]) -> dict[str, list[CaseFlagCount]]:
    """
    Each case's flags grouped by check, counting only flags on documents the case still has. Empty when the
    flags can't be read: the list then shows no flags.
    """
    try:
        rows = findings_repository.list_flags_for_cases(list(documents_by_case))
    except Exception:
        logger.exception("Failed to load flags for %d case(s)", len(documents_by_case))
        return {}
    types = {doc.id: doc.document_type for docs in documents_by_case.values() for doc in docs}
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if row.get("document_id") in types:
            grouped[row["case_id"]][row.get("check_name") or "unknown"].append(row)
    return {
        case_id: sorted(
            (
                CaseFlagCount(
                    check=check,
                    severity=max((str(r.get("severity") or "medium") for r in flags), key=lambda v: _SEVERITY_RANK.get(v, 0)),
                    count=len(flags),
                    document_types=sorted({t for r in flags if (t := types.get(r["document_id"]))}),
                )
                for check, flags in checks.items()
            ),
            key=lambda flag: -flag.count,
        )
        for case_id, checks in grouped.items()
    }


def _load_creators(case_ids: list[str]) -> dict[str, str]:
    """Empty when the audit trail can't be read: the list then shows no creators."""
    try:
        return audit.created_by_many(case_ids)
    except Exception:
        logger.exception("Failed to load case creators for %d case(s)", len(case_ids))
        return {}


def _list_items(
    cases: list[CaseRecord],
    documents_by_case: dict[str, list[DocumentRecord]] | None = None,
    saved: dict[str, CaseRisk] | None = None,
) -> list[CaseListItem]:
    """
    Queue rows for these cases: five queries (their documents' statuses and types, their latest saved scores, their
    stage timings, their flags, their creators) however many there are. The score is whatever the case's last analysis run saved;
    nothing is recomputed here.
    """
    if not cases:
        return []
    ids = [case.id for case in cases]
    if documents_by_case is None:
        documents_by_case = _group_by_case(document_repository.list_document_statuses(ids))
    if saved is None:
        saved = _load_saved_risks(ids)
    processing = _load_processing_times(ids)
    creators = _load_creators(ids)
    flags = _load_flag_counts({case_id: documents_by_case.get(case_id, []) for case_id in ids})
    return [
        CaseListItem(
            **case.model_dump(),
            documents_status=compute_documents_status(documents_by_case.get(case.id, [])),
            risk=saved.get(case.id),
            processing_ms=processing.get(case.id),
            recommended_documents=(
                [item.title for item in case.additional_documents.items] if case.additional_documents else None
            ),
            document_types=sorted({doc.document_type for doc in documents_by_case.get(case.id, []) if doc.document_type}),
            flags=flags.get(case.id, []),
            created_by=creators.get(case.id),
        )
        for case in cases
    ]


def _risk_rank(risk: CaseRisk | None) -> int:
    """Unscored cases rank below Low."""
    return _RISK_RANK.get(risk.level or "", 0) if risk is not None and risk.state == "scored" else 0


def _risk_filter_value(risk: CaseRisk | None) -> str:
    rank = _risk_rank(risk)
    return next((level for level, value in _RISK_RANK.items() if value == rank), "unscored")


def list_cases_page(
    *, limit: int, offset: int, sort: CaseSort, statuses: list[CaseStage], q: str | None,
    risks: list[CaseRiskFilter] | None = None,
) -> CaseListPage:
    """
    One page of the case queue. Sorting by date, searching and the stage filter (cases.status) happen in the
    database, so only the page's own documents and scores are read. A risk filter and the risk order come
    from another table, which the database can't sort or filter on: those read every matching case first,
    then slice.
    """
    q = (q or "").strip() or None
    stages = [stage.value for stage in statuses]
    by_risk = sort in ("risk", "lowest_risk")
    if not by_risk and not risks:
        cases, total = case_repository.list_cases_page(
            q, newest=sort == "newest", limit=limit, offset=offset, statuses=stages,
        )
        return CaseListPage(items=_list_items(cases), total=total, limit=limit, offset=offset)

    cases = case_repository.list_matching_cases(q, statuses=stages)  # newest first
    documents_by_case = _group_by_case(document_repository.list_document_statuses([case.id for case in cases]))
    saved: dict[str, CaseRisk] | None = None
    if by_risk or risks:
        saved = _load_saved_risks([case.id for case in cases])
    if risks:
        wanted_risks = set(risks)
        cases = [case for case in cases if _risk_filter_value((saved or {}).get(case.id)) in wanted_risks]
    if by_risk:
        sign = -1 if sort == "risk" else 1
        cases = sorted(cases, key=lambda case: sign * _risk_rank((saved or {}).get(case.id)))  # stable: newest first among equals
    elif sort == "oldest":
        cases.reverse()
    page = cases[offset : offset + limit]
    return CaseListPage(items=_list_items(page, documents_by_case, saved), total=len(cases), limit=limit, offset=offset)


def case_ids_matching(since: date | None, stages: list[CaseStage]) -> set[str]:
    """The ids of cases created on or after `since` (UTC date) and at one of these stages (any when empty)."""
    cases = case_repository.list_matching_cases(None, statuses=[stage.value for stage in stages] or None)
    if since is not None:
        cases = [case for case in cases if case.created_at.date() >= since]
    return {case.id for case in cases}


def case_counts() -> CaseCounts:
    """Every case, and how many are at each stage (the queue's sidebar)."""
    stages = case_repository.list_case_statuses()
    return CaseCounts(total=len(stages), by_status=dict(Counter(stages)))


def _match_rank(case: CaseRecord, needle: str) -> int:
    """Exact number first, then a number or name that starts with it, then the rest."""
    number = case.greenlink_case_id.lower()
    if needle in (number, case.id.lower()):
        return 0
    if number.startswith(needle) or (case.company_name or "").lower().startswith(needle):
        return 1
    return 2


def search_cases(q: str | None, limit: int) -> CaseSearchResult:
    """Cases whose number or name contains q, best match first. Without q: the newest cases."""
    q = (q or "").strip() or None
    cases, total = case_repository.list_cases_page(q, newest=True, limit=limit, offset=0)
    if q and total > limit:
        # More cases match than fit, so newer cases may have crowded the best ones out: fetch those directly.
        exact = case_repository.find_case_numbered(q)
        best = ([exact] if exact else []) + case_repository.find_cases_starting_with(q, limit) + cases
        cases = list({case.id: case for case in best}.values())
    if q:
        cases = sorted(cases, key=lambda case: _match_rank(case, q.lower()))
    return CaseSearchResult(items=cases[:limit], total=total)


def get_case_item(number: str) -> CaseListItem | None:
    """One queue row, by the number officers see in the URL (GLDB-137)."""
    case = case_repository.get_case_by_number(number)
    return _list_items([case])[0] if case is not None else None


def finalise_case(case_id: str) -> CaseSummary | None:
    """Raises case_stage.StageChangeNotAllowed when the case isn't analysed."""
    case_stage.finalise(case_id)
    return get_case_summary(case_id)

