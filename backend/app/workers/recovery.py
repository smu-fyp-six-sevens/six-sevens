"""What the owning workers share when handling a sweeper's recovery.requested (sweeper.py)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .. import events, metrics
from ..config import settings
from ..events import Event, types
from ..events.envelope import Outcome

logger = logging.getLogger("classifier_pipeline.recovery")


def is_for(event: Event, role: str) -> bool:
    """In-process (and on an emulator without filters) every worker sees every recovery request."""
    return event.detail.get(types.OWNER_ATTRIBUTE) == role


def attempt(event: Event) -> int:
    return int(event.detail.get("attempt") or 1)


def exhausted(event: Event) -> bool:
    return attempt(event) > settings.RECOVERY_MAX_ATTEMPTS


async def report(event: Event, role: str, type: str, outcome: Outcome, **detail: object) -> None:
    stage = str(event.detail.get("stage"))
    result = "exhausted" if type == types.RECOVERY_EXHAUSTED else "recovered" if outcome == "success" else "failed"
    metrics.recoveries.labels(stage, result).inc()
    await events.record(Event(
        type=type, actor_type="agent", actor=role, case_id=event.case_id, document_id=event.document_id,
        outcome=outcome, detail={"stage": stage, "attempt": attempt(event), "run_id": event.detail.get("run_id"), **detail},
    ))


async def cancel_when(task: asyncio.Task, cancelled: Callable[[], Awaitable[bool]], on_cancel: Callable[[], None]) -> None:
    """
    Polls `cancelled` while `task` runs and cancels it when true: a cancel made through another instance, which the
    in-process fast path (the cancel event reaching this process) can't see. Only with Pub/Sub, where that happens.
    """
    if settings.EVENT_TRANSPORT != "pubsub":
        return
    while not task.done():
        await asyncio.sleep(settings.CANCEL_POLL_SECONDS)
        try:
            if not task.done() and await cancelled():
                on_cancel()
                task.cancel()
                return
        except Exception:
            logger.debug("Cancel check failed; trying again", exc_info=True)
