"""
Runs the per-case analysis on an officer's request: cross-document
hypotheses, the risk outlook and the plain-language summary. See
CROSS_DOCUMENT_AND_RISK_PLAN.md.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from .. import document_repository, query_stats
from ..config import settings
from ..langgraph import trace as langgraph_trace
from ..models import DocumentRecord
from . import repository, state
from .cross_document.evidence import build_context
from .cross_document.agents import PROMPT_VERSION
from .cross_document.graph import run_cross_document_analysis
from .models import AnalysisAudit, CaseAnalysis
from .risk import store as risk_store
from .trace import RunTrace
from .readiness import is_ready_for_analysis
from .risk.case_risk import compute_case_risk, scoring_breakdown
from .risk.outlook import build_outlook, outlook_trace, propose_requests
from .risk.summary import write_summary
from .risk.supporting_documents import recommend_supporting_documents

logger = logging.getLogger("classifier_pipeline.case_pipeline.service")


class AnalysisDisabled(Exception):
    pass


class AnalysisNotReady(Exception):
    pass


class AnalysisAlreadyRunning(Exception):
    pass


def _clean_checks(reports: dict[str, dict[str, Any]]) -> list[str]:
    """Checks that ran on at least one document and raised nothing anywhere in the case."""
    ran: set[str] = set()
    flagged: set[str] = set()
    for report in reports.values():
        for consulted in report.get("consulted") or []:
            ran.update(consulted.get("tools_called") or [])
        flagged.update(f.get("tool") for f in report.get("findings") or [] if f.get("tool"))
    return sorted(ran - flagged)


async def start_analysis(case_id: str) -> CaseAnalysis:
    if not settings.CASE_ANALYSIS_ENABLED:
        raise AnalysisDisabled()
    documents = await asyncio.to_thread(document_repository.list_documents, case_id=case_id)
    if not is_ready_for_analysis(documents):
        raise AnalysisNotReady()
    task = state.running_tasks.get(case_id)
    if task is not None and not task.done():
        raise AnalysisAlreadyRunning()

    reports, failed_ids = await asyncio.to_thread(
        langgraph_trace.get_detection_states, [d.id for d in documents]
    )
    run_id = str(uuid.uuid4())
    fingerprint = state.compute_fingerprint(documents, reports)
    await asyncio.to_thread(repository.insert_run, run_id, case_id, fingerprint)
    analysis = CaseAnalysis(
        run_id=run_id, case_id=case_id, status="running", fingerprint=fingerprint,
        started_at=datetime.now(timezone.utc),
    )
    state.running_tasks[case_id] = asyncio.create_task(
        _execute_scoped(analysis, documents, reports, failed_ids)
    )
    return analysis


async def _execute_scoped(
    analysis: CaseAnalysis, documents: list[DocumentRecord], reports: dict[str, dict[str, Any]],
    failed_ids: set[str],
) -> None:
    with query_stats.scope(f"case analysis case={analysis.case_id[:8]}"):
        await _execute(analysis, documents, reports, failed_ids)


async def _execute(
    analysis: CaseAnalysis, documents: list[DocumentRecord], reports: dict[str, dict[str, Any]],
    failed_ids: set[str],
) -> None:
    case_id = analysis.case_id
    trace = RunTrace(analysis.run_id)
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
        trace.record(
            "scoring", **scoring_breakdown(risk),
            cross_document_findings=len(findings),
            score_without_cross_document=document_only.score, level_without_cross_document=document_only.level,
        )
        requests = await propose_requests(risk)
        supporting_documents = recommend_supporting_documents(documents)
        trace.record("evidence_requests", proposed=[r.model_dump() for r in requests],
                     supporting_documents=[s.title for s in supporting_documents])
        outlook = build_outlook(risk, requests)
        trace.record("outlook", **outlook_trace(risk, requests, outlook))
        summary = await write_summary(risk, outlook, _clean_checks(reports))
        trace.record("summary", text=summary)

        analysis.findings = findings
        analysis.outlook = outlook
        analysis.summary = summary
        analysis.risk = risk
        analysis.supporting_documents = supporting_documents
        analysis.audit = AnalysisAudit(
            models={"analysis": settings.LANGGRAPH_SONNET_MODEL},
            prompt_version=PROMPT_VERSION,
            hypotheses_tried=len(result.get("hypotheses") or []),
            findings_discarded=len(result.get("discarded") or []),
            tool_calls=result.get("tool_calls") or [],
        )
        analysis.status = "complete"
        trace.record("done", status="complete", findings=len(findings))
    except Exception as exc:  # noqa: BLE001 - recorded on the run and retryable, never silent
        logger.exception("Case analysis failed for case %s", case_id)
        analysis.status = "failed"
        analysis.error = f"{type(exc).__name__}: {exc}"[:500]
        trace.record("error", error=analysis.error)
    finally:
        trace.save()
        try:
            published = await asyncio.to_thread(repository.finish_run, analysis)
            if published and analysis.status == "complete":
                # The case list reads the score from here rather than opening every run.
                await asyncio.to_thread(risk_store.save, case_id, analysis.risk)
        except Exception:
            logger.exception("Failed to store the case analysis result for case %s", case_id)
        state.running_tasks.pop(case_id, None)
        await _push_snapshot(case_id)


async def _push_snapshot(case_id: str) -> None:
    from .. import case_service
    from ..case_broadcaster import broadcaster

    if not broadcaster.has_subscribers(case_id):
        return
    try:
        with query_stats.scope("pushed snapshot"):
            snapshot = await asyncio.to_thread(case_service.build_case_snapshot, case_id)
    except Exception:
        logger.exception("Failed to build case snapshot for case %s", case_id)
        return
    if snapshot is not None:
        broadcaster.publish(case_id, snapshot)


def get_current(case_id: str) -> CaseAnalysis | None:
    """The case's latest analysis against the documents as they are now (sync: run on a worker thread)."""
    analysis = repository.get_latest(case_id)
    if analysis is None:
        return None
    documents = document_repository.list_documents(case_id=case_id)
    reports, _ = langgraph_trace.get_detection_states([d.id for d in documents])
    return state.current_view(analysis, documents, reports)
