"""
OpenTelemetry traces. Off unless OTEL_ENABLED (Terraform turns it on in the cloud); spans are then no-ops.

Where spans go: OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set (Jaeger in the local observability profile),
else Google Cloud Trace when GOOGLE_CLOUD_PROJECT names a real project, else the console.

A trace follows the work across services: events.publish puts the context on the Pub/Sub message and the push
adapter continues it, so one upload is one trace through api, doc-worker and detection-worker.

get_tracer() is what the rest of the codebase uses, never `opentelemetry.trace` directly. Its spans also name
the agent a Claude call is made in (current_agent), which labels the LLM metrics.

Inside steps_into(sink), each span is also written to the run's own trace (a document's langgraph trace or a
case run's trace) as a "span" step: its parent, duration, the Claude calls made in it (tokens, cost, reasoning),
anything annotate() added and any reply that failed validation. The app's trace view draws the tree from these.
Whether or not OTEL_ENABLED is set: the steps never leave the app's own database.
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from . import pricing
from .config import settings

logger = logging.getLogger("classifier_pipeline.telemetry")

_TRACER_NAME = "six-sevens-backend"
_initialised = False
_provider: TracerProvider | None = None
_agent: contextvars.ContextVar[str] = contextvars.ContextVar("telemetry_agent", default="unattributed")
# Where span steps go during a run (see steps_into), and the step of the span now open.
_sink: contextvars.ContextVar[Callable[..., None] | None] = contextvars.ContextVar("telemetry_sink", default=None)
_step: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("telemetry_step", default=None)
_STEP_CHARS = 1500


def _exporter() -> Any:
    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        logger.info("OpenTelemetry: exporting to %s (OTLP)", settings.OTEL_EXPORTER_OTLP_ENDPOINT)
        return OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/") + "/v1/traces")
    if settings.GOOGLE_CLOUD_PROJECT and settings.GOOGLE_CLOUD_PROJECT != "local":
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            logger.info("OpenTelemetry: exporting to Cloud Trace (project=%s)", settings.GOOGLE_CLOUD_PROJECT)
            return CloudTraceSpanExporter(project_id=settings.GOOGLE_CLOUD_PROJECT)
        except Exception:
            # Missing credentials must not crash the app; the console shows the misconfiguration.
            logger.exception("Cloud Trace exporter failed to start; printing spans to the console")
    else:
        logger.info("OpenTelemetry: no exporter configured; printing spans to the console")
    return ConsoleSpanExporter()


def setup(app=None) -> None:
    """Call once at startup. Installs the tracer provider and traces every request except probes and scrapes."""
    global _initialised, _provider
    if _initialised:
        return
    _initialised = True
    if not settings.OTEL_ENABLED:
        # Spans still reach the runs' own traces (steps_into); only the export is off.
        logger.debug("OpenTelemetry export off (OTEL_ENABLED=false)")
        return

    resource = Resource.create({
        "service.name": settings.OTEL_SERVICE_NAME,
        "service.namespace": "six-sevens",
        "service.version": settings.SERVICE_REVISION,
    })
    _provider = TracerProvider(resource=resource, sampler=ParentBased(TraceIdRatioBased(settings.OTEL_TRACES_SAMPLE_RATIO)))
    _provider.add_span_processor(BatchSpanProcessor(_exporter()))
    trace.set_tracer_provider(_provider)

    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")


def shutdown() -> None:
    """Flushes spans still buffered (Cloud Run stops the instance soon after SIGTERM)."""
    if _provider is not None:
        _provider.shutdown()


class _Tracer:
    """OTel's tracer, plus the agent name the span stands for and, inside steps_into, the span's trace step."""

    @contextmanager
    def start_as_current_span(self, name: str, **kwargs: Any) -> Iterator[trace.Span]:
        token = _agent.set(name)
        sink = _sink.get()
        parent = _step.get()
        step = {"name": name, "id": uuid.uuid4().hex[:12], "parent": parent["id"] if parent else None} if sink else None
        step_token = _step.set(step) if step is not None else None
        started = time.perf_counter()
        try:
            with trace.get_tracer(_TRACER_NAME).start_as_current_span(name, **kwargs) as span:
                yield span
        except BaseException as exc:
            if step is not None:
                step["error"] = f"{type(exc).__name__}: {exc}"[:300]
            raise
        finally:
            _agent.reset(token)
            if step is not None and step_token is not None:
                _step.reset(step_token)
                step["duration_s"] = round(time.perf_counter() - started, 3)
                try:
                    if sink is not None:
                        sink("span", **step)
                except Exception:
                    logger.debug("Failed to record span %s on the run's trace", name, exc_info=True)


_tracer = _Tracer()


def get_tracer() -> _Tracer:
    return _tracer


def current_agent() -> str:
    return _agent.get()


@contextmanager
def steps_into(sink: Callable[..., None]) -> Iterator[None]:
    """Spans opened inside also go to `sink(step, **fields)` as "span" steps: a run's own trace."""
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def current_step_id() -> str | None:
    """The id of the open span's step, which a trace event recorded now belongs under."""
    step = _step.get()
    return step["id"] if step else None


def short(value: Any) -> str:
    """A value as compact JSON, cut to fit a trace step."""
    import json

    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= _STEP_CHARS else text[:_STEP_CHARS] + "…"


def annotate(**fields: Any) -> None:
    """Adds fields to the open span's trace step (never sent to Cloud Trace)."""
    step = _step.get()
    if step is not None:
        step.update(fields)


def _append(key: str, entry: dict[str, Any]) -> None:
    step = _step.get()
    if step is not None:
        step.setdefault(key, []).append(entry)


def add_llm_call(entry: dict[str, Any]) -> None:
    """One Claude call made inside the open span: model, tokens, cost, seconds, reasoning summaries."""
    _append("llm_calls", entry)


def add_validation_failure(entry: dict[str, Any]) -> None:
    """A reply that failed validation inside the open span, with the raw payload."""
    _append("validation_failures", entry)


def inject() -> dict[str, str]:
    """The current trace context as message attributes (W3C traceparent)."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


@contextmanager
def continued_from(carrier: Mapping[str, str]) -> Iterator[None]:
    """Makes the trace context in `carrier` (an event's message attributes) current, if it has one."""
    if "traceparent" not in carrier:
        yield
        return
    token = otel_context.attach(propagate.extract(dict(carrier)))
    try:
        yield
    finally:
        otel_context.detach(token)


def record_usage(span, response) -> None:
    """Token usage, model and cost on `span` from a langchain AIMessage. Never raises."""
    try:
        used = pricing.usage(response)
        if used is None:
            return
        span.set_attribute("llm.model", used.model)
        span.set_attribute("llm.input_tokens", used.uncached_input)
        span.set_attribute("llm.output_tokens", used.output)
        span.set_attribute("llm.cache_read_tokens", used.cache_read)
        span.set_attribute("llm.cache_write_tokens", used.cache_write + used.cache_write_1h)
        cost = pricing.cost(
            used.model, used.uncached_input, used.output, used.cache_read, used.cache_write, used.cache_write_1h,
        )
        span.set_attribute("llm.cost_usd", round(cost.usd, 6))
    except Exception:
        logger.debug("Failed to record token usage on span", exc_info=True)
