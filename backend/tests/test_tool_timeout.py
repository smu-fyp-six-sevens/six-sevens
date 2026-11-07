"""A tool's time limit counts the time it works, not the time its CPU job waits for the worker."""

from __future__ import annotations

import asyncio
import threading

from langchain_core.tools import StructuredTool

from app.config import settings
from app.cpu_pool import run_cpu
from app.langgraph.detection.tool_timeout import guard_tool


def _tool(job) -> StructuredTool:
    async def run() -> dict:
        return {"value": await run_cpu(job)}

    return guard_tool(StructuredTool.from_function(coroutine=run, name="layout_anomalies_tool", description="test"))


async def _behind_a_busy_worker() -> tuple[asyncio.Task, threading.Event]:
    """Occupies the one CPU worker until the returned event is set, and returns once it is busy."""
    busy, release = threading.Event(), threading.Event()

    def hold() -> None:
        busy.set()
        release.wait(10)

    blocker = asyncio.create_task(run_cpu(hold))
    await asyncio.to_thread(busy.wait, 5)
    return blocker, release


def _short_limits(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TOOL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(settings, "CPU_QUEUE_TIMEOUT_SECONDS", 5.0)


async def test_a_job_that_waits_longer_than_the_limit_still_runs(monkeypatch) -> None:
    _short_limits(monkeypatch)
    blocker, release = await _behind_a_busy_worker()
    call = asyncio.create_task(_tool(lambda: "done").coroutine())  # type: ignore[misc]

    await asyncio.sleep(0.4)  # twice the limit, all of it spent waiting for the worker
    assert not call.done()
    release.set()

    assert await call == {"value": "done"}
    await blocker


async def test_a_job_that_runs_longer_than_the_limit_still_times_out(monkeypatch) -> None:
    _short_limits(monkeypatch)
    finish = threading.Event()
    try:
        result = await _tool(lambda: finish.wait(10)).coroutine()  # type: ignore[misc]
    finally:
        finish.set()  # the job's thread ends with the test instead of running into the next one

    assert "no result within 0.2 seconds" in str(result)


async def test_waiting_has_its_own_cap(monkeypatch) -> None:
    _short_limits(monkeypatch)
    monkeypatch.setattr(settings, "CPU_QUEUE_TIMEOUT_SECONDS", 0.2)
    blocker, release = await _behind_a_busy_worker()
    try:
        result = await _tool(lambda: "done").coroutine()  # type: ignore[misc]
    finally:
        release.set()
        await blocker

    assert "waited 0.2 seconds for the CPU worker" in str(result)


async def test_a_tool_that_never_uses_the_worker_keeps_its_plain_limit(monkeypatch) -> None:
    _short_limits(monkeypatch)

    async def slow() -> dict:
        await asyncio.sleep(0.6)
        return {}

    tool = guard_tool(StructuredTool.from_function(coroutine=slow, name="font_consistency_tool", description="test"))

    assert "no result within 0.2 seconds" in str(await tool.coroutine())  # type: ignore[misc]
