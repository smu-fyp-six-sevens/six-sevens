"""
The audit trail: an append-only `audit_events` table recording who (an officer, an
agent or the system) did what, to which case or document, how long it took, and how
it ended. See backend/sql/audit_events.sql.

Best effort by design: a failed write is logged and never fails the action it
describes. Events outlive the case and document they name (no foreign keys), and the
service role is granted INSERT and SELECT only, so a row can't be edited or removed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, Field

from .supabase_client import service_supabase_client

logger = logging.getLogger(__name__)

_TABLE = "audit_events"
ActorType = Literal["user", "agent", "system"]
Outcome = Literal["success", "failed", "cancelled"]
_ERROR_CHARS = 300


class AuditEvent(BaseModel):
    id: str
    occurred_at: datetime
    actor_type: ActorType
    actor: str
    action: str
    case_id: str | None = None
    document_id: str | None = None
    outcome: Outcome = "success"
    started_at: datetime | None = None
    duration_ms: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class Stopwatch:
    """Start one before the work; hand it to `record` after to store when it began and how long it took."""

    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc)
        self._started = time.perf_counter()

    @property
    def duration_ms(self) -> int:
        return int((time.perf_counter() - self._started) * 1000)


def _insert(row: dict[str, Any]) -> None:
    service_supabase_client().table(_TABLE).insert(row).execute()


def record(
    action: str,
    actor: str,
    *,
    actor_type: ActorType = "user",
    case_id: str | None = None,
    document_id: str | None = None,
    outcome: Outcome = "success",
    timer: Stopwatch | None = None,
    **detail: Any,
) -> None:
    """Writes one event. Never raises. Keep `detail` small and free of protected attributes."""
    try:
        _insert({
            "actor_type": actor_type,
            "actor": actor,
            "action": action,
            "case_id": case_id,
            "document_id": document_id,
            "outcome": outcome,
            "started_at": timer.started_at.isoformat() if timer else None,
            "duration_ms": timer.duration_ms if timer else None,
            "detail": json.loads(json.dumps(detail, default=str)),
        })
    except Exception:
        logger.exception("Failed to write audit event %s by %s", action, actor)


async def arecord(action: str, actor: str, **kwargs: Any) -> None:
    """`record` from async code."""
    await asyncio.to_thread(record, action, actor, **kwargs)


@asynccontextmanager
async def timed(
    action: str,
    actor: str,
    *,
    actor_type: ActorType = "agent",
    case_id: str | None = None,
    document_id: str | None = None,
    **detail: Any,
) -> AsyncIterator[dict[str, Any]]:
    """
    Times the block and records it afterwards: success, failed (with the error) or cancelled.
    The block may add to the yielded dict to put results in the event's detail.
    """
    timer = Stopwatch()
    extra: dict[str, Any] = {}
    outcome: Outcome = "success"
    try:
        yield extra
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as exc:
        outcome = "failed"
        extra.setdefault("error", f"{type(exc).__name__}: {exc}"[:_ERROR_CHARS])
        raise
    finally:
        await arecord(
            action, actor, actor_type=actor_type, case_id=case_id, document_id=document_id,
            outcome=outcome, timer=timer, **{**detail, **extra},
        )


def list_events(
    *, case_id: str | None = None, document_id: str | None = None, limit: int = 200
) -> list[AuditEvent]:
    """Newest first. Sync: run on a worker thread."""
    query = service_supabase_client().table(_TABLE).select("*").order("occurred_at", desc=True).limit(limit)
    if case_id is not None:
        query = query.eq("case_id", case_id)
    if document_id is not None:
        query = query.eq("document_id", document_id)
    return [AuditEvent.model_validate(row) for row in query.execute().data or []]
