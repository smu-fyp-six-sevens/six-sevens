"""
The audit trail, owned by the audit-metrics role: every domain event, stored once in `audit_events`.

Events outlive the case and document they name (no foreign keys), and the service role is granted
INSERT and SELECT only, so a row can't be edited or removed. A redelivered event is ignored by its id.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from .events import Event, types
from .supabase_client import service_supabase_client
from .supabase_rows import row as _row

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


def list_events(
    *, case_id: str | None = None, document_id: str | None = None, limit: int = 200
) -> list[Event]:
    """Newest first. Sync: run on a worker thread."""
    query = service_supabase_client().table(_TABLE).select("*").order("occurred_at", desc=True).limit(limit)
    if case_id is not None:
        query = query.eq("case_id", case_id)
    if document_id is not None:
        query = query.eq("document_id", document_id)
    return [Event.from_audit_row(_row(row)) for row in query.execute().data or []]
