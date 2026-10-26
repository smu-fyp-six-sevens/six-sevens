"""
Prometheus metrics, served on GET /metrics by every service (CLOUD_ARCHITECTURE_PLAN.md section 8).
Labels are routes, event types, stages, agents and models, never a document or case id. Cloud Run's Managed
Prometheus sidecar scrapes :8080/metrics and adds the service name as `namespace`; the Grafana dashboards
(deploy/grafana) read these.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from . import pricing

# Seconds: a request, up to a full detection or analysis run.
_STAGE_BUCKETS = (0.5, 1, 2, 5, 10, 20, 30, 60, 90, 120, 180, 300, 450, 600, 900)
_LLM_BUCKETS = (0.5, 1, 2, 4, 8, 15, 30, 60, 120)
_COST_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5)
_CALL_BUCKETS = (1, 2, 3, 5, 8, 13, 20, 30, 50, 80)

app_info = Gauge("app_info", "The roles and revision this process runs", ["roles", "revision"])

http_requests = Counter("http_requests_total", "HTTP requests handled", ["method", "route", "status"])
http_latency = Histogram("http_request_duration_seconds", "HTTP request duration", ["method", "route"])

events_published = Counter("events_published_total", "Events published", ["type", "result"])
events_handled = Counter("events_handled_total", "Events handled", ["role", "type", "result"])
handler_duration = Histogram(
    "event_handler_duration_seconds", "Time a handler took on one event", ["role", "type"], buckets=_STAGE_BUCKETS
)
# The same timings the events carry (document.analyzed, document.detected, case.analysis.completed, ...).
stage_duration = Histogram(
    "stage_duration_seconds", "Duration of a pipeline stage, from its event", ["stage", "outcome"], buckets=_STAGE_BUCKETS
)

# Claude, per agent (the span the call ran in, telemetry.current_agent) and model.
llm_calls = Counter("llm_calls_total", "Claude calls", ["agent", "model", "result"])
llm_tokens = Counter("llm_tokens_total", "Claude tokens; input excludes cache reads and writes", ["agent", "model", "kind"])
llm_cost = Counter("llm_cost_usd_total", "Claude cost at list price, USD", ["agent", "model"])
llm_cache_savings = Counter("llm_cache_savings_usd_total", "What prompt-cache reads saved, USD", ["agent", "model"])
llm_latency = Histogram("llm_call_duration_seconds", "One Claude call", ["agent", "model"], buckets=_LLM_BUCKETS)
llm_budget_exhausted = Counter("llm_budget_exhausted_total", "Runs that hit their model-call ceiling")

# Per unit of work (a document's classify and extract, a detection run, a case analysis).
work_cost = Histogram("work_llm_cost_usd", "Claude cost of one unit of work, USD", ["work"], buckets=_COST_BUCKETS)
work_llm_calls = Histogram("work_llm_calls", "Claude calls in one unit of work", ["work"], buckets=_CALL_BUCKETS)
work_cache_read = Counter("work_llm_cache_read_tokens_total", "Cache-read tokens per kind of work", ["work"])
work_input = Counter("work_llm_input_tokens_total", "All input tokens (cached or not) per kind of work", ["work"])

detection_tool_calls = Counter("detection_tool_calls_total", "Detection tool results", ["tool", "outcome"])
detection_verdicts = Counter("detection_verdicts_total", "Detection runs by verdict", ["verdict"])
content_guard_results = Counter("content_guard_results_total", "Content guard results by risk", ["risk"])
content_guard_blocks = Counter("content_guard_blocks_total", "Documents stopped at ingest by the content guard")
llm_prompt_leaks = Counter("llm_prompt_leaks_total", "Model replies that repeat their own instructions", ["agent"])

supabase_calls = Counter("supabase_calls_total", "Supabase calls", ["target"])

# Stuck work (sweeper.py): set on every sweep; owners count how each recovery ended.
stuck_work = Gauge("stuck_work", "Work past its stage's limit at the last sweep", ["stage"])
recoveries = Counter("recoveries_total", "Recovery outcomes", ["stage", "result"])  # requested, recovered, failed, exhausted
cancels_seen_remotely = Counter("cancels_seen_remotely_total", "Cancels a run picked up by polling", ["work"])


def set_info(roles: Iterable[str]) -> None:
    # K_REVISION is set by Cloud Run; it marks each deploy on the dashboards.
    app_info.labels(",".join(sorted(roles)), os.environ.get("K_REVISION", "local")).set(1)


def record_llm(
    agent: str, model: str, uncached_input: int, output: int, cache_read: int, cache_write: int, seconds: float | None,
) -> float:
    """Counts one finished call; returns its cost in USD."""
    llm_calls.labels(agent, model, "ok").inc()
    for kind, n in (("input", uncached_input), ("output", output), ("cache_read", cache_read), ("cache_write", cache_write)):
        if n:
            llm_tokens.labels(agent, model, kind).inc(n)
    cost = pricing.cost(model, uncached_input, output, cache_read, cache_write)
    llm_cost.labels(agent, model).inc(cost.usd)
    llm_cache_savings.labels(agent, model).inc(cost.saved_by_cache_usd)
    if seconds is not None:
        llm_latency.labels(agent, model).observe(seconds)
    return cost.usd


def record_work(work: str, cost_usd: float, calls: int, input_tokens: int, cache_read: int) -> None:
    work_cost.labels(work).observe(cost_usd)
    work_llm_calls.labels(work).observe(calls)
    work_input.labels(work).inc(input_tokens)
    work_cache_read.labels(work).inc(cache_read)


def exposition() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
