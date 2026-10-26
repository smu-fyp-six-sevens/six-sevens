"""Which handlers this process runs, per role. main.py installs the roles in ROLE."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Mapping

from opentelemetry.trace import SpanKind

from .. import metrics, telemetry
from .envelope import Event
from .types import EVERY_TYPE, SUBSCRIPTIONS

logger = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]

_by_role: dict[str, dict[str, Handler]] = {}


def install(role: str, handlers: Mapping[str, Handler]) -> None:
    # A role subscribed to every type may still handle some types specially (audit-metrics runs the sweep).
    unsubscribed = set() if EVERY_TYPE in SUBSCRIPTIONS[role] else set(handlers) - SUBSCRIPTIONS[role]
    if unsubscribed:
        raise ValueError(f"{role} handles {sorted(unsubscribed)} but does not subscribe to them")
    _by_role[role] = dict(handlers)


def installed_roles() -> set[str]:
    return set(_by_role)


def clear() -> None:
    _by_role.clear()


def handlers_for(event_type: str, role: str | None = None) -> list[tuple[str, Handler]]:
    """(role, handler) for a type: of one role (a push), or of every installed role (in-process)."""
    roles = [role] if role is not None else list(_by_role)
    found: list[tuple[str, Handler]] = []
    for name in roles:
        handlers = _by_role.get(name, {})
        handler = handlers.get(event_type) or handlers.get(EVERY_TYPE)
        if handler is not None:
            found.append((name, handler))
    return found


async def run_handler(role: str, handler: Handler, event: Event) -> None:
    """Runs one handler, counting and timing it. Raises what the handler raises."""
    # Audit stores every type; one label for it keeps the series few.
    type_label = event.type if EVERY_TYPE not in SUBSCRIPTIONS[role] else EVERY_TYPE
    started = time.perf_counter()
    result = "error"
    try:
        with telemetry.get_tracer().start_as_current_span(f"handle {role} {type_label}", kind=SpanKind.CONSUMER) as span:
            span.set_attribute("event.id", event.event_id)
            await handler(event)
        result = "ok"
    finally:
        metrics.events_handled.labels(role, type_label, result).inc()
        metrics.handler_duration.labels(role, type_label).observe(time.perf_counter() - started)


async def dispatch(role: str, event: Event) -> None:
    """Runs one role's handler for the event, if it has one. A type it does not handle is ignored."""
    for name, handler in handlers_for(event.type, role):
        await run_handler(name, handler, event)
