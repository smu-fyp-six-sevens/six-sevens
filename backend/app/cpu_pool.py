"""
Executor for PDF/OCR/forensic work, kept off the default pool that every Supabase call uses.
One worker by default: PyMuPDF isn't thread-safe, even across separate Document objects.
"""

from __future__ import annotations

import asyncio
import functools
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from typing import Any, Callable, Protocol, TypeVar

from .config import settings

T = TypeVar("T")

_executor = ThreadPoolExecutor(
    max_workers=settings.CPU_WORKER_CONCURRENCY, thread_name_prefix="cpu-work"
)


class QueueClock(Protocol):
    """A caller's time limit, told when its work is waiting for a worker and when it starts."""

    def queued(self) -> None: ...

    def running(self) -> None: ...


# Set by tool_timeout.guard_tool so a tool's time limit does not run while its job waits in the queue.
queue_clock: ContextVar[QueueClock | None] = ContextVar("cpu_queue_clock", default=None)


async def run_cpu(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    loop = asyncio.get_running_loop()
    clock = queue_clock.get()
    job = functools.partial(fn, *args, **kwargs)
    if clock is None:
        return await loop.run_in_executor(_executor, job)

    def started() -> T:
        loop.call_soon_threadsafe(clock.running)
        return job()

    clock.queued()
    try:
        return await loop.run_in_executor(_executor, started)
    finally:
        clock.running()
