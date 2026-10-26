"""
Executor for PDF/OCR/forensic work, kept off the default pool that every Supabase call uses.

Threads by default, one worker: PyMuPDF isn't thread-safe, even across separate Document objects.
With CPU_WORKER_PROCESSES > 0 (the deployed workers), plain module-level functions run in that many
processes instead, each with its own PyMuPDF, so a 2-vCPU service uses both cores. Anything else
(a bound method such as the land-use index lookup, a closure, a test's mock) stays on the thread.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextvars import ContextVar
from typing import Any, Callable, Protocol, TypeVar

from .config import settings

T = TypeVar("T")
logger = logging.getLogger("classifier_pipeline.cpu_pool")

_executor = ThreadPoolExecutor(
    max_workers=settings.CPU_WORKER_CONCURRENCY, thread_name_prefix="cpu-work"
)
# Created on first use, inside the running loop.
_processes: ProcessPoolExecutor | None = None
_process_slots: asyncio.Semaphore | None = None


class QueueClock(Protocol):
    """A caller's time limit, told when its work is waiting for a worker and when it starts."""

    def queued(self) -> None: ...

    def running(self) -> None: ...


# Set by tool_timeout.guard_tool so a tool's time limit does not run while its job waits in the queue.
queue_clock: ContextVar[QueueClock | None] = ContextVar("cpu_queue_clock", default=None)


def _init_process() -> None:
    # A spawned process starts with no logging set up; match main.py's so its INFO lines reach the logs.
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _runs_in_process(fn: Callable[..., Any]) -> bool:
    """Only a module-level function can be sent to another process by name."""
    return settings.CPU_WORKER_PROCESSES > 0 and inspect.isfunction(fn) and "<" not in fn.__qualname__


async def run_cpu(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    job = functools.partial(fn, *args, **kwargs)
    if _runs_in_process(fn):
        return await _run_in_process(job)
    loop = asyncio.get_running_loop()
    clock = queue_clock.get()
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


async def _run_in_process(job: Callable[[], T]) -> T:
    global _processes, _process_slots
    if _processes is None or _process_slots is None:
        _processes = ProcessPoolExecutor(
            max_workers=settings.CPU_WORKER_PROCESSES,
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_init_process,
        )
        _process_slots = asyncio.Semaphore(settings.CPU_WORKER_PROCESSES)
    pool, slots = _processes, _process_slots
    clock = queue_clock.get()
    if clock is not None:
        clock.queued()
    try:
        # A free slot means a free process, so the job starts now: that's when its time limit starts.
        async with slots:
            if clock is not None:
                clock.running()
            return await asyncio.get_running_loop().run_in_executor(pool, job)
    except BrokenProcessPool:
        # A process died (e.g. a crash inside PyMuPDF): start a fresh pool for the next job.
        logger.error("A CPU worker process died; starting a new pool")
        if _processes is pool:
            _processes = None
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    finally:
        if clock is not None:
            clock.running()
