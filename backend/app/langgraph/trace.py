"""
Dev-only, in-memory trace of what the langgraph pipeline actually did with a
document — which route it took (fast vs agent), what rule/llm/vision each
said, which model calls fired. Not persisted, not part of graph state
(nothing here is checkpointed or written to Postgres): purely so a human
testing can see routing decisions without reading
logs, via GET /documents/{id}/langgraph-trace.

The process-local dict is the write buffer; every start()/record() also
saves the full trace snapshot (one row per document) to the Supabase
`langgraph_trace` table on a single background thread, so traces survive
restarts and can be shared. Best-effort: a failed write is logged and never
affects the pipeline.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, cast

from ..supabase_client import supabase_client
from .detection_report import read_report

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
    except Exception:
        persisted = None
    if persisted:
        with _lock:
            _traces.setdefault(document_id, persisted)


def _trace(document_id: str) -> dict[str, Any]:
    return _traces.setdefault(document_id, {"document_id": document_id, "started_at": time.time(), "steps": []})


def record(document_id: str, step: str, **fields: Any) -> None:
    with _lock:
        trace = _trace(document_id)
        trace["steps"].append({"step": step, "at": round(time.time() - trace["started_at"], 3), **fields})
        trace.update(fields)  # also flatten onto the top level for easy reading
    _persist(document_id)


def flush(document_id: str) -> None:
    """Blocks until this document's queued trace write has landed. Call via asyncio.to_thread."""
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


def _case_trace_rows(case_id: str) -> list[dict[str, Any]]:
    rows = (
        supabase_client.table("langgraph_trace")
        .select("document_id, trace")
        .eq("trace->>case_id", case_id)
        .execute()
        .data
    )
    # Every row here is a JSON object (the query selects document_id,
    # trace from a real table) — cast() just tells the type checker
    # what's already true, since postgrest types .data loosely.
    return [cast(dict, raw_row) for raw_row in rows]


def get_case_detection_reports(case_id: str) -> list[dict[str, Any]]:
    """Detection reports stored on the traces of every document in a case."""
    reports = []
    for row in _case_trace_rows(case_id):
        row_trace = cast(dict, row.get("trace")) or {}
        if not read_report(row_trace.get("detection_report")):
            continue
        reports.append({
            "document_id": row["document_id"],
            "filename": row_trace.get("filename"),
            "report": read_report(row_trace["detection_report"]),
        })
    return reports


_DETECTION_STATE_CHUNK = 100  # keeps the in.(...) filter well under URL length limits


def forget(document_ids: list[str]) -> None:
    """Drops the in-memory traces of documents being deleted, so a pending write can't bring one back."""
    with _lock:
        for document_id in document_ids:
            _traces.pop(document_id, None)


def get_detection_states(
    document_ids: list[str], *, slim: bool = False
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """
    (reports by document_id, ids whose last run failed and left no report)
    for many documents in one query per 100 — what case_risk needs for a
    whole case list without one trace query per case. Selects only the two
    keys it needs, not each trace's full step history. Keyed by document
    rather than trace->>case_id so a document moved to another case still
    counts towards the case it's in now.

    slim=True fetches only each report's verdict, enough to know a report exists: its value is then just
    {"verdict": ...}, not a full report. For callers that only need which documents were scanned.
    """
    report_path = "trace->detection_report->verdict" if slim else "trace->detection_report"
    reports: dict[str, dict[str, Any]] = {}
    failed: set[str] = set()
    for start_index in range(0, len(document_ids), _DETECTION_STATE_CHUNK):
        rows = (
            supabase_client.table("langgraph_trace")
            .select(f"document_id, report:{report_path}, error:trace->detection_error")
            .in_("document_id", document_ids[start_index : start_index + _DETECTION_STATE_CHUNK])
            .execute()
            .data
        )
        for raw_row in rows:
            row = cast(dict, raw_row)
            raw = row.get("report")
            report = {"verdict": raw} if slim and isinstance(raw, str) else read_report(raw)
            if report:
                reports[row["document_id"]] = report
            elif row.get("error"):
                failed.add(row["document_id"])
    return reports, failed


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
    table = supabase_client.table("langgraph_trace")
    try:
        # No unique key on document_id, so update-then-insert instead of upsert.
        updated = table.update({"trace": snapshot}).eq("document_id", document_id).execute()
        if not updated.data:
            table.insert({"document_id": document_id, "trace": snapshot}).execute()
    except Exception as exc:
        logger.warning("Failed to persist langgraph trace for %s: %s", document_id, exc)
