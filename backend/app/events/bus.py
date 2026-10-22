from __future__ import annotations

import asyncio
import logging
from typing import Any

from opentelemetry.trace import SpanKind

from .. import metrics, telemetry
from ..config import settings
from . import registry
from .envelope import Event

logger = logging.getLogger(__name__)

_TRANSPORTS = ("inprocess", "pubsub")
# Handler tasks started in-process; kept so they aren't garbage collected and can be cancelled at shutdown.
_tasks: set[asyncio.Task] = set()
_publisher: Any = None


async def publish(event: Event) -> None:
    """Publishes one event; raises if it could not be. An event that triggers work is the action, so callers fail with it."""
    try:
        # In-process handlers inherit this span through the task's context; Pub/Sub ones through traceparent.
        with telemetry.get_tracer().start_as_current_span(f"publish {event.type}", kind=SpanKind.PRODUCER) as span:
            span.set_attribute("event.id", event.event_id)
            if settings.EVENT_TRANSPORT == "pubsub":
                await _publish_pubsub(event)
            else:
                _dispatch_in_process(event)
    except Exception:
        metrics.events_published.labels(event.type, "error").inc()
        raise
    metrics.events_published.labels(event.type, "ok").inc()
    if event.duration_ms is not None:
        metrics.stage_duration.labels(event.type, event.outcome).observe(event.duration_ms / 1000)


async def record(event: Event) -> None:
    """`publish` for an event nothing waits on (audit only): a failure is logged, never raised."""
    try:
        await publish(event)
    except Exception:
        logger.exception("Failed to publish %s event", event.type)


def check_transport() -> None:
    if settings.EVENT_TRANSPORT not in _TRANSPORTS:
        raise ValueError(f"EVENT_TRANSPORT must be one of {_TRANSPORTS}, not {settings.EVENT_TRANSPORT!r}")
    if settings.EVENT_TRANSPORT == "pubsub" and not settings.GOOGLE_CLOUD_PROJECT:
        raise ValueError("EVENT_TRANSPORT=pubsub needs GOOGLE_CLOUD_PROJECT")


def _dispatch_in_process(event: Event) -> None:
    for role, handler in registry.handlers_for(event.type):
        task = asyncio.create_task(_run(role, handler, event))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _run(role: str, handler: registry.Handler, event: Event) -> None:
    try:
        await registry.run_handler(role, handler, event)
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Handler %s failed on %s %s", getattr(handler, "__qualname__", handler), event.type, event.event_id)


def _client() -> Any:
    global _publisher
    if _publisher is None:
        from google.cloud import pubsub_v1

        _publisher = pubsub_v1.PublisherClient()
    return _publisher


async def _publish_pubsub(event: Event) -> None:
    client = _client()
    topic = client.topic_path(settings.GOOGLE_CLOUD_PROJECT, settings.PUBSUB_TOPIC)
    future = client.publish(topic, event.model_dump_json().encode(), type=event.type, **telemetry.inject())
    await asyncio.to_thread(future.result, timeout=settings.PUBSUB_PUBLISH_TIMEOUT_SECONDS)


async def shutdown() -> None:
    """Cancels in-process handler tasks still running, so their own cleanup (e.g. back to approved) runs."""
    for task in list(_tasks):
        task.cancel()
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
