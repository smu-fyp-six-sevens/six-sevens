"""Which handlers this process runs, per role. main.py installs the roles in ROLE."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping

from .envelope import Event
from .types import EVERY_TYPE, SUBSCRIPTIONS

logger = logging.getLogger(__name__)

Handler = Callable[[Event], Awaitable[None]]

_by_role: dict[str, dict[str, Handler]] = {}


def install(role: str, handlers: Mapping[str, Handler]) -> None:
    unsubscribed = set(handlers) - SUBSCRIPTIONS[role]
    if unsubscribed:
        raise ValueError(f"{role} handles {sorted(unsubscribed)} but does not subscribe to them")
    _by_role[role] = dict(handlers)


def installed_roles() -> set[str]:
    return set(_by_role)


def clear() -> None:
    _by_role.clear()


def handlers_for(event_type: str, role: str | None = None) -> list[Handler]:
    """The handlers for a type: of one role (a push), or of every installed role (in-process)."""
    roles = [role] if role is not None else list(_by_role)
    found: list[Handler] = []
    for name in roles:
        handlers = _by_role.get(name, {})
        handler = handlers.get(event_type) or handlers.get(EVERY_TYPE)
        if handler is not None:
            found.append(handler)
    return found


async def dispatch(role: str, event: Event) -> None:
    """Runs one role's handler for the event, if it has one. A type it does not handle is ignored."""
    for handler in handlers_for(event.type, role):
        await handler(event)
