"""
Dev-only, in-memory trace of what the langgraph pipeline actually did with a
document — which route it took (fast vs agent), what rule/llm/vision each
said, which model calls fired. Not persisted, not part of graph state
(nothing here is checkpointed or written to Postgres): purely so a human
testing the langgraph-test branch can see routing decisions without reading
logs, via GET /documents/{id}/langgraph-trace.

The process-local dict is the write buffer; every start()/record() also
saves the full trace snapshot (one row per document) to the Supabase
`langgraph_trace` table on a single background thread, so traces survive
restarts and can be shared. Best-effort: a failed write is logged and never
affects the pipeline.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..supabase_client import supabase_client

logger = logging.getLogger("classifier_pipeline.langgraph.trace")

# One worker so saves for a document land in order (each carries the full
# snapshot, so the last write wins).
_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="langgraph-trace")

_traces: dict[str, dict[str, Any]] = {}
_MAX_TRACES = 500  # bounded so a long-running dev process doesn't leak memory


def start(document_id: str) -> None:
    if len(_traces) >= _MAX_TRACES:
        _traces.pop(next(iter(_traces)))
    _traces[document_id] = {"document_id": document_id, "started_at": time.time(), "steps": []}
    _persist(document_id)


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
    except Exception:  # noqa: BLE001 — falls back to a fresh trace
        persisted = None
    if persisted:
        _traces[document_id] = persisted


def _trace(document_id: str) -> dict[str, Any]:
    return _traces.setdefault(document_id, {"document_id": document_id, "started_at": time.time(), "steps": []})


def record(document_id: str, step: str, **fields: Any) -> None:
    trace = _trace(document_id)
    trace["steps"].append({"step": step, "at": round(time.time() - trace["started_at"], 3), **fields})
    trace.update(fields)  # also flatten onto the top level for easy reading
    _persist(document_id)


def get(document_id: str) -> dict[str, Any] | None:
    return _traces.get(document_id)


def get_case_detection_reports(case_id: str) -> list[dict[str, Any]]:
    """Detection reports stored on the traces of every document in a case."""
    rows = (
        supabase_client.table("langgraph_trace")
        .select("document_id, trace")
        .eq("trace->>case_id", case_id)
        .execute()
        .data
    )
    return [
        {
            "document_id": row["document_id"],
            "filename": row["trace"].get("filename"),
            "report": row["trace"]["detection_report"],
        }
        for row in rows
        if (row.get("trace") or {}).get("detection_report")
    ]


def get_persisted(document_id: str) -> dict[str, Any] | None:
    result = supabase_client.table("langgraph_trace").select("trace").eq("document_id", document_id).execute()
    return result.data[0]["trace"] if result.data else None


def _persist(document_id: str) -> None:
    trace = _traces.get(document_id)
    if trace is None:
        return
    _writer.submit(_write_row, {**trace, "steps": list(trace["steps"])})


def _write_row(snapshot: dict[str, Any]) -> None:
    document_id = snapshot["document_id"]
    table = supabase_client.table("langgraph_trace")
    try:
        # No unique key on document_id, so update-then-insert instead of upsert.
        updated = table.update({"trace": snapshot}).eq("document_id", document_id).execute()
        if not updated.data:
            table.insert({"document_id": document_id, "trace": snapshot}).execute()
    except Exception as exc:  # noqa: BLE001 — debug aid must never break the pipeline
        logger.warning("Failed to persist langgraph trace for %s: %s", document_id, exc)
