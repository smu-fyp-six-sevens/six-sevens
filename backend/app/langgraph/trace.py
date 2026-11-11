"""
Debug trace of what the langgraph pipeline actually did with a document —
which route it took (fast vs agent), what rule/llm/vision each said, which
model calls fired — so a human testing can see routing decisions without
reading logs, via GET /documents/{id}/langgraph-trace. The review screen also
shows each low-confidence field's reason from it. Not part of graph state, and
nothing reads it to decide anything: detection's results live in
detection_results.py.

record() adds steps to the process-local copy; flush(), called once at the
end of each run, saves the whole trace (one row per document) to the
Supabase `langgraph_trace` table on a single background thread, so traces
survive restarts and can be shared. Best-effort: a failed write is logged
and never affects the pipeline.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from .. import telemetry
from ..supabase_client import supabase_client

logger = logging.getLogger("classifier_pipeline.langgraph.trace")

# One worker so saves for a document land in order (each carries the full
# snapshot, so the last write wins).
_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="langgraph-trace")

_traces: dict[str, dict[str, Any]] = {}
_MAX_TRACES = 500  # bounded so a long-running dev process doesn't leak memory


# Guards _traces against the writer thread copying a trace mid-update.
_lock = threading.Lock()
# Documents with a write already queued: later records ride along with it instead of queueing their own.
_pending: set[str] = set()


def start(document_id: str) -> None:
    with _lock:
        if len(_traces) >= _MAX_TRACES:
            _traces.pop(next(iter(_traces)))
        _traces[document_id] = {"document_id": document_id, "started_at": time.time(), "steps": []}


def resume(document_id: str) -> None:
    """
    Loads this document's persisted trace into memory (if it isn't already),
    so a later record() appends to it instead of overwriting the saved row
    with a fresh one — for work that happens after the original process run
    (e.g. detection). Blocking; call via asyncio.to_thread.
    """
    if document_id in _traces:
        return
    try:
        persisted = get_persisted(document_id)
    except Exception:
        persisted = None
    if persisted:
        with _lock:
            _traces.setdefault(document_id, persisted)


def _trace(document_id: str) -> dict[str, Any]:
    return _traces.setdefault(document_id, {"document_id": document_id, "started_at": time.time(), "steps": []})


def record(document_id: str, step: str, **fields: Any) -> None:
    """A step, at seconds since the trace started. A span step (telemetry.steps_into) is recorded when it ends."""
    if step != "span" and (in_span := telemetry.current_step_id()) is not None:
        fields = {"in_span": in_span, **fields}
    with _lock:
        trace = _trace(document_id)
        trace["steps"].append({"step": step, "at": round(time.time() - trace["started_at"], 3), **fields})


def steps_into(document_id: str):
    """Spans opened inside are recorded on this document's trace (telemetry.steps_into)."""
    return telemetry.steps_into(lambda step, **fields: record(document_id, step, **fields))


def flush(document_id: str) -> None:
    """Saves the trace and blocks until the write has landed: the end of a run. Call via asyncio.to_thread."""
    _persist(document_id)
    with _lock:
        queued = document_id in _pending
    if queued:
        _writer.submit(lambda: None).result()  # one worker: this runs only after the queued write


def get(document_id: str) -> dict[str, Any] | None:
    return _traces.get(document_id)


def load(document_id: str) -> dict[str, Any] | None:
    """The persisted trace, else this process's copy (the table may be unreachable). Blocking; use to_thread."""
    try:
        persisted = get_persisted(document_id)
    except Exception:
        persisted = None
    return persisted or get(document_id)


def forget(document_ids: list[str]) -> None:
    """Drops the in-memory traces of documents being deleted, so a pending write can't bring one back."""
    with _lock:
        for document_id in document_ids:
            _traces.pop(document_id, None)


def get_persisted(document_id: str) -> dict[str, Any] | None:
    result = supabase_client.table("langgraph_trace").select("trace").eq("document_id", document_id).execute()
    if not result.data:
        return None
    return cast(dict, cast(dict, result.data[0])["trace"])


def _persist(document_id: str) -> None:
    with _lock:
        if document_id in _pending or document_id not in _traces:
            return
        _pending.add(document_id)
    _writer.submit(_flush, document_id)


def _flush(document_id: str) -> None:
    with _lock:
        _pending.discard(document_id)
        trace = _traces.get(document_id)
        if trace is None:
            return
        snapshot = {**trace, "steps": list(trace["steps"])}
    _write_row(snapshot)


def _write_row(snapshot: dict[str, Any]) -> None:
    document_id = snapshot["document_id"]
    try:
        # One round trip: needs the unique key on document_id (backend/sql/0001_schema.sql).
        supabase_client.table("langgraph_trace").upsert(
            {"document_id": document_id, "trace": snapshot}, on_conflict="document_id",
        ).execute()
    except Exception as exc:
        logger.warning("Failed to persist langgraph trace for %s: %s", document_id, exc)
