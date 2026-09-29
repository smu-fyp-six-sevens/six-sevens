"""
OpenTelemetry wiring — traces only for now (see LANGGRAPH_MIGRATION_PLAN.md
section 6). Off by default (settings.OTEL_ENABLED): setup() then does nothing
and every tracer.start_as_current_span() call elsewhere in the codebase is a
real no-op (OTel's own default global tracer), so instrumenting a code path
ahead of time costs nothing until this is turned on.

When enabled: exports to Google Cloud Trace if settings.GOOGLE_CLOUD_PROJECT
is set (needs a service account/ADC with roles/cloudtrace.agent reachable
from wherever this runs), else prints spans to the console — so OTEL_ENABLED
can be tried locally before any GCP credentials exist.

get_tracer() is what the rest of the codebase (langgraph pipeline, detection
graph) imports to create spans — never `opentelemetry.trace` directly, so
there's exactly one place that decides the tracer name/version.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from .config import settings

logger = logging.getLogger("classifier_pipeline.telemetry")

_TRACER_NAME = "six-sevens-backend"
_initialised = False


def setup(app=None) -> None:
    """
    Call once, at startup (see main.py's lifespan). Installs a global
    TracerProvider and, if `app` is given, instruments FastAPI so every
    request gets a span automatically. Safe to call multiple times.
    """
    global _initialised
    if _initialised:
        return
    _initialised = True

    if not settings.OTEL_ENABLED:
        logger.info("OpenTelemetry disabled (OTEL_ENABLED=false) — spans are no-ops")
        return

    resource = Resource.create({"service.name": settings.OTEL_SERVICE_NAME})
    provider = TracerProvider(resource=resource)

    if settings.GOOGLE_CLOUD_PROJECT:
        try:
            from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter

            exporter = CloudTraceSpanExporter(project_id=settings.GOOGLE_CLOUD_PROJECT)
            logger.info(
                "OpenTelemetry enabled — exporting to Google Cloud Trace (project=%s)",
                settings.GOOGLE_CLOUD_PROJECT,
            )
        except Exception:
            # Missing/invalid credentials shouldn't crash the app — fall back
            # to the console so the misconfiguration is at least visible.
            logger.exception(
                "Failed to set up the Cloud Trace exporter — falling back to console output. "
                "Check GOOGLE_CLOUD_PROJECT and that ADC/a service account with "
                "roles/cloudtrace.agent is reachable."
            )
            exporter = ConsoleSpanExporter()
    else:
        logger.info("OpenTelemetry enabled — GOOGLE_CLOUD_PROJECT not set, printing spans to console")
        exporter = ConsoleSpanExporter()

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)


def get_tracer():
    return trace.get_tracer(_TRACER_NAME)


def record_usage(span, response) -> None:
    """
    Sets token-usage attributes on `span` from a langchain AIMessage's
    usage_metadata, if present. Never raises — telemetry must not affect the
    call it's observing.
    """
    try:
        usage = getattr(response, "usage_metadata", None)
        if not usage:
            return
        span.set_attribute("llm.input_tokens", usage.get("input_tokens", 0))
        span.set_attribute("llm.output_tokens", usage.get("output_tokens", 0))
        span.set_attribute("llm.total_tokens", usage.get("total_tokens", 0))
    except Exception:  # noqa: BLE001 — telemetry is best-effort
        logger.debug("Failed to record token usage on span", exc_info=True)
