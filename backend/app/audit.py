"""
The audit trail, owned by the audit-metrics role: every domain event, stored once in `audit_events`.

Events outlive the case and document they name (no foreign keys), and the service role is granted
INSERT and SELECT only, so a row can't be edited or removed. A redelivered event is ignored by its id.
"""

from __future__ import annotations

import asyncio

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
