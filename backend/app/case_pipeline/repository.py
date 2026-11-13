"""Storage for per-case analysis runs (the `case_analysis_runs` table)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from postgrest.exceptions import APIError
from postgrest import CountMethod

from ..config import settings
from ..models import CaseRisk, SupportingDocumentRecommendation
from ..supabase_client import supabase_client
from ..supabase_rows import fetch_all
from ..supabase_rows import row as _row
from .models import AdverseMediaCheck, AnalysisAudit, AnalysisSummary, CaseAnalysis, CrossDocFinding, RiskOutlook

_TABLE = "case_analysis_runs"
# What a snapshot or summary needs from a run: no trace, outlook or audit, which are large.
_LIST_COLUMNS = (
    "run_id, case_id, status, fingerprint, started_at, finished_at, error, confidence:result->confidence, findings:result->findings, "
    "risk:result->risk, supporting_documents:result->supporting_documents"
)
# A case's list of runs: when, how it ended, its score and how many findings, without the result itself.
_RUN_LIST_COLUMNS = (
    "run_id, status, fingerprint, started_at, finished_at, risk_score:result->risk->score, "
    "risk_level:result->risk->level, findings:result->findings"
)


def _to_analysis(row: dict) -> CaseAnalysis:
    result: dict[str, Any] = row.get("result") or {}
    if "findings" in row:  # a list read selects only these keys out of result
        result = {key: row.get(key) for key in ("confidence", "findings", "risk", "supporting_documents")}
    return CaseAnalysis(
        run_id=row["run_id"],
        case_id=row["case_id"],
        status=row["status"],
        fingerprint=row["fingerprint"],
        started_at=row["started_at"],
        finished_at=row.get("finished_at"),
        error=row.get("error"),
        # A list read always has the key: None for a run with no result yet, or from before confidence was recorded.
        confidence=result.get("confidence") or 0.0,
        findings=[CrossDocFinding(**f) for f in result.get("findings") or []],
        outlook=RiskOutlook(**result["outlook"]) if result.get("outlook") else None,
        structured_summary=AnalysisSummary(**result["structured_summary"]) if result.get("structured_summary") else None,
        risk=CaseRisk(**result["risk"]) if result.get("risk") else None,
        supporting_documents=[SupportingDocumentRecommendation(**s) for s in result.get("supporting_documents") or []],
        consistent_checks=[str(c) for c in result.get("consistent_checks") or []],
        clean_checks=[str(c) for c in result.get("clean_checks") or []],
        adverse_media=AdverseMediaCheck(**result["adverse_media"]) if result.get("adverse_media") else None,
        audit=AnalysisAudit(**(result.get("audit") or {})),
        trace=row.get("trace") or [],
    )


class RunAlreadyStarted(Exception):
    """The case already has a live running run over the same inputs (another instance started it)."""


def insert_run(run_id: str, case_id: str, fingerprint: str) -> None:
    """Records a running run. The database allows one running run per case and fingerprint, across instances."""
    row = {
        "run_id": run_id,
        "case_id": case_id,
        "status": "running",
        "fingerprint": fingerprint,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        supabase_client.table(_TABLE).insert(row).execute()
        return
    except APIError as exc:
        if exc.code != "23505":  # unique violation
            raise
        # A running run past its time limit lost its worker: it shouldn't hold the slot until the sweeper comes.
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=settings.CASE_ANALYSIS_TIMEOUT_SECONDS)
        lost = supabase_client.table(_TABLE).update({
            "status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(),
            "error": "The analysis stopped without finishing. It was started again.",
        }).eq("case_id", case_id).eq("fingerprint", fingerprint).eq("status", "running") \
            .lt("started_at", cutoff.isoformat()).execute()
        if not lost.data:
            raise RunAlreadyStarted() from exc
    try:
        supabase_client.table(_TABLE).insert(row).execute()
    except APIError as exc:
        if exc.code == "23505":
            raise RunAlreadyStarted() from exc
        raise


def claim_run(run_id: str) -> bool:
    """
    Takes a requested run for a worker, once (compare-and-swap): whether it did. A redelivered request finds the run
    taken, or already ended, and has nothing to do.
    """
    result = (
        supabase_client.table(_TABLE).update({"worker_started_at": datetime.now(timezone.utc).isoformat()})
        .eq("run_id", run_id).eq("status", "running").is_("worker_started_at", "null").execute()
    )
    return bool(result.data)


def write_trace(run_id: str, events: list[dict[str, Any]]) -> None:
    supabase_client.table(_TABLE).update({"trace": events}).eq("run_id", run_id).execute()


def latest_run_id(case_id: str) -> str | None:
    rows = (
        supabase_client.table(_TABLE).select("run_id").eq("case_id", case_id)
        .order("started_at", desc=True).limit(1).execute().data
    )
    return _row(rows[0])["run_id"] if rows else None


def latest_run_state(case_id: str) -> tuple[str, datetime] | None:
    """The newest run's status and start, without its large result and trace."""
    brief = latest_run_brief(case_id)
    return (brief[0], brief[1]) if brief else None


def latest_run_brief(case_id: str) -> tuple[str, datetime, str] | None:
    """The newest run's status, start and fingerprint, without its large result and trace."""
    rows = (
        supabase_client.table(_TABLE).select("status, started_at, fingerprint").eq("case_id", case_id)
        .order("started_at", desc=True).limit(1).execute().data
    )
    if not rows:
        return None
    row = _row(rows[0])
    return str(row["status"]), datetime.fromisoformat(str(row["started_at"])), str(row["fingerprint"])


def latest_adverse_media(case_id: str) -> AdverseMediaCheck | None:
    """The media check of the case's newest complete run that has one, without reading the rest of the result."""
    rows = (
        supabase_client.table(_TABLE).select("adverse_media:result->adverse_media").eq("case_id", case_id)
        .eq("status", "complete").order("started_at", desc=True).limit(5).execute().data or []
    )
    for row in rows:
        stored = _row(row).get("adverse_media")
        if stored:
            return AdverseMediaCheck(**stored)
    return None


def count_runs_since(case_id: str, since: datetime) -> int:
    result = (
        supabase_client.table(_TABLE).select("run_id", count=CountMethod.exact).eq("case_id", case_id)
        .gte("started_at", since.isoformat()).limit(1).execute()
    )
    return result.count or 0


def running_started_before(cutoff: datetime) -> list[tuple[str, str, datetime]]:
    """(run_id, case_id, started_at) of runs still marked running that started before `cutoff`."""
    rows = fetch_all(
        lambda: supabase_client.table(_TABLE).select("run_id, case_id, started_at").eq("status", "running")
        .lt("started_at", cutoff.isoformat()),
        "run_id",
    )
    return [
        (str(r["run_id"]), str(r["case_id"]), datetime.fromisoformat(str(r["started_at"])))
        for r in (_row(row) for row in rows)
    ]


def fail_if_running(run_id: str, error: str) -> bool:
    """Marks a run failed only while it is still running (compare-and-swap); whether it was."""
    result = supabase_client.table(_TABLE).update({
        "status": "failed", "finished_at": datetime.now(timezone.utc).isoformat(), "error": error,
    }).eq("run_id", run_id).eq("status", "running").execute()
    return bool(result.data)


def finish_run(analysis: CaseAnalysis) -> bool:
    """
    Writes a run's outcome only while it is still the case's newest run.
    A run that was replaced in the meantime is recorded as superseded and
    its result is dropped, so a result over outdated inputs is never published.
    """
    current = latest_run_id(analysis.case_id)
    superseded = current is not None and current != analysis.run_id
    result = {} if superseded else {
        "findings": [f.model_dump(mode="json") for f in analysis.findings],
        "confidence": analysis.confidence,
        "outlook": analysis.outlook.model_dump(mode="json") if analysis.outlook else None,
        "structured_summary": analysis.structured_summary.model_dump(mode="json") if analysis.structured_summary else None,
        "risk": analysis.risk.model_dump(mode="json") if analysis.risk else None,
        "supporting_documents": [s.model_dump(mode="json") for s in analysis.supporting_documents],
        "consistent_checks": analysis.consistent_checks,
        "clean_checks": analysis.clean_checks,
        "adverse_media": analysis.adverse_media.model_dump(mode="json") if analysis.adverse_media else None,
        "audit": analysis.audit.model_dump(mode="json"),
    }
    supabase_client.table(_TABLE).update({
        "status": "superseded" if superseded else analysis.status,
        # What the run read, which may differ from what it was asked for (service.run_requested).
        "fingerprint": analysis.fingerprint,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "error": analysis.error,
        "result": result,
    }).eq("run_id", analysis.run_id).execute()
    return not superseded


def get_latest(case_id: str) -> CaseAnalysis | None:
    rows = (
        supabase_client.table(_TABLE).select("*").eq("case_id", case_id)
        .order("started_at", desc=True).limit(1).execute().data
    )
    return _to_analysis(_row(rows[0])) if rows else None


def get_latest_complete(case_id: str) -> CaseAnalysis | None:
    """The newest finished run (findings, risk and evidence requests only), for when a newer one is still going."""
    rows = (
        supabase_client.table(_TABLE).select(_LIST_COLUMNS).eq("case_id", case_id).eq("status", "complete")
        .order("started_at", desc=True).limit(1).execute().data
    )
    return _to_analysis(_row(rows[0])) if rows else None


def get_latest_summary(case_id: str) -> CaseAnalysis | None:
    """The case's newest run, findings only (no trace, outlook or audit): one row however many runs there are."""
    rows = (
        supabase_client.table(_TABLE).select(_LIST_COLUMNS).eq("case_id", case_id)
        .order("started_at", desc=True).limit(1).execute().data
    )
    return _to_analysis(_row(rows[0])) if rows else None


def list_runs(case_id: str) -> list[dict[str, Any]]:
    """Every run of the case, newest first: status, times, fingerprint, score, level and findings (no trace)."""
    rows = fetch_all(lambda: supabase_client.table(_TABLE).select(_RUN_LIST_COLUMNS).eq("case_id", case_id), "run_id")
    return sorted(rows, key=lambda row: str(row["started_at"]), reverse=True)


def get_run(case_id: str, run_id: str) -> CaseAnalysis | None:
    """One of the case's runs, whole, or None when the case has no run with that id."""
    rows = (
        supabase_client.table(_TABLE).select("*").eq("case_id", case_id).eq("run_id", run_id)
        .limit(1).execute().data
    )
    return _to_analysis(_row(rows[0])) if rows else None
