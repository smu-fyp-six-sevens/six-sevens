"""
The audit trail, owned by the audit-metrics role: every domain event, stored once in `audit_events`.

Events outlive the case and document they name (no foreign keys), and the service role is granted
INSERT and SELECT only, so a row can't be edited or removed. A redelivered event is ignored by its id.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Literal, cast

from pydantic import BaseModel

from . import case_repository
from .events import Event, types
from .supabase_client import service_supabase_client
from .supabase_rows import fetch_all, row as _row

logger = logging.getLogger(__name__)

_TABLE = "audit_events"


def store(event: Event) -> None:
    service_supabase_client().table(_TABLE).upsert(
        event.to_audit_row(), on_conflict="id", ignore_duplicates=True
    ).execute()


def refresh_processing_time(case_id: str) -> None:
    """Rewrites the case's stored processing time (cases.processing_ms, which the case list shows) from its trail.
    Best-effort: the list shows the previous value until the next timed run."""
    try:
        total = processing_ms_many([case_id]).get(case_id)
        if total is not None:
            case_repository.set_processing_ms(case_id, total)
    except Exception:
        logger.exception("Failed to refresh the processing time of case %s", case_id)


async def on_event(event: Event) -> None:
    await asyncio.to_thread(store, event)
    if event.type in _TIMED and event.outcome == "success" and event.case_id is not None:
        await asyncio.to_thread(refresh_processing_time, str(event.case_id))


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


def latest_times(actions: list[str], document_ids: list[str]) -> dict[str, datetime]:
    """When the newest of these event types happened, per document, a query per 100: one with none is absent."""
    latest: dict[str, datetime] = {}
    for start in range(0, len(document_ids), _CASE_CHUNK):
        chunk = document_ids[start : start + _CASE_CHUNK]
        for row in fetch_all(
            lambda chunk=chunk: service_supabase_client().table(_TABLE).select("id, document_id, occurred_at")
            .in_("document_id", chunk).in_("action", actions),
            "id",
        ):
            when = datetime.fromisoformat(str(row["occurred_at"]))
            if row["document_id"] not in latest or when > latest[row["document_id"]]:
                latest[row["document_id"]] = when
    return latest


def find_many(
    action: str, *, document_ids: list[str] | None = None, case_ids: list[str] | None = None,
    since: datetime | None = None,
) -> list[Event]:
    """find for many documents (or many cases), a query per 100, newest first. Sync: run on a worker thread."""
    column, ids = ("document_id", document_ids) if document_ids is not None else ("case_id", case_ids or [])
    events: list[Event] = []
    for start in range(0, len(ids), _CASE_CHUNK):
        chunk = ids[start : start + _CASE_CHUNK]

        def build(chunk: list[str] = chunk):
            query = service_supabase_client().table(_TABLE).select("*").eq("action", action).in_(column, chunk)
            return query.gt("occurred_at", since.isoformat()) if since is not None else query

        events += _events(build)
    return sorted(events, key=lambda e: e.occurred_at, reverse=True)


def ran_for(trigger_event_id: str, actions: list[str], document_id: str, since: datetime) -> bool:
    """Whether a run's end event (one of `actions`) names this event as what started it (detail.trigger_event_id)."""
    rows = (
        service_supabase_client().table(_TABLE).select("id").eq("document_id", document_id).in_("action", actions)
        .gte("occurred_at", since.isoformat()).eq("detail->>trigger_event_id", trigger_event_id).limit(1).execute().data
    )
    return bool(rows)




# The stages a case's processing time adds up, each as the case page shows it (pages/upload/processingTimes.ts).
_TIMED = [types.DOCUMENT_ANALYZED, types.DOCUMENT_DETECTED, types.CASE_ANALYSIS_COMPLETED]
_CASE_CHUNK = 100
# Ids per case_cost_totals call: it returns one row per case, and a response holds at most 1,000 rows.
_COST_CHUNK = 500


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


# End-of-run events that carry a run's Claude usage (detail.llm_cost_usd and the token counts): finished, failed,
# cancelled or cut short alike.
_COSTED = [
    types.DOCUMENT_ANALYZED, types.DOCUMENT_FAILED, types.DOCUMENT_RUN_STOPPED, types.DOCUMENT_DETECTED,
    types.DETECTION_FAILED, types.CASE_ANALYSIS_COMPLETED,
]
# Events that name a document's file.
_NAMED = [types.DOCUMENT_UPLOADED, types.DOCUMENT_DELETED]


class CaseCostRun(BaseModel):
    """One costed run, at list price."""
    occurred_at: datetime
    type: str
    run: Literal["document", "extraction_rerun", "detection", "case_analysis"] | None = None
    document_id: str | None = None
    outcome: Literal["success", "failed", "cancelled"] = "success"
    # 1 for a document's (or the case's) first run of this kind, 2 for the next (a retry or rerun), and so on.
    attempt: int = 1
    llm_cost_usd: float
    llm_calls: int | None = None
    # Every input token, cached or not; the cache reads and writes are part of it.
    llm_input_tokens: int | None = None
    llm_output_tokens: int | None = None
    llm_cache_read_tokens: int | None = None
    llm_cache_write_tokens: int | None = None
    # What the cache saved, net of the writes' extra cost.
    llm_cache_saved_usd: float = 0.0


class CacheUse(BaseModel):
    """How well prompt caching worked over some runs."""
    runs: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Share of the input tokens read from the cache (0-1); None with no input.
    hit_rate: float | None = None
    saved_usd: float = 0.0


def cache_use(runs: list[CaseCostRun]) -> CacheUse:
    counted = [r for r in runs if r.llm_input_tokens is not None]
    input_tokens = sum(r.llm_input_tokens or 0 for r in counted)
    read = sum(r.llm_cache_read_tokens or 0 for r in counted)
    return CacheUse(
        runs=len(counted), input_tokens=input_tokens, cache_read_tokens=read,
        cache_write_tokens=sum(r.llm_cache_write_tokens or 0 for r in counted),
        hit_rate=round(read / input_tokens, 4) if input_tokens else None,
        saved_usd=round(sum(r.llm_cache_saved_usd for r in counted), 6),
    )


class CaseCostDocument(BaseModel):
    """A document a costed run was on: its file name, whether it is still in the case, and whether it was deleted."""
    filename: str | None = None
    in_case: bool = True
    deleted: bool = False


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
    # Every document in documents_usd or runs, current or deleted.
    documents: dict[str, CaseCostDocument] = {}
    case_analysis_usd: float
    # What failed, cancelled and repeated runs cost: included in total_usd.
    failed_or_cancelled_usd: float = 0.0
    repeated_runs_usd: float = 0.0
    # Prompt caching over every costed run, and per kind of run (document, extraction_rerun, detection, case_analysis).
    cache: CacheUse = CacheUse()
    cache_by_run: dict[str, CacheUse] = {}
    runs: list[CaseCostRun]
    timings: list[CaseCostTiming]


def cost_usd_many(case_ids: list[str]) -> dict[str, float]:
    """
    Each case's Claude spend at list price, for the dashboard: every costed run filed under the case (finished,
    failed, cancelled, retried and rerun alike), added up by the database (case_cost_totals). Unlike case_cost, a
    document's runs from before it was moved into the case stay with its old case: close enough for totals across
    many cases. Cases with no costed run are absent. Sync: run on a worker thread.
    """
    totals: dict[str, float] = {}
    for start in range(0, len(case_ids), _COST_CHUNK):
        chunk = case_ids[start : start + _COST_CHUNK]
        rows = cast(list[dict[str, Any]], service_supabase_client().rpc(
            "case_cost_totals", {"p_case_ids": chunk, "p_actions": _COSTED},
        ).execute().data or [])
        for row in rows:
            if row.get("cost_usd") is not None:
                totals[str(row["case_id"])] = round(float(row["cost_usd"]), 6)
    return totals


def _events(build) -> list[Event]:
    return [Event.from_audit_row(row) for row in fetch_all(build, "id")]


def case_cost(case_id: str, document_ids: list[str]) -> CaseCost:
    """
    A case's Claude spend from its audit trail: each costed run (finished, failed, cancelled or cut short, retries
    and reruns included), a total per document, and the case total. `document_ids` are the case's documents now: a
    document moved in brings the runs it had before the move. Runs on documents since deleted stay, named from the
    upload or delete event. A run whose event carries no cost is absent. Also how long each finished run took
    (`timings`, whether or not it was costed). Sync: run on a worker thread.
    """
    client = service_supabase_client()
    by_id: dict[str, Event] = {
        e.event_id: e for e in _events(lambda: client.table(_TABLE).select("*").eq("case_id", case_id).in_("action", _COSTED))
    }
    for start in range(0, len(document_ids), _CASE_CHUNK):
        chunk = document_ids[start : start + _CASE_CHUNK]
        for event in _events(
            lambda chunk=chunk: client.table(_TABLE).select("*").in_("document_id", chunk).in_("action", _COSTED)
        ):
            by_id[event.event_id] = event
    events_in_order = sorted(by_id.values(), key=lambda e: e.occurred_at)

    runs: list[CaseCostRun] = []
    timings: list[CaseCostTiming] = []
    per_document: dict[str, float] = {}
    attempts: dict[tuple[str | None, str | None], int] = {}
    for event in events_in_order:
        if event.outcome == "success" and event.duration_ms is not None:
            timings.append(CaseCostTiming(
                occurred_at=event.occurred_at, type=event.type, document_id=event.document_id,
                duration_ms=event.duration_ms,
            ))
        cost = event.detail.get("llm_cost_usd")
        if cost is None:
            continue
        kind = event.detail.get("run")
        attempts[(event.document_id, kind)] = attempts.get((event.document_id, kind), 0) + 1
        runs.append(CaseCostRun(
            occurred_at=event.occurred_at, type=event.type, run=kind, document_id=event.document_id,
            outcome=event.outcome, attempt=attempts[(event.document_id, kind)], llm_cost_usd=float(cost),
            llm_calls=event.detail.get("llm_calls"), llm_input_tokens=event.detail.get("llm_input_tokens"),
            llm_output_tokens=event.detail.get("llm_output_tokens"),
            llm_cache_read_tokens=event.detail.get("llm_cache_read_tokens"),
            llm_cache_write_tokens=event.detail.get("llm_cache_write_tokens"),
            llm_cache_saved_usd=event.detail.get("llm_cache_saved_usd", 0.0),
        ))
        if event.document_id:
            per_document[event.document_id] = per_document.get(event.document_id, 0.0) + float(cost)

    named = {r.document_id for r in runs if r.document_id}
    current = set(document_ids)
    documents = {document_id: CaseCostDocument(in_case=document_id in current) for document_id in named}
    for start in range(0, len(named), _CASE_CHUNK):
        chunk = sorted(named)[start : start + _CASE_CHUNK]
        for event in _events(
            lambda chunk=chunk: client.table(_TABLE).select("*").in_("document_id", chunk).in_("action", _NAMED)
        ):
            document = documents[str(event.document_id)]
            document.filename = event.detail.get("filename") or document.filename
            document.deleted = document.deleted or (event.type == types.DOCUMENT_DELETED and event.document_id not in current)

    return CaseCost(
        case_id=case_id, total_usd=round(sum(r.llm_cost_usd for r in runs), 6),
        documents_usd={doc: round(usd, 6) for doc, usd in per_document.items()},
        documents=documents,
        case_analysis_usd=round(sum(r.llm_cost_usd for r in runs if r.run == "case_analysis"), 6),
        failed_or_cancelled_usd=round(sum(r.llm_cost_usd for r in runs if r.outcome != "success"), 6),
        repeated_runs_usd=round(sum(r.llm_cost_usd for r in runs if r.attempt > 1), 6),
        cache=cache_use(runs),
        cache_by_run={kind: cache_use([r for r in runs if r.run == kind]) for kind in sorted({r.run for r in runs if r.run})},
        runs=runs, timings=timings,
    )


def list_events(
    *, case_id: str | None = None, document_id: str | None = None, event_type: str | None = None, limit: int = 200,
    before: datetime | None = None,
) -> list[Event]:
    """Newest first; `before` pages back by occurred_at. Sync: run on a worker thread."""
    query = service_supabase_client().table(_TABLE).select("*").order("occurred_at", desc=True).limit(limit)
    if before is not None:
        query = query.lt("occurred_at", before.isoformat())
    if case_id is not None:
        query = query.eq("case_id", case_id)
    if document_id is not None:
        query = query.eq("document_id", document_id)
    if event_type is not None:
        # The event's type is stored as `action` (Event.to_audit_row).
        query = query.eq("action", event_type)
    return [Event.from_audit_row(_row(row)) for row in query.execute().data or []]
