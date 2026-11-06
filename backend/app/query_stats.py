"""
Per-unit-of-work cost logging: how many Supabase calls (database and storage)
and Claude calls one API request, WebSocket connect/refresh, Realtime-triggered
snapshot or background job made. One "perf" log line when each unit ends, plus
a periodic line for Realtime/WebSocket traffic and any calls made outside a unit.

A unit is opened with `with scope(name):`. It follows the work into
asyncio.to_thread and child tasks (contextvars are copied into both), so a
background task must open its own scope or it would count into the request
that started it.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import re
import threading
import time
from collections import Counter
from contextlib import contextmanager
from typing import Any, Iterator

import httpx
from langchain_core.callbacks import AsyncCallbackHandler

from . import metrics, pricing, telemetry
from .llm_io import thinking_summaries

logger = logging.getLogger("perf")

REPORT_INTERVAL_SECONDS = 60
_ID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


class _Scope:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: Counter[str] = Counter()  # "GET documents" -> count
        self.db_seconds = 0.0
        self.llm_calls = 0
        self.llm_input_tokens = 0
        self.llm_output_tokens = 0
        self.llm_cache_read = 0
        self.llm_cache_write = 0
        self.llm_cost_usd = 0.0
        self.llm_cache_saved_usd = 0.0
        self._lock = threading.Lock()  # to_thread calls in one scope can run concurrently

    def add_call(self, label: str) -> None:
        with self._lock:
            self.calls[label] += 1

    def add_db_time(self, seconds: float) -> None:
        with self._lock:
            self.db_seconds += seconds

    def add_llm(
        self, input_tokens: int, output_tokens: int, cache_read: int = 0, cache_write: int = 0, cost_usd: float = 0.0,
        *, cache_saved_usd: float = 0.0,
    ) -> None:
        with self._lock:
            self.llm_calls += 1
            self.llm_input_tokens += input_tokens
            self.llm_output_tokens += output_tokens
            self.llm_cache_read += cache_read
            self.llm_cache_write += cache_write
            self.llm_cost_usd += cost_usd
            self.llm_cache_saved_usd += cache_saved_usd

    def summary(self) -> str:
        with self._lock:
            total = sum(self.calls.values())
            parts = [f"{total} supabase"]
            if total:
                detail = ", ".join(f"{label} x{n}" for label, n in self.calls.most_common())
                parts[0] += f" ({detail}; {self.db_seconds * 1000:.0f}ms)"
            if self.llm_calls:
                parts.append(
                    f"{self.llm_calls} claude ({self.llm_input_tokens} in / {self.llm_output_tokens} out tokens; "
                    f"cache read {self.llm_cache_read} / write {self.llm_cache_write}; ${self.llm_cost_usd:.4f})"
                )
            return " | ".join(parts)

    def is_empty(self) -> bool:
        return not self.calls and not self.llm_calls

    def llm_detail(self) -> dict[str, float | int]:
        """This unit's Claude usage for its end-of-run event, so the audit trail carries cost per document and case."""
        with self._lock:
            return {
                "llm_cost_usd": round(self.llm_cost_usd, 6), "llm_calls": self.llm_calls,
                "llm_input_tokens": self.llm_input_tokens, "llm_output_tokens": self.llm_output_tokens,
                "llm_cache_read_tokens": self.llm_cache_read, "llm_cache_write_tokens": self.llm_cache_write,
                # Net of what the cache writes cost above plain input.
                "llm_cache_saved_usd": round(self.llm_cache_saved_usd, 6),
            }


_current: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar("query_scope", default=None)
# Calls made outside any scope, reported periodically: mostly the trace writer threads, which outlive
# the job that queued them, so counting them into its scope would lose them.
_unscoped = _Scope("unscoped")
_events: Counter[str] = Counter()
_events_lock = threading.Lock()


def label_path(path: str) -> str:
    """'/cases/8f1c…/analysis' -> '/cases/{id}/analysis', so the same endpoint always logs under one name."""
    return _ID_RE.sub("{id}", path)


@contextmanager
def scope(name: str, work: str | None = None) -> Iterator[_Scope]:
    """`work` (a fixed kind, e.g. "detection") also records the unit's Claude cost and calls as metrics."""
    current = _Scope(name)
    token = _current.set(current)
    started = time.perf_counter()
    try:
        yield current
    finally:
        _current.reset(token)
        if work is not None:
            metrics.record_work(
                work, current.llm_cost_usd, current.llm_calls,
                current.llm_input_tokens, current.llm_cache_read,
            )
        if not current.is_empty():
            logger.info("%s -> %s, %.0fms total", name, current.summary(), (time.perf_counter() - started) * 1000)


def count_event(name: str, n: int = 1) -> None:
    """A Realtime/WebSocket occurrence with no Supabase call of its own (an event in, a push out)."""
    with _events_lock:
        _events[name] += n


def _active() -> _Scope:
    return _current.get() or _unscoped


# --- Supabase: httpx event hooks on the shared clients (supabase_client.py) ---


def _call_label(request: httpx.Request) -> str:
    path = request.url.path
    if "/rest/v1/" in path:
        target = path.split("/rest/v1/", 1)[1].split("/", 1)[0]
    elif "/storage/v1/" in path:
        target = "storage"
    else:
        target = path
    return f"{request.method} {target}"


def _on_request(request: httpx.Request) -> None:
    label = _call_label(request)
    _active().add_call(label)
    metrics.supabase_calls.labels(label.split(" ", 1)[-1]).inc()
    request.extensions["query_stats_started"] = time.perf_counter()


def _on_response(response: httpx.Response) -> None:
    started = response.request.extensions.get("query_stats_started")
    if started is not None:
        _active().add_db_time(time.perf_counter() - started)


HTTPX_EVENT_HOOKS: dict[str, list[Any]] = {"request": [_on_request], "response": [_on_response]}


# --- Claude: a callback on every model built by langgraph/_anthropic.py ---


class _LLMUsage(AsyncCallbackHandler):
    """Counts each call into the current scope and the metrics, labelled by agent (telemetry.current_agent)."""

    def __init__(self) -> None:
        self._started: dict[Any, tuple[float, str]] = {}  # run_id -> (start time, agent)

    async def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: Any, **kwargs: Any) -> None:
        self._started[run_id] = (time.perf_counter(), telemetry.current_agent())

    async def on_llm_end(self, response, *, run_id: Any = None, **kwargs: Any) -> None:
        started, agent = self._started.pop(run_id, (None, telemetry.current_agent()))
        seconds = time.perf_counter() - started if started is not None else None
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                used = pricing.usage(message)
                if used is None:
                    continue
                cost = metrics.record_llm(
                    agent, used.model, used.uncached_input, used.output, used.cache_read, used.cache_write, seconds,
                    cache_write_1h=used.cache_write_1h,
                )
                _active().add_llm(
                    used.total_input, used.output, used.cache_read, used.cache_write + used.cache_write_1h, cost.usd,
                    cache_saved_usd=cost.net_cache_saving_usd,
                )
                telemetry.add_llm_call({
                    "model": used.model, "input_tokens": used.uncached_input, "output_tokens": used.output,
                    "cache_read_tokens": used.cache_read, "cache_write_tokens": used.cache_write + used.cache_write_1h,
                    "cost_usd": round(cost.usd, 6), "seconds": round(seconds, 3) if seconds is not None else None,
                    "stop_reason": (getattr(message, "response_metadata", None) or {}).get("stop_reason"),
                    "thinking": thinking_summaries(message),
                })

    async def on_llm_error(self, error: BaseException, *, run_id: Any = None, **kwargs: Any) -> None:
        started, agent = self._started.pop(run_id, (None, telemetry.current_agent()))
        metrics.llm_calls.labels(agent, "unknown", "error").inc()
        telemetry.add_llm_call({
            "error": f"{type(error).__name__}: {error}"[:300],
            "seconds": round(time.perf_counter() - started, 3) if started is not None else None,
        })


llm_callback = _LLMUsage()


# --- Periodic report: Realtime/WebSocket traffic and unscoped calls ---


def _report() -> None:
    global _unscoped
    with _events_lock:
        events = dict(_events)
        _events.clear()
    unscoped, _unscoped = _unscoped, _Scope("unscoped")
    if events:
        logger.info(
            "last %ss realtime/ws: %s", REPORT_INTERVAL_SECONDS,
            ", ".join(f"{name}={n}" for name, n in sorted(events.items())),
        )
    if not unscoped.is_empty():
        logger.info("last %ss trace saves and other unscoped calls -> %s", REPORT_INTERVAL_SECONDS, unscoped.summary())


async def run_reporter() -> None:
    while True:
        await asyncio.sleep(REPORT_INTERVAL_SECONDS)
        _report()
