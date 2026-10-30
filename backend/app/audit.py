"""
The audit trail, owned by the audit-metrics role: every domain event, stored once in `audit_events`.

Events outlive the case and document they name (no foreign keys), and the service role is granted
INSERT and SELECT only, so a row can't be edited or removed. A redelivered event is ignored by its id.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .events import Event, types
from .supabase_client import service_supabase_client
from .supabase_rows import fetch_all, row as _row

_TABLE = "audit_events"


def store(event: Event) -> None:
    service_supabase_client().table(_TABLE).upsert(
        event.to_audit_row(), on_conflict="id", ignore_duplicates=True
    ).execute()


async def on_event(event: Event) -> None:
    await asyncio.to_thread(store, event)


HANDLERS = {types.EVERY_TYPE: on_event}


def find(
    action: str, *, document_id: str | None = None, case_id: str | None = None, since: datetime | None = None,
) -> list[Event]:
    """Events of one type for a document or case, newest first. Sync: run on a worker thread."""
    query = service_supabase_client().table(_TABLE).select("*").eq("action", action).order("occurred_at", desc=True)
    if document_id is not None:
        query = query.eq("document_id", document_id)
    if case_id is not None:
        query = query.eq("case_id", case_id)
    if since is not None:
        query = query.gt("occurred_at", since.isoformat())
    return [Event.from_audit_row(_row(row)) for row in query.limit(50).execute().data or []]


def latest_time(actions: list[str], document_id: str) -> datetime | None:
    """When the newest of these event types happened for a document."""
    rows = (
        service_supabase_client().table(_TABLE).select("occurred_at").eq("document_id", document_id)
        .in_("action", actions).order("occurred_at", desc=True).limit(1).execute().data
    )
    return datetime.fromisoformat(str(_row(rows[0])["occurred_at"])) if rows else None


# The stages a case's processing time adds up, each as the case page shows it (pages/upload/processingTimes.ts).
_TIMED = [types.DOCUMENT_ANALYZED, types.DOCUMENT_DETECTED, types.CASE_ANALYSIS_COMPLETED]
_CASE_CHUNK = 100


def processing_ms_many(case_ids: list[str]) -> dict[str, int]:
    """
    Each analysed case's automated processing time: the average extraction run plus the average detection run
    of its documents, plus its latest case analysis. Cases missing any of the three stages are absent.
    Sync: run on a worker thread.
    """
    rows: list[dict] = []
    for start in range(0, len(case_ids), _CASE_CHUNK):
        chunk = case_ids[start : start + _CASE_CHUNK]
        rows += fetch_all(
            lambda chunk=chunk: service_supabase_client().table(_TABLE)
            .select("id, case_id, action, duration_ms, occurred_at")
            .in_("case_id", chunk).in_("action", _TIMED).eq("outcome", "success"),
            "id",
        )
    by_case: dict[str, dict[str, list[tuple[str, int]]]] = {}
    for row in rows:
        if row.get("duration_ms") is not None:
            by_case.setdefault(row["case_id"], {}).setdefault(row["action"], []).append(
                (str(row["occurred_at"]), int(row["duration_ms"]))
            )
    totals: dict[str, int] = {}
    for case_id, stages in by_case.items():
        # A case processed before the trail recorded timings has only some stages: no total rather than a short one.
        if any(not stages.get(action) for action in _TIMED):
            continue
        total = max(stages[types.CASE_ANALYSIS_COMPLETED])[1]  # the latest run
        for action in (types.DOCUMENT_ANALYZED, types.DOCUMENT_DETECTED):
            durations = [ms for _, ms in stages[action]]
            total += sum(durations) / len(durations)
        totals[case_id] = round(total)
    return totals


def created_by_many(case_ids: list[str]) -> dict[str, str]:
    """
    The officer who created each case, from its case.created event. Cases created before the trail existed, or by
    the system, are absent. Sync: run on a worker thread.
    """
    rows: list[dict] = []
    for start in range(0, len(case_ids), _CASE_CHUNK):
        chunk = case_ids[start : start + _CASE_CHUNK]
        rows += fetch_all(
            lambda chunk=chunk: service_supabase_client().table(_TABLE).select("id, case_id, actor")
            .in_("case_id", chunk).eq("action", types.CASE_CREATED).eq("actor_type", "user"),
            "id",
        )
    return {row["case_id"]: row["actor"] for row in rows if row.get("actor")}


# End-of-run events that carry a run's Claude usage (detail.llm_cost_usd and the token counts).
_COSTED = [
    types.DOCUMENT_ANALYZED, types.DOCUMENT_FAILED, types.DOCUMENT_DETECTED, types.DETECTION_FAILED,
    types.CASE_ANALYSIS_COMPLETED,
]


class CaseCostRun(BaseModel):
    """One costed run, at list price."""
    occurred_at: datetime
    type: str
    run: Literal["document", "extraction_rerun", "detection", "case_analysis"] | None = None
    document_id: str | None = None
    llm_cost_usd: float
    llm_calls: int | None = None
    llm_cache_read_tokens: int | None = None


class CaseCostTiming(BaseModel):
    """A finished run and how long it took (success only)."""
    occurred_at: datetime
    type: str
    document_id: str | None = None
    duration_ms: int


class CaseCost(BaseModel):
    case_id: str
    total_usd: float
    documents_usd: dict[str, float]
    case_analysis_usd: float
    runs: list[CaseCostRun]
    timings: list[CaseCostTiming]


def case_cost(case_id: str) -> CaseCost:
    """
    A case's Claude spend from its audit trail: each costed run, a total per document, and the case total.
    Runs from before the trail carried cost are absent. Also how long each finished run took (`timings`, whether
    or not it was costed). Sync: run on a worker thread.
    """
    rows = (
        service_supabase_client().table(_TABLE).select("*").eq("case_id", case_id).in_("action", _COSTED)
        .order("occurred_at").limit(1000).execute().data or []
    )
    runs: list[CaseCostRun] = []
    timings: list[CaseCostTiming] = []
    per_document: dict[str, float] = {}
    for event in (Event.from_audit_row(_row(row)) for row in rows):
        if event.outcome == "success" and event.duration_ms is not None:
            timings.append(CaseCostTiming(
                occurred_at=event.occurred_at, type=event.type, document_id=event.document_id,
                duration_ms=event.duration_ms,
            ))
        cost = event.detail.get("llm_cost_usd")
        if cost is None:
            continue
        runs.append(CaseCostRun(
            occurred_at=event.occurred_at, type=event.type, run=event.detail.get("run"),
            document_id=event.document_id, llm_cost_usd=float(cost), llm_calls=event.detail.get("llm_calls"),
            llm_cache_read_tokens=event.detail.get("llm_cache_read_tokens"),
        ))
        if event.document_id:
            per_document[event.document_id] = per_document.get(event.document_id, 0.0) + float(cost)
    return CaseCost(
        case_id=case_id, total_usd=round(sum(r.llm_cost_usd for r in runs), 6),
        documents_usd={doc: round(usd, 6) for doc, usd in per_document.items()},
        case_analysis_usd=round(sum(r.llm_cost_usd for r in runs if r.run == "case_analysis"), 6),
        runs=runs, timings=timings,
    )


def list_events(
    *, case_id: str | None = None, document_id: str | None = None, event_type: str | None = None, limit: int = 200
) -> list[Event]:
    """Newest first. Sync: run on a worker thread."""
    query = service_supabase_client().table(_TABLE).select("*").order("occurred_at", desc=True).limit(limit)
    if case_id is not None:
        query = query.eq("case_id", case_id)
    if document_id is not None:
        query = query.eq("document_id", document_id)
    if event_type is not None:
        # The event's type is stored as `action` (Event.to_audit_row).
        query = query.eq("action", event_type)
    return [Event.from_audit_row(_row(row)) for row in query.execute().data or []]
