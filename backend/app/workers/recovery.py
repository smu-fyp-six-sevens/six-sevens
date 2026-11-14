"""
What the doc-worker and detection-worker share: how a run is tracked, slotted and metered (RunTracker.slot, metered),
and how they handle a sweeper's recovery.requested (sweeper.py).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager

from typing import Any

from .. import agent_versions, events, metrics, query_stats
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


class Meter:
    """
    What a run spent on Claude (query_stats' llm detail) and which agent versions it used, filled in when its metered
    block ends, however it ends.
    """

    def __init__(self) -> None:
        self.usage: dict[str, Any] = {}


@contextmanager
def metered(label: str, kind: str, meter: Meter) -> Iterator[None]:
    """
    The run's perf scope (`label` in the perf log, `kind` its work label in the cost metrics), with its usage on
    `meter` even when the run raises or is cancelled: what it spent still goes on the record.
    """
    with query_stats.scope(label, work=kind) as unit:
        try:
            yield
        finally:
            meter.usage = {**unit.llm_detail(), "agent_versions": agent_versions.for_run(kind)}


class RunTracker:
    """A worker's runs in this process (document_id -> task), so an officer's cancel can interrupt one."""

    def __init__(self, kind: str) -> None:
        self._kind = kind  # label on the remote-cancel metric
        self._active: dict[str, asyncio.Task] = {}
        self._officer_cancels: set[str] = set()

    @contextmanager
    def track(self, document_id: str, cancelled: Callable[[], Awaitable[bool]]) -> Iterator[None]:
        """Registers the current task and watches `cancelled` for a cancel made through another instance."""
        task = asyncio.current_task()
        watcher: asyncio.Task | None = None
        if task is not None:
            self._active[document_id] = task
            watcher = asyncio.create_task(cancel_when(task, cancelled, lambda: self._note_remote_cancel(document_id)))
        try:
            yield
        finally:
            self._active.pop(document_id, None)
            if watcher is not None:
                watcher.cancel()

    @asynccontextmanager
    async def slot(
        self, document_id: str, cancelled: Callable[[], Awaitable[bool]], slots: asyncio.Semaphore,
    ) -> AsyncIterator[None]:
        """track(), then a place among the worker's concurrent runs: the start of every run either worker makes."""
        with self.track(document_id, cancelled):
            async with slots:
                yield

    def _note_remote_cancel(self, document_id: str) -> None:
        self._officer_cancels.add(document_id)
        metrics.cancels_seen_remotely.labels(self._kind).inc()

    def cancel(self, document_id: str | None) -> None:
        """An officer's cancel: interrupts the run if it is in this process."""
        task = self._active.get(document_id or "")
        if task is not None and not task.done() and document_id is not None:
            self._officer_cancels.add(document_id)
            task.cancel()

    def cancelled_by_officer(self, document_id: str) -> bool:
        """Whether a CancelledError came from an officer's cancel (rather than shutdown); clears the mark."""
        if document_id not in self._officer_cancels:
            return False
        self._officer_cancels.discard(document_id)
        return True

    def is_running(self, document_id: str) -> bool:
        return document_id in self._active
