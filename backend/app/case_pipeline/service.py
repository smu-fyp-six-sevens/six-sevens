"""
Runs the per-case analysis on an officer's request: cross-document
hypotheses, the risk outlook and the plain-language summary. See
CROSS_DOCUMENT_AND_RISK_PLAN.md.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any, cast

from .. import audit, case_repository, document_repository, events, finding_reviews_repository, read_pool
from ..config import settings
from ..snapshots import push_case_snapshot
from ..langgraph import trace as langgraph_trace
from ..llm_io import ModelRefused
from ..models import DocumentRecord
from ..run_context import end_run, start_run
from . import repository, state
from .cross_document.evidence import build_context
from .cross_document.agents import PROMPT_VERSION
from .cross_document.graph import run_cross_document_analysis
from .models import AnalysisAudit, AnalysisStatus, CaseAnalysis, CaseAnalysisRunItem
from .risk import store as risk_store
from .trace import RunTrace
from .readiness import is_ready_for_analysis
from .risk.case_risk import compute_case_risk, scoring_breakdown
from .risk.additional_documents import build_additional_documents
from .risk.outlook import build_outlook, outlook_trace, propose_requests_with_status
from .risk.summary import write_summary
from .risk.supporting_documents import recommend_supporting_documents

logger = logging.getLogger("classifier_pipeline.case_pipeline.service")

# Plan and judge (Sonnet), evidence requests and summary (Haiku), with two to spare.
_MAX_MODEL_CALLS = 6


class AnalysisDisabled(Exception):
    pass


class AnalysisNotReady(Exception):
    pass


class AnalysisAlreadyRunning(Exception):
    pass


def _check_outcomes(reports: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    """
    (clean, could_not_run). A check is clean only if it examined something, raised
    nothing, and never failed to run on any document: being called is not the same
    as having looked. A check that couldn't run, or had nothing to look at, says
    nothing about that document either way.
    """
    examined: set[str] = set()
    flagged: set[str] = set()
    not_run: set[str] = set()
    for report in reports.values():
        unable = {i.get("tool") for i in report.get("unable_to_assess") or [] if i.get("tool")}
        nothing = {i.get("tool") for i in report.get("nothing_to_check") or [] if i.get("tool")}
        not_run |= unable
        for consulted in report.get("consulted") or []:
            examined.update(t for t in consulted.get("tools_called") or [] if t not in unable | nothing)
        flagged.update(f.get("tool") for f in report.get("findings") or [] if f.get("tool"))
    return sorted(examined - flagged - not_run), sorted(not_run)


async def start_analysis(case_id: str) -> CaseAnalysis:
    """The api side: checks, records a running run, and asks the case-worker to run it."""
    if not settings.CASE_ANALYSIS_ENABLED:
        raise AnalysisDisabled()
    documents = await asyncio.to_thread(document_repository.list_documents, case_id=case_id)
    if not is_ready_for_analysis(documents):
        raise AnalysisNotReady()
    latest = await asyncio.to_thread(repository.latest_run_state, case_id)
    if latest is not None and state.is_live(*latest):
        raise AnalysisAlreadyRunning()

    reports, _ = await asyncio.to_thread(
        langgraph_trace.get_detection_states, [d.id for d in documents]
    )
    run_id = str(uuid.uuid4())
    fingerprint = state.compute_fingerprint(documents, reports)
    await asyncio.to_thread(repository.insert_run, run_id, case_id, fingerprint)
    analysis = CaseAnalysis(
        run_id=run_id, case_id=case_id, status="running", fingerprint=fingerprint,
        started_at=datetime.now(timezone.utc),
    )
    try:
        await events.publish(events.officer_event(
            events.types.CASE_ANALYSIS_REQUESTED, case_id=case_id,
            detail={"run_id": run_id, "fingerprint": fingerprint, "started_at": analysis.started_at.isoformat()},
        ))
    except Exception:
        # Nothing will run it, so it must not block the next attempt for the whole time limit.
        analysis.status, analysis.error = "failed", "The analysis could not be started. Run it again."
        await asyncio.to_thread(repository.finish_run, analysis)
        raise
    return analysis


async def run_requested(case_id: str, run_id: str, fingerprint: str, started_at: datetime) -> CaseAnalysis:
    """The case-worker side: runs a requested analysis over the documents as they are now."""
    documents = await asyncio.to_thread(document_repository.list_documents, case_id=case_id)
    reports, failed_ids = await asyncio.to_thread(
        langgraph_trace.get_detection_states, [d.id for d in documents]
    )
    analysis = CaseAnalysis(
        run_id=run_id, case_id=case_id, status="running", fingerprint=fingerprint, started_at=started_at,
    )
    # The case-worker's scope counts this run's calls (workers/cases.py).
    await _execute(analysis, documents, reports, failed_ids)
    return analysis


async def _execute(
    analysis: CaseAnalysis, documents: list[DocumentRecord], reports: dict[str, dict[str, Any]],
    failed_ids: set[str],
) -> None:
    case_id = analysis.case_id
    trace = RunTrace(analysis.run_id)
    run, run_token = start_run(_MAX_MODEL_CALLS)
    trace.record(
        "run_start", case_id=case_id, fingerprint=analysis.fingerprint,
        documents=[{"id": d.id, "filename": d.filename, "type": d.document_type, "status": d.status.value} for d in documents],
    )
    try:
        context = build_context(case_id, documents, reports)
        context.trace = trace
        trace.record("dossier", documents={k: {"id": v.id, "filename": v.filename, "findings": len(v.findings)}
                                          for k, v in context.docs.items()})
        result = await run_cross_document_analysis(context)
        findings = result.get("findings") or []
        trace.save()

        risk = compute_case_risk(documents, reports, failed_ids, findings)
        document_only = compute_case_risk(documents, reports, failed_ids)
        without_dedupe = compute_case_risk(documents, reports, failed_ids, findings, dedupe=False)
        trace.record(
            "scoring", **scoring_breakdown(risk),
            cross_document_findings=len(findings),
            score_without_cross_document=document_only.score, level_without_cross_document=document_only.level,
            score_without_dedupe=without_dedupe.score, level_without_dedupe=without_dedupe.level,
        )
        requests, request_status = await propose_requests_with_status(risk)
        supporting_documents = recommend_supporting_documents(documents)
        trace.record("evidence_requests", proposed=[r.model_dump() for r in requests],
                     supporting_documents=[s.title for s in supporting_documents])
        outlook = build_outlook(risk, requests)
        trace.record("outlook", **outlook_trace(risk, requests, outlook))
        additional = build_additional_documents(risk, requests, outlook, supporting_documents, request_status)
        trace.record("additional_documents", reason=additional.reason, request_status=request_status,
                     items=[{"title": i.title, "source": i.source, "affects_score": i.affects_score} for i in additional.items])
        clean_checks, unrun_checks = _check_outcomes(reports)
        summary = await write_summary(risk, outlook, clean_checks, unrun_checks)
        trace.record("summary", **summary.model_dump())

        analysis.findings = findings
        analysis.outlook = outlook
        analysis.structured_summary = summary
        analysis.risk = risk
        analysis.supporting_documents = supporting_documents
        analysis.additional_documents = additional
        analysis.consistent_checks = result.get("consistent_checks") or []
        analysis.clean_checks = clean_checks
        analysis.audit = AnalysisAudit(
            models={"analysis": settings.LANGGRAPH_SONNET_MODEL},
            prompt_version=PROMPT_VERSION,
            hypotheses_tried=len(result.get("hypotheses") or []),
            findings_discarded=len(result.get("discarded") or []),
            tool_calls=result.get("tool_calls") or [],
        )
        analysis.status = "complete"
        trace.record("done", status="complete", findings=len(findings))
    except Exception as exc:
        logger.exception("Case analysis failed for case %s", case_id)
        analysis.status = "failed"
        analysis.error = (str(exc) if isinstance(exc, ModelRefused) else f"{type(exc).__name__}: {exc}")[:500]
        trace.record("error", error=analysis.error)
    finally:
        trace.record("run_audit", model_calls=run.calls, call_ceiling=run.max_model_calls, output_repairs=run.repairs)
        end_run(run_token)
        trace.save()
        try:
            await _store_result(analysis)
        except Exception:
            logger.exception("Failed to store the case analysis result for case %s", case_id)
        await push_case_snapshot(case_id)


async def _store_result(analysis: CaseAnalysis) -> None:
    """Saves a finished run; only the case's newest complete run also updates the case's score and requested documents."""
    published = await asyncio.to_thread(repository.finish_run, analysis)
    if published and analysis.status == "complete":
        # Officers' reviews of cross-document findings named the previous run's findings, which this one replaces.
        try:
            await asyncio.to_thread(finding_reviews_repository.clear_case_scope, analysis.case_id)
        except Exception:
            logger.exception("Failed to clear the previous analysis's finding reviews for case %s", analysis.case_id)
        # The case list reads the score from here rather than opening every run.
        await asyncio.to_thread(risk_store.save, analysis.case_id, analysis.risk)
        await asyncio.to_thread(
            case_repository.set_recommended_additional_documents, analysis.case_id, analysis.additional_documents
        )


def _documents_and_reports(case_id: str) -> tuple[list[DocumentRecord], dict[str, dict[str, Any]]]:
    documents = document_repository.list_documents(case_id=case_id)
    reports, _ = langgraph_trace.get_detection_states([d.id for d in documents])
    return documents, reports


def _against_documents_now(analysis_read: Callable[[], CaseAnalysis | None], case_id: str) -> CaseAnalysis | None:
    """The run read side by side with the documents and their reports, which its staleness check needs."""
    documents_read = read_pool.submit(_documents_and_reports, case_id)
    analysis = analysis_read()
    if analysis is None:
        documents_read.cancel()
        return None
    documents, reports = documents_read.result()
    return state.current_view(analysis, documents, reports)


def get_current(case_id: str) -> CaseAnalysis | None:
    """The case's latest analysis against the documents as they are now (sync: run on a worker thread)."""
    return _against_documents_now(lambda: repository.get_latest(case_id), case_id)


def get_run(case_id: str, run_id: str) -> CaseAnalysis | None:
    """One earlier (or the latest) run against the documents as they are now, for reading back (sync)."""
    return _against_documents_now(lambda: repository.get_run(case_id, run_id), case_id)


def _starters(case_id: str) -> dict[str, str]:
    """run_id -> the officer who requested it, from the audit trail. Empty when the trail can't be read."""
    try:
        requested = audit.list_events(case_id=case_id, event_type=events.types.CASE_ANALYSIS_REQUESTED, limit=500)
    except Exception:
        logger.exception("Failed to read who started the analyses of case %s", case_id)
        return {}
    return {str(e.detail["run_id"]): e.actor for e in requested if e.detail.get("run_id")}


def list_runs(case_id: str) -> list[CaseAnalysisRunItem]:
    """Every analysis of the case, newest first, each with who started it, its score change and whether it's stale (sync)."""
    rows = repository.list_runs(case_id)
    if not rows:
        return []
    documents = document_repository.list_documents(case_id=case_id)
    reports, _ = langgraph_trace.get_detection_states([d.id for d in documents])
    current = state.compute_fingerprint(documents, reports)
    starters = _starters(case_id)

    items: list[CaseAnalysisRunItem] = []
    for row in rows:
        started_at = datetime.fromisoformat(str(row["started_at"]))
        status = cast(AnalysisStatus, str(row["status"]))
        # As get_current reads it: a run past the time limit was lost, not still running.
        if status == "running" and not state.is_live(status, started_at):
            status = "failed"
        score = row.get("risk_score")
        items.append(CaseAnalysisRunItem(
            run_id=str(row["run_id"]), status=status, started_at=started_at, finished_at=row.get("finished_at"),
            started_by=starters.get(str(row["run_id"])),
            risk_level=row.get("risk_level"), risk_score=round(score) if isinstance(score, (int, float)) else None,
            finding_count=len(row.get("findings") or []),
            stale=row.get("fingerprint") != current,
        ))
    # Oldest first, so each scored run is compared with the scored run before it.
    previous: int | None = None
    for item in reversed(items):
        if item.risk_score is None:
            continue
        if previous is not None:
            item.score_change = item.risk_score - previous
        previous = item.risk_score
    return items
