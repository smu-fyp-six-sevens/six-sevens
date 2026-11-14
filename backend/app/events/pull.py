"""
The pull adapter (GKE; EVENT_DELIVERY=pull): each role this process runs streams its own subscription, the same
one push used, switched in place. A message is acked when its handler returns and nacked when it raises, so it is
redelivered and, after the subscription's maximum attempts, dead-lettered: the same outcomes as push's 2xx and
5xx. Flow control holds at most PULL_MAX_MESSAGES per role, and the client extends each message's lease while its
run works (up to PULL_MAX_LEASE_SECONDS), so there is no 600 s limit.

Each message gets the scopes main.count_queries gives a push request: query counts, shared case reads, and the
case refreshes flushed before it is acked. Nothing listens for inbound calls; /health and /metrics stay on uvicorn.

At shutdown new messages are nacked, runs in flight get PULL_DRAIN_SECONDS to finish, and the rest are cancelled
(their own cleanup runs) and nacked. Every redelivery counts toward the dead-letter limit, drains included.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .. import case_repository, document_repository, query_stats, telemetry
from ..config import settings
from . import registry
from .envelope import Event
from .types import subscription_id

if TYPE_CHECKING:
    from google.cloud.pubsub_v1.subscriber.message import Message

logger = logging.getLogger(__name__)

# A stream that ends with an error (e.g. a missing permission) restarts after this backoff, doubling to the cap.
_RESTART_FIRST_SECONDS = 1.0
_RESTART_MAX_SECONDS = 60.0
# A stream that ran this long before failing starts its backoff again from the first step.
_HEALTHY_RUN_SECONDS = 60.0

_subscriber: Any = None
_supervisors: list[asyncio.Task[None]] = []
_inflight: set[asyncio.Task[None]] = set()
_accepting = False


def _client() -> Any:
    global _subscriber
    if _subscriber is None:
        from google.cloud import pubsub_v1

        _subscriber = pubsub_v1.SubscriberClient()
    return _subscriber


async def start() -> None:
    """Starts one stream per installed role."""
    global _accepting
    _accepting = True
    loop = asyncio.get_running_loop()
    for role in sorted(registry.installed_roles()):
        _supervisors.append(asyncio.create_task(_supervise(role, loop)))


async def stop() -> None:
    """Stops taking messages, lets runs in flight finish for PULL_DRAIN_SECONDS, then closes the streams."""
    global _accepting
    _accepting = False
    if _inflight:
        logger.info("Waiting up to %.0fs for %d run(s) in flight", settings.PULL_DRAIN_SECONDS, len(_inflight))
        _, pending = await asyncio.wait(set(_inflight), timeout=settings.PULL_DRAIN_SECONDS)
        for task in pending:
            task.cancel()
        if pending:
            logger.warning("Stopped %d run(s) still going after the drain; their messages are redelivered", len(pending))
            await asyncio.gather(*pending, return_exceptions=True)
    for supervisor in _supervisors:
        supervisor.cancel()
    await asyncio.gather(*_supervisors, return_exceptions=True)
    _supervisors.clear()


async def _supervise(role: str, loop: asyncio.AbstractEventLoop) -> None:
    """Keeps the role's stream open, restarting it with backoff if it ends with an error."""
    from google.cloud import pubsub_v1

    subscriber = _client()
    path = subscriber.subscription_path(settings.GOOGLE_CLOUD_PROJECT, subscription_id(role))
    flow_control = pubsub_v1.types.FlowControl(
        max_messages=settings.PULL_MAX_MESSAGES, max_lease_duration=settings.PULL_MAX_LEASE_SECONDS,
    )
    backoff = _RESTART_FIRST_SECONDS
    while True:
        started = time.monotonic()
        future = subscriber.subscribe(path, callback=functools.partial(_receive, role, loop), flow_control=flow_control)
        logger.info("Pulling %s from %s (at most %d at once)", role, path, settings.PULL_MAX_MESSAGES)
        try:
            await asyncio.wrap_future(future)
            logger.warning("Pulling %s ended without an error; restarting", role)
        except asyncio.CancelledError:
            future.cancel()
            await asyncio.to_thread(_wait_closed, future)
            raise
        except Exception:
            logger.exception("Pulling %s stopped; restarting in %.0fs", role, backoff)
        if time.monotonic() - started > _HEALTHY_RUN_SECONDS:
            backoff = _RESTART_FIRST_SECONDS
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, _RESTART_MAX_SECONDS)


def _wait_closed(future: Any) -> None:
    try:
        future.result(timeout=10)
    except Exception:
        pass  # a cancelled stream reports its cancellation here


def _receive(role: str, loop: asyncio.AbstractEventLoop, message: "Message") -> None:
    """Called on the client's thread: hands the message to the event loop."""
    try:
        loop.call_soon_threadsafe(_spawn, role, message)
    except RuntimeError:
        message.nack()  # the loop is closed: the process is going away


def _spawn(role: str, message: "Message") -> None:
    if not _accepting:
        message.nack()
        return
    task = asyncio.create_task(_handle(role, message))
    _inflight.add(task)
    task.add_done_callback(_inflight.discard)


async def _handle(role: str, message: "Message") -> None:
    try:
        event = Event.model_validate_json(message.data)
    except (ValueError, ValidationError) as exc:
        logger.error(
            "Malformed message %s on %s (attempt %s): %s", message.message_id, role, message.delivery_attempt, exc,
        )
        message.nack()
        return
    synced_cases = document_repository.track_request_syncs()
    try:
        # The publisher's traceparent, so the work joins the trace of the request that caused it.
        with (
            query_stats.scope(f"PULL {role}"),
            case_repository.request_scope(),
            telemetry.continued_from(message.attributes),
        ):
            await registry.dispatch(role, event)
            if synced_cases:
                await asyncio.to_thread(document_repository.flush_syncs, synced_cases)
    except asyncio.CancelledError:
        message.nack()
        raise
    except Exception:
        logger.exception(
            "%s failed on %s %s (attempt %s); it will be redelivered",
            role, event.type, event.event_id, message.delivery_attempt,
        )
        message.nack()
        return
    message.ack()
