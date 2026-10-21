"""
Prometheus metrics, served on GET /metrics by every service (CLOUD_ARCHITECTURE_PLAN.md section 8).
Labels are routes, event types and stages, never a document or case id. Cloud Run's Managed Prometheus
sidecar scrapes :8080/metrics and adds the service name itself.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# Seconds: a request, up to a full detection or analysis run.
_STAGE_BUCKETS = (0.5, 1, 2, 5, 10, 20, 30, 60, 90, 120, 180, 300, 450, 600, 900)

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

llm_calls = Counter("llm_calls_total", "Claude calls")
llm_tokens = Counter("llm_tokens_total", "Claude tokens", ["kind"])
supabase_calls = Counter("supabase_calls_total", "Supabase calls", ["target"])


def record_llm(input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> None:
    llm_calls.inc()
    for kind, n in (("input", input_tokens), ("output", output_tokens), ("cache_read", cache_read), ("cache_write", cache_write)):
        if n:
            llm_tokens.labels(kind).inc(n)


def exposition() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST
