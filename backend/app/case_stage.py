"""
Tracks which stage each case is at (models.CaseStage) and records every
change — the stage, when, and who — in case_status_history.

Stages before FINALISED follow from the case itself, so the system moves the
case through them (recorded as SYSTEM) whenever a document or the case
analysis changes — see document_repository._sync_case_now and
case_pipeline/service.py. A loan officer finalises an analysed case and can
reopen a finalised one; those changes are recorded under their username.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict

from . import case_repository, document_repository
from .case_pipeline import repository as analysis_repository, state as analysis_state
from .langgraph import trace as langgraph_trace
from .models import (
    APPROVED_STATUSES,
    CaseRecord,
    CaseStage,
    CaseStageChange,
    DocumentRecord,
    DocumentStatus,
    RoutingStatus,
)

logger = logging.getLogger(__name__)

# Who an automatic change is recorded under.
SYSTEM = "System"

# A document stopped by the officer no longer takes part in the case.
_SET_ASIDE = frozenset({DocumentStatus.CANCELLED})
# Still waiting for, or in, classification (EXTRACTING means classification is recorded).
_NOT_YET_CLASSIFIED = frozenset({DocumentStatus.UPLOADED, DocumentStatus.QUEUED, DocumentStatus.PROCESSING})
# Detection has started on the document (or finished).
_DETECTION_STARTED = frozenset({DocumentStatus.DETECTING, DocumentStatus.DETECTED})

# An officer may finalise once the analysis is current, even with further evidence requested (it may never come).
_FINALISABLE = frozenset({CaseStage.CASE_ANALYSED.value, CaseStage.ADDITIONAL_DOCUMENTS_REQUIRED.value})

# One stage change per case at a time in this process; set_status's compare-and-swap covers other processes.
_case_locks: defaultdict[str, threading.RLock] = defaultdict(threading.RLock)


class StageChangeNotAllowed(Exception):
    """The officer asked for a change the case's current stage doesn't allow."""


def needs_manual_review(document: DocumentRecord) -> bool:
    """Classification wasn't confident enough to accept it."""
    return document.status == DocumentStatus.ANALYZED and document.routing_status == RoutingStatus.MANUAL_REVIEW


def document_stage(
    documents: list[DocumentRecord],
    *,
    failed_detection_ids: frozenset[str] = frozenset(),
    missing_requirements: tuple[str, ...] = (),
) -> CaseStage | None:
    """
    The stage the documents put the case at, earliest unfinished work first.
    failed_detection_ids: approved documents whose last detection run crashed
    or was cancelled. missing_requirements: required checklist items no
    document fulfils. None once detection has finished on every approved
    document: the case analysis then decides (see analysis_stage).
    """
    active = [d for d in documents if d.status not in _SET_ASIDE]
    if not active:
        return CaseStage.OPEN
    if any(d.status in _NOT_YET_CLASSIFIED for d in active):
        return CaseStage.DOCUMENTS_UPLOADED
    if any(d.status == DocumentStatus.EXTRACTING for d in active):
        return CaseStage.DOCUMENTS_CLASSIFIED
    if any(d.status == DocumentStatus.FAILED for d in active):
        return CaseStage.DOCUMENTS_FAILED
    if any(needs_manual_review(d) for d in active):
        return CaseStage.MANUAL_REVIEW
    if any(d.status == DocumentStatus.ANALYZED for d in active):
        return CaseStage.DOCUMENTS_EXTRACTED
    # Every document is now approved (waiting for, in or past detection) or rejected.
    approved = [d for d in active if d.status in APPROVED_STATUSES]
    if not approved:
        return CaseStage.DOCUMENTS_REJECTED
    # A crashed run puts the document back at APPROVED; it stays failed until retried.
    if any(d.status == DocumentStatus.APPROVED and d.id in failed_detection_ids for d in approved):
        return CaseStage.DETECTION_FAILED
    if not any(d.status in _DETECTION_STARTED for d in approved):
        # Not a hard stop for now: once detection starts, an incomplete case moves on like any other.
        return CaseStage.DOCUMENTS_INCOMPLETE if missing_requirements else CaseStage.DOCUMENTS_APPROVED
    if any(d.status != DocumentStatus.DETECTED for d in approved):
        return CaseStage.DETECTION_TRIGGERED
    return None


def analysis_stage(case_id: str, documents: list[DocumentRecord]) -> CaseStage:
    """
    For a case whose approved documents are all scanned (documents carrying
    their extracted fields). Analysed only while the latest analysis is
    complete and still matches the documents — the same staleness check the
    case page shows (case_pipeline/state.py) — and then
    ADDITIONAL_DOCUMENTS_REQUIRED while it asks the client for more evidence.
    """
    analysis = analysis_repository.get_latest(case_id)
    if analysis is None or analysis.status != "complete":
        return CaseStage.READY_FOR_ANALYSIS  # a running or failed analysis too: it can be (re)run
    reports, _ = langgraph_trace.get_detection_states([d.id for d in documents])
    if analysis.fingerprint != analysis_state.compute_fingerprint(documents, reports):
        return CaseStage.READY_FOR_ANALYSIS
    if analysis.additional_documents is not None and analysis.additional_documents.items:
        return CaseStage.ADDITIONAL_DOCUMENTS_REQUIRED
    return CaseStage.CASE_ANALYSED


def derive_stage(case_id: str) -> CaseStage:
    # Imported here: case_service imports this module (to record a new case's opening stage).
    from .case_service import compute_requirement_coverage

    # With fields: the checklist's identity coverage and the analysis fingerprint both read them.
    documents = document_repository.list_documents(case_id=case_id)
    waiting = [d.id for d in documents if d.status == DocumentStatus.APPROVED]
    failed_detection_ids = frozenset(langgraph_trace.get_detection_states(waiting, slim=True)[1]) if waiting else frozenset()
    _, missing = compute_requirement_coverage(documents)
    stage = document_stage(documents, failed_detection_ids=failed_detection_ids, missing_requirements=tuple(missing))
    return stage or analysis_stage(case_id, documents)


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
        return _move(case, derive_stage(case_id), SYSTEM)


def finalise(case_id: str, changed_by: str) -> CaseStageChange | None:
    """An officer closes an analysed case. Brings the stage up to date first, so a pending sync can't block it."""
    with _case_locks[case_id]:
        sync_case_stage(case_id)
        case = case_repository.get_case(case_id)
        if case is None:
            return None
        if case.status == CaseStage.FINALISED.value:
            raise StageChangeNotAllowed("The case is already finalised.")
        if case.status not in _FINALISABLE:
            raise StageChangeNotAllowed("Only an analysed case, with a current analysis, can be finalised.")
        return _move(case, CaseStage.FINALISED, changed_by)


def reopen(case_id: str, changed_by: str) -> CaseStageChange | None:
    """An officer reopens a finalised case; it goes back to whatever stage it is now at."""
    with _case_locks[case_id]:
        case = case_repository.get_case(case_id)
        if case is None:
            return None
        if case.status != CaseStage.FINALISED.value:
            raise StageChangeNotAllowed("Only a finalised case can be reopened.")
        return _move(case, derive_stage(case_id), changed_by)


def sync_all_cases() -> int:
    """
    Brings every case's stage up to date, e.g. at startup: a case whose
    documents or analysis changed while no stage was being kept (or while the
    backend was down) would otherwise show a stale stage until its next
    change. Returns how many cases moved. Never raises.
    """
    try:
        case_ids = [case.id for case in case_repository.list_cases()]
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
