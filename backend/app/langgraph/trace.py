"""
Dev-only, in-memory trace of what the langgraph pipeline actually did with a
document — which route it took (fast vs agent), what rule/llm/vision each
said, which model calls fired. Not persisted, not part of graph state
(nothing here is checkpointed or written to Postgres): purely so a human
testing the langgraph-test branch can see routing decisions without reading
logs, via GET /documents/{id}/langgraph-trace.

Deliberately a plain process-local dict — this is a debugging aid for the
single-instance experiment stage, not a feature.
"""

from __future__ import annotations

import time
from typing import Any

_traces: dict[str, dict[str, Any]] = {}
_MAX_TRACES = 500  # bounded so a long-running dev process doesn't leak memory


def start(document_id: str) -> None:
    if len(_traces) >= _MAX_TRACES:
        _traces.pop(next(iter(_traces)))
    _traces[document_id] = {"document_id": document_id, "started_at": time.time(), "steps": []}


def _trace(document_id: str) -> dict[str, Any]:
    return _traces.setdefault(document_id, {"document_id": document_id, "started_at": time.time(), "steps": []})


def record(document_id: str, step: str, **fields: Any) -> None:
    trace = _trace(document_id)
    trace["steps"].append({"step": step, "at": round(time.time() - trace["started_at"], 3), **fields})
    trace.update(fields)  # also flatten onto the top level for easy reading


def get(document_id: str) -> dict[str, Any] | None:
    return _traces.get(document_id)
