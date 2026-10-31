"""
The `detection_results` table: each document's latest detection report, or why its last run failed or was
cancelled. One row per document, written once per run by langgraph/detection/graph.py and workers/detection.py.
The debug trace (langgraph/trace.py) holds the steps; this is what the case, its stage and the risk score read.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from .langgraph.detection_report import read_report
from .supabase_client import service_supabase_client

TABLE = "detection_results"
_CHUNK = 100  # keeps the in.(...) filter well under URL length limits


def _upsert(document_id: str, values: dict[str, Any]) -> None:
    # Only the columns given are updated on a conflict, so starting a run keeps nothing it shouldn't.
    service_supabase_client().table(TABLE).upsert(
        {"document_id": document_id, **values, "updated_at": datetime.now(timezone.utc).isoformat()},
        on_conflict="document_id",
    ).execute()


def mark_started(document_id: str) -> None:
    """A run is starting: an error an earlier attempt left no longer stands."""
    _upsert(document_id, {"error": None})


def save_report(document_id: str, report: dict[str, Any]) -> None:
    _upsert(document_id, {"report": report, "error": None})


def save_error(document_id: str, error: str) -> None:
    """A run crashed, was cancelled or gave up. Any report from before stays: a report always wins over an error."""
    _upsert(document_id, {"error": error})


def get(document_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """(the report, the last run's error), either None."""
    rows = (
        service_supabase_client().table(TABLE).select("report, error").eq("document_id", document_id).execute().data
    )
    if not rows:
        return None, None
    row = cast(dict, rows[0])
    return read_report(row.get("report")), row.get("error") or None


def has_report(document_id: str) -> bool:
    rows = service_supabase_client().table(TABLE).select("document_id").eq("document_id", document_id).not_.is_(
        "report", "null"
    ).execute().data
    return bool(rows)


def get_states(document_ids: list[str], *, slim: bool = False) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """
    (reports by document_id, ids whose last run failed and left no report), in one query per 100 documents.
    slim=True fetches only each report's verdict, enough to know a report exists: its value is then just
    {"verdict": ...}, for callers that only need which documents were scanned.
    """
    report_column = "report:report->verdict" if slim else "report"
    reports: dict[str, dict[str, Any]] = {}
    failed: set[str] = set()
    for start in range(0, len(document_ids), _CHUNK):
        rows = (
            service_supabase_client().table(TABLE).select(f"document_id, {report_column}, error")
            .in_("document_id", document_ids[start : start + _CHUNK]).execute().data
        )
        for raw in rows:
            row = cast(dict, raw)
            value = row.get("report")
            report = {"verdict": value} if slim and isinstance(value, str) else read_report(value)
            if report:
                reports[row["document_id"]] = report
            elif row.get("error"):
                failed.add(row["document_id"])
    return reports, failed
