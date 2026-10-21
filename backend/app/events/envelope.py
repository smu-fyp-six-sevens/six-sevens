from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from ..auth import current_actor

ActorType = Literal["user", "agent", "system"]
Outcome = Literal["success", "failed", "cancelled"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Event(BaseModel):
    """One domain event. Carries ids only (Supabase holds the state), and is also the audit_events row."""

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    occurred_at: datetime = Field(default_factory=_now)
    actor_type: ActorType = "system"
    actor: str = "system"
    case_id: str | None = None
    document_id: str | None = None
    outcome: Outcome = "success"
    started_at: datetime | None = None
    duration_ms: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)

    @field_validator("detail", mode="before")
    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        return json.loads(json.dumps(value, default=str)) if isinstance(value, dict) else value

    def to_audit_row(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "occurred_at": self.occurred_at.isoformat(),
            "actor_type": self.actor_type,
            "actor": self.actor,
            "action": self.type,
            "case_id": self.case_id,
            "document_id": self.document_id,
            "outcome": self.outcome,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "duration_ms": self.duration_ms,
            "detail": self.detail,
        }

    @classmethod
    def from_audit_row(cls, row: dict[str, Any]) -> Event:
        return cls(
            event_id=row["id"], type=row["action"], occurred_at=row["occurred_at"],
            actor_type=row["actor_type"], actor=row["actor"], case_id=row.get("case_id"),
            document_id=row.get("document_id"), outcome=row["outcome"], started_at=row.get("started_at"),
            duration_ms=row.get("duration_ms"), detail=row.get("detail") or {},
        )


def officer_event(type: str, **fields: Any) -> Event:
    """An event caused by the signed-in officer of this request, or by the system when there is none."""
    actor = current_actor()
    if actor is None:
        return Event(type=type, **fields)
    return Event(type=type, actor_type="user", actor=actor, **fields)


class Stopwatch:
    """Start one before the work; `fields()` after gives the event's started_at and duration_ms."""

    def __init__(self) -> None:
        self.started_at = _now()
        self._started = time.perf_counter()

    def fields(self) -> dict[str, Any]:
        return {"started_at": self.started_at, "duration_ms": int((time.perf_counter() - self._started) * 1000)}
