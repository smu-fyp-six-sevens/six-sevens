from __future__ import annotations

from fastapi import APIRouter, Query

from .. import audit
from ..events import Event
from ._dependencies import to_thread_or_502

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", response_model=list[Event])
async def list_audit_events(
    case_id: str | None = None, document_id: str | None = None, limit: int = Query(200, ge=1, le=1000)
) -> list[Event]:
    """The audit trail, newest first, optionally for one case or document."""
    return await to_thread_or_502(
        audit.list_events, case_id=case_id, document_id=document_id, limit=limit,
        detail="Failed to read the audit trail. Check Supabase table access.",
        log=("Failed to read audit events",),
    )
