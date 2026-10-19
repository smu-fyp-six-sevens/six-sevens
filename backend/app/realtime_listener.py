from __future__ import annotations

import asyncio
import logging

from realtime._async.channel import AsyncRealtimeChannel
from realtime.types import PostgresChangesPayload, RealtimePostgresChangesListenEvent
from supabase import AsyncClient

from . import case_service, query_stats
from .case_broadcaster import broadcaster
from .supabase_client import build_async_supabase_client

logger = logging.getLogger("classifier_pipeline.realtime_listener")

# Coalescing window: a bulk upload (or several documents finishing
# classification/extraction close together) inserts/updates several rows
# for the same case within milliseconds — Realtime fires one event per row.
# Without this, each event would independently trigger a full
# build_case_snapshot query + a WebSocket push, so a 10-document burst would
# mean 10 near-identical snapshots built and sent back to back. Resetting a
# short per-case_id delay on every event and only actually building+
# publishing once it elapses collapses that into one query, one push — at
# the cost of up to this much added latency on any single status update.
DEBOUNCE_SECONDS = 0.5

_client: AsyncClient | None = None
_channel: AsyncRealtimeChannel | None = None
_pending: dict[str, asyncio.Task] = {}


async def _debounced_publish(case_id: str) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        # Superseded by a newer event for the same case_id before the delay
        # elapsed — that newer call's own _debounced_publish will run
        # instead. Not an error, just the coalescing working as intended.
        return

    try:
        with query_stats.scope("realtime snapshot"):
            snapshot = await asyncio.to_thread(case_service.build_case_snapshot, case_id)
    except Exception:
        logger.exception("Failed to build case snapshot for case %s", case_id)
        return
    finally:
        _pending.pop(case_id, None)

    if snapshot is not None:
        broadcaster.publish(case_id, snapshot)


def _on_change(payload: PostgresChangesPayload) -> None:
    """
    Registered as the postgres_changes callback — called synchronously by
    realtime-py from within its own message-handling loop (not awaited), so
    this must return immediately. realtime-py runs on the same asyncio event
    loop as the rest of FastAPI (confirmed: it's asyncio-native, not
    thread-based, and is started from main.py's lifespan on this loop), so
    scheduling a task here from synchronous code is safe.
    """
    data = payload.get("data") or {}
    record = data.get("record") or data.get("old_record")
    query_stats.count_event("realtime.event")
    if not record:
        return
    # A cases row (e.g. its stage moving) is the case itself; a documents row names its case.
    case_id = record.get("id") if data.get("table") == "cases" else record.get("case_id")
    if not case_id:
        return
    if not broadcaster.has_subscribers(case_id):
        query_stats.count_event("realtime.no_viewer")
        return  # nobody has this case open

    existing = _pending.get(case_id)
    if existing is not None and not existing.done():
        existing.cancel()
        query_stats.count_event("realtime.coalesced")
    _pending[case_id] = asyncio.create_task(_debounced_publish(case_id))


async def start() -> None:
    """
    Called once from main.py's lifespan at startup. One subscription per
    process — not per case, not per connection — is what keeps this cheap
    against Supabase's free-tier Realtime connection cap, and what makes it
    scale to N replicas unchanged later: each replica
    independently runs its own copy of this, and Realtime's fan-out means
    every replica sees every change regardless of how many others exist.
    """
    global _client, _channel

    _client = await build_async_supabase_client()
    _channel = _client.channel("case-status-changes")
    _channel.on_postgres_changes(
        RealtimePostgresChangesListenEvent.All, callback=_on_change, table="documents", schema="public"
    )
    _channel.on_postgres_changes(
        RealtimePostgresChangesListenEvent.All, callback=_on_change, table="cases", schema="public"
    )
    await _channel.subscribe()
    logger.info("Realtime listener subscribed to documents/cases changes")


async def stop() -> None:
    global _client, _channel

    for task in _pending.values():
        task.cancel()
    _pending.clear()

    if _client is not None:
        await _client.realtime.close()
    _client = None
    _channel = None
