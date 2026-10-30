"""
Tracks which stage each case is at (models.CaseStage) and records every
change — the stage, when, and who — in case_status_history.

Stages before FINALISED follow from the case itself, so the system moves the
case through them (recorded as SYSTEM) whenever a document or the case
analysis changes — see document_repository._sync_case_now and
case_pipeline/service.py. A loan officer finalises an analysed case, recorded
under their username; finalising is permanent and the case is then view-only.

A case holds many documents at different points, so its stage is the one of
the document furthest behind, the most urgent first (see document_stage):
the officer sees what is holding the case up, never an average.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from . import case_repository, document_repository, finding_reviews_repository
from .case_pipeline import repository as analysis_repository, state as analysis_state
from .langgraph import trace as langgraph_trace
from .mapper import compute_requirement_coverage
from .models import (
    APPROVED_STATUSES,
    CaseFailure,
    CaseRecord,
    CaseStage,
    CaseStageChange,
    ContributingFinding,
    DocumentRecord,
    DocumentStatus,
    FindingReview,
)

logger = logging.getLogger(__name__)

# Who an automatic change is recorded under.
SYSTEM = "System"

# A document stopped by the officer no longer takes part in the case.
_SET_ASIDE = frozenset({DocumentStatus.CANCELLED})
# Still being read: uploaded, queued, classified or extracted.
_PROCESSING = frozenset({
    DocumentStatus.UPLOADED, DocumentStatus.QUEUED, DocumentStatus.PROCESSING, DocumentStatus.EXTRACTING,
})

# Only an analysed case whose findings all have the officer's verdict.
_FINALISABLE = frozenset({CaseStage.CASE_ANALYSED.value})

# One stage change per case at a time in this process; set_status's compare-and-swap covers other processes.
_case_locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)


class StageChangeNotAllowed(Exception):
    """The officer asked for a change the case's current stage doesn't allow."""


@dataclass(frozen=True)
class StageResult:
    stage: CaseStage
    # Set only with CaseStage.FAILED.
    failure: CaseFailure | None = None


def _failed(failure: CaseFailure) -> StageResult:
    return StageResult(CaseStage.FAILED, failure)


def document_stage(
    documents: list[DocumentRecord],
    *,
    missing_requirements: Sequence[str],
    failed_detection_ids: frozenset[str] = frozenset(),
) -> StageResult | None:
    """
    The stage the documents put the case at, most urgent first: any document
    still processing holds the whole case there, then a failure, then one
    waiting for the officer, then a required document that is missing (every
    REQUIRED_DOCUMENT_TYPES item is mandatory: missing_requirements, from
    mapper.compute_requirement_coverage), then one being scanned.
    failed_detection_ids: approved documents whose last detection run crashed
    or was cancelled. None once every approved document is scanned and the
    checklist is complete: the case analysis then decides (see analysis_stage).
    """
    active = [d for d in documents if d.status not in _SET_ASIDE]
    if not active:
        return StageResult(CaseStage.OPEN)
    if any(d.status in _PROCESSING for d in active):
        return StageResult(CaseStage.PROCESSING_DOCUMENTS)
    if any(d.status == DocumentStatus.FAILED for d in active):
        return _failed(CaseFailure.DOCUMENT_PROCESSING)
    # A crashed run puts the document back at APPROVED; it stays failed until retried.
    if any(d.status == DocumentStatus.APPROVED and d.id in failed_detection_ids for d in active):
        return _failed(CaseFailure.FRAUD_DETECTION)
    if any(d.status == DocumentStatus.ANALYZED for d in active):
        return StageResult(CaseStage.PENDING_REVIEW)
    # Every document is now approved (waiting for, in or past detection) or rejected. With none approved, or a
    # required document missing, the case can't be analysed until the officer uploads more.
    approved = [d for d in active if d.status in APPROVED_STATUSES]
    if not approved or missing_requirements:
        return StageResult(CaseStage.DOCUMENTS_INCOMPLETE)
    if any(d.status != DocumentStatus.DETECTED for d in approved):
        return StageResult(CaseStage.FRAUD_DETECTION)
    return None


def analysis_stage(case: CaseRecord, documents: list[DocumentRecord]) -> StageResult:
    """
    For a case whose approved documents are all scanned (documents carrying
    their extracted fields). A run, failed or complete, counts only while it
    still matches the documents — the same staleness check the case page
    shows (case_pipeline/state.py); otherwise the case is ready to be
    analysed again. A running analysis keeps the case at Ready for Analysis. A
    complete one is Pending Evaluation until the officer has marked every
    finding correct or wrong (precision and recall), then Case Analysed.
    """
    analysis = analysis_repository.get_latest_summary(case.id)
    if analysis is None:
        return StageResult(CaseStage.READY_FOR_ANALYSIS)
    if analysis.status == "running":
        if analysis_state.is_live(analysis.status, analysis.started_at):
            return StageResult(CaseStage.READY_FOR_ANALYSIS)
        return _failed(CaseFailure.CASE_ANALYSIS)  # the worker was lost; the sweeper marks it failed
    reports, _ = langgraph_trace.get_detection_states([d.id for d in documents])
    if analysis.fingerprint != analysis_state.compute_fingerprint(documents, reports):
        return StageResult(CaseStage.READY_FOR_ANALYSIS)
    if analysis.status == "failed":
        return _failed(CaseFailure.CASE_ANALYSIS)
    if analysis.status != "complete":
        return StageResult(CaseStage.READY_FOR_ANALYSIS)
    findings = analysis.risk.contributing_findings if analysis.risk else []
    if findings and count_unreviewed(findings, finding_reviews_repository.list_for_case(case.id)):
        return StageResult(CaseStage.PENDING_EVALUATION)
    return StageResult(CaseStage.CASE_ANALYSED)


def derive(case: CaseRecord) -> StageResult:
    # With fields: the analysis fingerprint reads them.
    documents = document_repository.list_documents(case_id=case.id)
    waiting = [d.id for d in documents if d.status == DocumentStatus.APPROVED]
    failed_detection_ids = frozenset(langgraph_trace.get_detection_states(waiting, slim=True)[1]) if waiting else frozenset()
    _, missing = compute_requirement_coverage(documents)
    return (
        document_stage(documents, missing_requirements=missing, failed_detection_ids=failed_detection_ids)
        or analysis_stage(case, documents)
    )


def failure_of(documents: list[DocumentRecord], failed_detection_ids: set[str] | frozenset[str]) -> CaseFailure:
    """Which step a FAILED case needs fixed, the earliest first; the analysis when no document failed."""
    active = [d for d in documents if d.status not in _SET_ASIDE]
    if any(d.status == DocumentStatus.FAILED for d in active):
        return CaseFailure.DOCUMENT_PROCESSING
    if any(d.status == DocumentStatus.APPROVED and d.id in failed_detection_ids for d in active):
        return CaseFailure.FRAUD_DETECTION
    return CaseFailure.CASE_ANALYSIS


def count_unreviewed(findings: list[ContributingFinding], reviews: list[FindingReview]) -> int:
    """
    Findings with no correct/wrong verdict. A verdict only counts against the finding it was given on (same id
    and summary), as on the Risk Summary: a rerun renumbers findings, so an old verdict never covers a new one.
    """
    reviewed = {(r.finding_id, r.summary) for r in reviews if r.kind == "verdict" and r.verdict is not None}
    return sum(1 for f in findings if (f.finding_id, f.summary) not in reviewed)


def _move(case: CaseRecord, to_stage: CaseStage, changed_by: str) -> CaseStageChange | None:
    """Records the change unless the case is already there, or another process moved it first."""
    if case.status == to_stage.value:
        return None
    if not case_repository.set_status(case.id, case.status, to_stage.value):
        return None
    return case_repository.add_status_change(case.id, case.status, to_stage.value, changed_by)


def record_created(case: CaseRecord, changed_by: str) -> None:
    """The first history row: the case was opened. Never raises — the case itself already exists."""
    try:
        case_repository.add_status_change(case.id, None, case.status, changed_by)
    except Exception:
        logger.exception("Failed to record the opening stage of case %s", case.id)


def sync_case_stage(case_id: str) -> CaseStageChange | None:
    """Moves the case to the stage its documents and analysis now put it at. A finalised case stays put."""
    with _case_locks[case_id]:
        case = case_repository.get_case(case_id)
        if case is None or case.status == CaseStage.FINALISED.value:
            return None
        return _move(case, derive(case).stage, SYSTEM)


def finalise(case_id: str, changed_by: str) -> CaseStageChange | None:
    """An officer closes an analysed case. Brings the stage up to date first, so a pending sync can't block it."""
    with _case_locks[case_id]:
        sync_case_stage(case_id)
        case = case_repository.get_case(case_id)
        if case is None:
            return None
        if case.status == CaseStage.FINALISED.value:
            raise StageChangeNotAllowed("The case is already finalised.")
        if case.status == CaseStage.PENDING_EVALUATION.value:
            raise StageChangeNotAllowed(
                "Every finding needs a verdict (correct or wrong) before the case can be finalised."
            )
        if case.status not in _FINALISABLE:
            raise StageChangeNotAllowed("Only an analysed case, with its findings evaluated, can be finalised.")
        return _move(case, CaseStage.FINALISED, changed_by)


def sync_all_cases() -> int:
    """
    Brings every case's stage up to date, e.g. at startup: a case whose
    documents or analysis changed while no stage was being kept (or while the
    backend was down), or one still at a stage from before these ones, would
    otherwise show a stale stage until its next change. Returns how many
    cases moved. Never raises.
    """
    try:
        case_ids = case_repository.list_case_ids()
    except Exception:
        logger.exception("Failed to list cases to bring their stages up to date")
        return 0
    moved = 0
    for case_id in case_ids:
        try:
            if sync_case_stage(case_id) is not None:
                moved += 1
        except Exception:
            logger.exception("Failed to bring the stage of case %s up to date", case_id)
    return moved
