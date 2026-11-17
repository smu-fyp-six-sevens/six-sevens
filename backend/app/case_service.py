from __future__ import annotations

import concurrent.futures
import logging
import threading
from collections import defaultdict
from typing import Any, Literal

from . import (
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
from .langgraph import detection_report
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
    skips_detection,
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


def create_case(
    company_name: str | None = None, *, reviews_required: bool = True, created_by: str | None = None,
) -> CaseRecord:
    return case_repository.create_case(
        company_name, checklist_enforced=settings.REQUIRE_COMPLETE_CHECKLIST, reviews_required=reviews_required,
        created_by=created_by,
    )


def update_case(case_id: str, company_name: str) -> CaseRecord | None:
    return case_repository.update_case(case_id, company_name)


class CaseNotDeletable(Exception):
    """Something is still running on the case. The message is written for the officer."""


def _ensure_deletable(case_id: str, documents: list[DocumentRecord]) -> None:
    """A delete mid-run would leave the run spending Claude calls on rows that no longer exist."""
    if any(document.status in IN_FLIGHT_STATUSES for document in documents):
        raise CaseNotDeletable(
            "Case has a document that's still being processed; cancel it first, or wait for it to finish, "
            "before deleting the case."
        )
    if any(document.status == DocumentStatus.DETECTING for document in documents):
        raise CaseNotDeletable("A document is still being scanned; cancel it or wait for it to finish first.")
    waiting = [d.id for d in documents if d.status == DocumentStatus.APPROVED and not skips_detection(d)]
    if waiting:
        reports, failed = detection_results.get_states(waiting, slim=True)
        if set(waiting) - set(reports) - failed:
            raise CaseNotDeletable("A document is waiting to be scanned; cancel it or wait for it to finish first.")
    latest = analysis_repository.latest_run_state(case_id)
    if latest is not None and analysis_state.is_live(*latest):
        raise CaseNotDeletable("The case analysis is still running; wait for it to finish first.")


def delete_case(case_id: str) -> list[DocumentRecord]:
    """
    Removes a case and every document that belongs to it, and returns those documents. Refused (CaseNotDeletable)
    while anything still runs on it. Documents go first, for their stored files; the database cascades the rest.
    """
    documents = document_repository.list_documents(case_id=case_id, include_fields=False)
    _ensure_deletable(case_id, documents)
    document_repository.remove_documents(documents, case_going=True)
    case_repository.delete_case(case_id)
    return documents


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
        # Whether any of the callers waiting for the follow-up saw a document change.
        self.documents_changed = False


_snapshot_slots: dict[str, _SnapshotSlot] = {}
_snapshot_lock = threading.Lock()


class _DocumentReads:
    """A full build's reads of a case's documents, fields and detection states, as read (before any rewriting)."""

    def __init__(self, documents: list[DocumentRecord], fields: dict, states: DetectionStates, slim: bool) -> None:
        self.documents = [document.model_copy(deep=True) for document in documents]
        self.fields = fields
        self.states = states
        self.slim = slim


# Each case's last full build, reused by a build after a change to the case row or its analysis run only: neither
# can change a document, its fields or its detection report. Bounded, oldest case dropped first.
_document_reads: dict[str, _DocumentReads] = {}
_MAX_REMEMBERED_CASES = 100


def _remember_document_reads(case_id: str, reads: _DocumentReads) -> None:
    _document_reads.pop(case_id, None)
    _document_reads[case_id] = reads
    while len(_document_reads) > _MAX_REMEMBERED_CASES:
        _document_reads.pop(next(iter(_document_reads)), None)


def build_case_snapshot(case_id: str, *, documents_changed: bool = True) -> dict | None:
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

    documents_changed=False (only the case row or its analysis run changed) reuses the documents, fields and
    detection states of the case's last full build. The payload is complete either way.
    """
    with _snapshot_lock:
        slot = _snapshot_slots.get(case_id)
        if slot is not None:
            if slot.waiting is None:
                slot.waiting = concurrent.futures.Future()
            slot.documents_changed = slot.documents_changed or documents_changed
            follow_up = slot.waiting
        else:
            slot = _snapshot_slots[case_id] = _SnapshotSlot()
            follow_up = None
    if follow_up is not None:
        return follow_up.result()

    first_result, first_error = None, None
    try:
        first_result = _build_case_snapshot(case_id, documents_changed=documents_changed)
    except Exception as exc:
        first_error = exc
    while True:
        with _snapshot_lock:
            waiting, slot.waiting = slot.waiting, None
            changed, slot.documents_changed = slot.documents_changed, False
            if waiting is None:
                _snapshot_slots.pop(case_id, None)
                break
        try:
            waiting.set_result(_build_case_snapshot(case_id, documents_changed=changed))
        except Exception as exc:
            waiting.set_exception(exc)
    if first_error is not None:
        raise first_error
    return first_result


def _detection_counts(report: dict[str, Any] | None) -> dict[str, int] | None:
    """A scanned document's findings by severity, so the Documents list shows them with the list (None: not scanned,
    or a light read of a report saved before counts were stored; the page then reads that report itself)."""
    if report is None:
        return None
    if "findings" in report:
        return detection_report.severity_counts(report["findings"])
    return report.get("severity_counts")


def _build_case_snapshot(case_id: str, *, documents_changed: bool = True) -> dict | None:
    reused = None if documents_changed else _document_reads.get(case_id)
    case_read = _submit(case_repository.get_case, case_id)
    documents_read = (
        None if reused else _submit(document_repository.list_documents, case_id=case_id, include_fields=False)
    )
    analysis_read = _submit(_load_analysis, case_id)
    case = case_read.result()
    if case is None:
        _document_reads.pop(case_id, None)
        return None
    analysis = analysis_read.result()
    if reused is not None and reused.slim and analysis is not None:
        # The staleness check of the analysis needs the full reports, which the last build didn't read.
        reused = None
        documents_read = _submit(document_repository.list_documents, case_id=case_id, include_fields=False)
    completed_read = _submit(_load_last_complete, case_id, analysis)
    reviews_read = _submit(_load_reviews, case)
    if reused is not None:
        documents = [document.model_copy(deep=True) for document in reused.documents]
        fields_by_document, detection_states = reused.fields, reused.states
    else:
        assert documents_read is not None
        documents = documents_read.result()
        # Second round: the reads that need the document ids.
        # One fields query serves both the plain values (mapper/case_match) and the {value, confidence} wrappers.
        fields_read = _submit(
            extracted_fields_repository.get_extracted_fields_for_documents, [document.id for document in documents]
        )
        # Full reports only matter for the staleness check of an existing analysis; otherwise "was it scanned" is
        # enough.
        states_read = _submit(_load_detection_states, documents, slim=analysis is None)
        fields_by_document, detection_states = fields_read.result(), states_read.result()
        if detection_states is not None:
            _remember_document_reads(
                case_id, _DocumentReads(documents, fields_by_document, detection_states, slim=analysis is None),
            )
    for document in documents:
        fields = fields_by_document.get(document.id)
        document.extracted_fields = {f.field_name: f.field_value for f in fields} if fields else None
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
        dump["detection_counts"] = _detection_counts(reports.get(document.id))
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
_RISK_FILTER_RANKS: dict[str, int] = {**risk_store.RISK_RANKS, "unscored": 0}


def _group_by_case(documents: list[DocumentRecord]) -> dict[str, list[DocumentRecord]]:
    grouped: dict[str, list[DocumentRecord]] = defaultdict(list)
    for doc in documents:
        if doc.case_id is not None:
            grouped[doc.case_id].append(doc)
    return grouped


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


def _list_items(cases: list[CaseRecord]) -> list[CaseListItem]:
    """
    Queue rows for these cases: three queries (their documents' statuses and types, their latest saved scores, their
    flags) however many there are. The creator and processing time are on the case row. The score is whatever the
    case's last analysis run saved; nothing is recomputed here.
    """
    if not cases:
        return []
    ids = [case.id for case in cases]
    documents_by_case = _group_by_case(document_repository.list_document_statuses(ids))
    saved = _load_saved_risks(ids)
    flags = _load_flag_counts({case_id: documents_by_case.get(case_id, []) for case_id in ids})
    return [
        CaseListItem(
            **case.model_dump(),
            documents_status=compute_documents_status(documents_by_case.get(case.id, [])),
            risk=saved.get(case.id),
            processing_ms=case.processing_ms,
            recommended_documents=(
                [item.title for item in case.additional_documents.items] if case.additional_documents else None
            ),
            document_types=sorted({doc.document_type for doc in documents_by_case.get(case.id, []) if doc.document_type}),
            flags=flags.get(case.id, []),
            created_by=case.created_by,
        )
        for case in cases
    ]


def list_cases_page(
    *, limit: int, offset: int, sort: CaseSort, statuses: list[CaseStage], q: str | None,
    risks: list[CaseRiskFilter] | None = None,
) -> CaseListPage:
    """
    One page of the case queue. Sorting, searching and the stage and risk filters all happen in the database (the
    risk by cases.risk_rank, which each analysis run's saved score sets), so only the page's own cases, documents
    and scores are read. Among cases of equal risk, the newest come first.
    """
    q = (q or "").strip() or None
    highest_first = {"risk": True, "lowest_risk": False}.get(sort)
    cases, total = case_repository.list_cases_page(
        q, newest=sort != "oldest", limit=limit, offset=offset, statuses=[stage.value for stage in statuses],
        risk_ranks=sorted({_RISK_FILTER_RANKS[risk] for risk in risks}) if risks else None,
        highest_risk_first=highest_first,
    )
    return CaseListPage(items=_list_items(cases), total=total, limit=limit, offset=offset)


def case_counts() -> CaseCounts:
    """How many cases there are, and how many at each stage (the queue's sidebar), counted by the database."""
    by_status = case_repository.count_cases_by_status()
    return CaseCounts(total=sum(by_status.values()), by_status=by_status)


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

