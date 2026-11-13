from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query

from .. import audit, document_repository
from ..events import Event
from ._dependencies import to_thread_or_502

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", response_model=list[Event])
async def list_audit_events(
    case_id: str | None = None, document_id: str | None = None, limit: int = Query(200, ge=1, le=1000),
    before: datetime | None = None,
) -> list[Event]:
    """The audit trail, newest first, optionally for one case or document. `before`: only events older than this (the next page)."""
    return await to_thread_or_502(
        audit.list_events, case_id=case_id, document_id=document_id, limit=limit, before=before,
        detail="Failed to read the audit trail. Check Supabase table access.",
        log=("Failed to read audit events",),
    )


def _case_cost(case_id: str) -> audit.CaseCost:
    documents = document_repository.list_documents(case_id=case_id, include_fields=False)
    return audit.case_cost(case_id, [document.id for document in documents])


@router.get("/costs")
async def get_case_costs(case_id: list[str] = Query(default=[], max_length=500)) -> dict[str, float]:
    """
    Claude spend at list price for each of these cases (repeat case_id), from the audit trail: the dashboard's
    totals. A case with no costed run is absent. Runs a document had before being moved into a case are counted
    with its old case; GET /audit/cases/{case_id}/cost has a case's exact breakdown.
    """
    return await to_thread_or_502(
        audit.cost_usd_many, case_id,
        detail="Failed to read the audit trail. Check Supabase table access.",
        log=("Failed to read the cost of %d cases", len(case_id)),
    )


@router.get("/cases/{case_id}/cost")
async def get_case_cost(case_id: str) -> audit.CaseCost:
    """
    Claude spend for one case at list price: per run, per document and in total, from the audit trail. Every run
    counts: failed, cancelled, retried and rerun ones, earlier case analyses, and runs on documents since deleted.
    """
    return await to_thread_or_502(
        _case_cost, case_id,
        detail="Failed to read the audit trail. Check Supabase table access.",
        log=("Failed to read the cost of case %s", case_id),
    )
