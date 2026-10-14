"""Storage for per-case analysis runs (migrations/002_case_analysis_runs.sql)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, cast

from ..supabase_client import supabase_client
from .models import AnalysisAudit, CaseAnalysis, CrossDocFinding, RiskOutlook

_TABLE = "case_analysis_runs"
_CHUNK = 100


def _row(raw: Any) -> dict:
    return cast(dict, raw)


def _to_analysis(row: dict) -> CaseAnalysis:
    result = row.get("result") or {}
    return CaseAnalysis(
        run_id=row["run_id"],
        case_id=row["case_id"],
        status=row["status"],
        fingerprint=row["fingerprint"],
        started_at=row["started_at"],
        finished_at=row.get("finished_at"),
        error=row.get("error"),
        findings=[CrossDocFinding(**f) for f in result.get("findings") or []],
        outlook=RiskOutlook(**result["outlook"]) if result.get("outlook") else None,
        summary=result.get("summary"),
        audit=AnalysisAudit(**(result.get("audit") or {})),
    )


def insert_run(run_id: str, case_id: str, fingerprint: str) -> None:
    supabase_client.table(_TABLE).insert({
        "run_id": run_id,
        "case_id": case_id,
        "status": "running",
        "fingerprint": fingerprint,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


def latest_run_id(case_id: str) -> Optional[str]:
    rows = (
        supabase_client.table(_TABLE).select("run_id").eq("case_id", case_id)
        .order("started_at", desc=True).limit(1).execute().data
    )
    return _row(rows[0])["run_id"] if rows else None


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
        "outlook": analysis.outlook.model_dump(mode="json") if analysis.outlook else None,
        "summary": analysis.summary,
        "audit": analysis.audit.model_dump(mode="json"),
    }
    supabase_client.table(_TABLE).update({
        "status": "superseded" if superseded else analysis.status,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "error": analysis.error,
        "result": result,
    }).eq("run_id", analysis.run_id).execute()
    return not superseded


def get_latest(case_id: str) -> Optional[CaseAnalysis]:
    rows = (
        supabase_client.table(_TABLE).select("*").eq("case_id", case_id)
        .order("started_at", desc=True).limit(1).execute().data
    )
    return _to_analysis(_row(rows[0])) if rows else None


def get_latest_many(case_ids: list[str]) -> dict[str, CaseAnalysis]:
    """The newest run of each case, in one query per 100 cases."""
    latest: dict[str, CaseAnalysis] = {}
    for start in range(0, len(case_ids), _CHUNK):
        rows = (
            supabase_client.table(_TABLE).select("*").in_("case_id", case_ids[start : start + _CHUNK])
            .order("started_at", desc=True).execute().data
        )
        for raw in rows:
            row = _row(raw)
            latest.setdefault(row["case_id"], _to_analysis(row))
    return latest
