from __future__ import annotations

import asyncio
from collections import defaultdict


class CaseBroadcaster:
    """
    In-memory, one per process. The only piece that touches both the
    WebSocket layer (routers/ws.py, which subscribes/unsubscribes as
    connections come and go) and the Realtime layer (realtime_listener.py,
    which is the only thing allowed to call publish() — see its module
    docstring for why orchestration code must never call this directly).

    subscribe/unsubscribe/publish are plain synchronous functions with no
    `await` inside them — there's no I/O in touching an in-memory dict/set,
    and keeping them that way is what makes them atomic with respect to
    each other under asyncio's cooperative single-threaded scheduling.
    Nothing here needs a lock; see WS.MD for the full reasoning (confirmed
    against this app's actual dependency, realtime-py, which shares
    FastAPI's own event loop rather than running its own thread).
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, case_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[case_id].add(queue)
        return queue

    def unsubscribe(self, case_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(case_id)
        if subscribers is None:
            return
        subscribers.discard(queue)
        if not subscribers:
            del self._subscribers[case_id]

    def publish(self, case_id: str, snapshot: dict) -> None:
        # Iterate a copy, not the live set — forecloses "RuntimeError: Set
        # changed size during iteration" even if a future edit adds an
        # `await` somewhere in this loop (put_nowait itself never awaits).
        for queue in list(self._subscribers.get(case_id, ())):
            queue.put_nowait(snapshot)


broadcaster = CaseBroadcaster()
