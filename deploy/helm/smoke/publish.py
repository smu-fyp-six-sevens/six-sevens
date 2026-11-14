"""
Publishes one event to each consumer on the kind cluster's Pub/Sub emulator (the smoke test). The ids name nothing,
so each handler finds nothing to do; the test checks only that every worker received its event.
"""

import asyncio

from app.events import bus, types
from app.events.envelope import Event

EVENTS = [
    Event(type=types.DOCUMENT_CANCELLED, actor_type="system", actor="smoke", document_id="smoke-doc"),
    Event(type=types.DETECTION_CANCEL_REQUESTED, actor_type="system", actor="smoke", document_id="smoke-doc"),
    Event(type=types.CASE_ANALYSIS_REQUESTED, actor_type="system", actor="smoke", case_id="smoke-case"),
]


async def main() -> None:
    for event in EVENTS:
        await bus.publish(event)
        print(f"published {event.type}", flush=True)


asyncio.run(main())
