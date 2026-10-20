"""A tool's time limit counts the time it works, not the time its CPU job waits for the worker."""

from __future__ import annotations

import asyncio
import time

from langchain_core.tools import StructuredTool

from app.config import settings
from app.cpu_pool import run_cpu
from app.langgraph.detection.tool_timeout import guard_tool


def _tool(job) -> StructuredTool:
    async def run() -> dict:
        return {"value": await run_cpu(job)}

    return guard_tool(StructuredTool.from_function(coroutine=run, name="layout_anomalies_tool", description="test"))


async def _behind_a_busy_worker(seconds: float):
    """Occupies the one CPU worker, and returns once it is busy."""
    blocker = asyncio.create_task(run_cpu(time.sleep, seconds))
    await asyncio.sleep(0.05)
    return blocker


def _short_limits(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TOOL_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(settings, "CPU_QUEUE_TIMEOUT_SECONDS", 5.0)


async def test_a_job_that_waits_longer_than_the_limit_still_runs(monkeypatch) -> None:
    _short_limits(monkeypatch)
    blocker = await _behind_a_busy_worker(0.6)

    result = await _tool(lambda: "done").coroutine()  # type: ignore[misc]

    assert result == {"value": "done"}
    await blocker


async def test_a_job_that_runs_longer_than_the_limit_still_times_out(monkeypatch) -> None:
    _short_limits(monkeypatch)
    result = await _tool(lambda: time.sleep(0.6)).coroutine()  # type: ignore[misc]

    assert "no result within 0.2 seconds" in str(result)


async def test_waiting_has_its_own_cap(monkeypatch) -> None:
    _short_limits(monkeypatch)
    monkeypatch.setattr(settings, "CPU_QUEUE_TIMEOUT_SECONDS", 0.2)
    blocker = await _behind_a_busy_worker(0.8)

    result = await _tool(lambda: "done").coroutine()  # type: ignore[misc]

    assert "waited 0.2 seconds for the CPU worker" in str(result)
    await blocker


async def test_a_tool_that_never_uses_the_worker_keeps_its_plain_limit(monkeypatch) -> None:
    _short_limits(monkeypatch)

    async def slow() -> dict:
        await asyncio.sleep(0.6)
        return {}

    tool = guard_tool(StructuredTool.from_function(coroutine=slow, name="font_consistency_tool", description="test"))

    assert "no result within 0.2 seconds" in str(await tool.coroutine())  # type: ignore[misc]
