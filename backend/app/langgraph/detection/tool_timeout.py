"""
A time limit on each detection tool. A tool that hangs (an outside registry, a stuck regex, a lock) ends as
"unable to assess", so the specialist and the run carry on instead of waiting forever.

The limit does not run while a tool's job waits for the single CPU worker (a long queue is not a slow
tool); it counts the time the tool is actually working. A wait has its own, longer cap
(CPU_QUEUE_TIMEOUT_SECONDS), because a thread that is already running cannot be stopped: a hung CPU check
keeps the worker busy, and the checks queued behind it would otherwise wait for ever. Stopping one for
good needs a process boundary.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import weakref

from langchain_core.tools import BaseTool, StructuredTool

from ...config import settings
from ...cpu_pool import queue_clock
from .tools import _result

logger = logging.getLogger("classifier_pipeline.langgraph.detection.tool_timeout")

# These call a model or an outside registry.
# prompt_injection_tool and hidden_text_tool re-read (and for an image, OCR) a document from before the content guard.
_SLOW_TOOLS = frozenset({
    "ai_generation_tool", "numerical_checks_tool", "external_validation_tool", "prompt_injection_tool", "hidden_text_tool",
})
# Each wrapper guard_tool made -> the coroutine it limits, so guarding again (or guarding a copy) re-wraps the
# original instead of stacking limits or keeping a stale one. Not tool ids: Python reuses them after collection.
_originals: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def timeout_for(tool_name: str) -> float:
    return settings.SLOW_TOOL_TIMEOUT_SECONDS if tool_name in _SLOW_TOOLS else settings.TOOL_TIMEOUT_SECONDS


class _ToolClock:
    """A tool's time limit that is paused while its CPU job is queued (see cpu_pool.run_cpu)."""

    def __init__(self, scope: asyncio.Timeout, seconds: float) -> None:
        self._scope = scope
        self._seconds = seconds
        self._loop = asyncio.get_running_loop()
        self._spent = 0.0
        self._since: float | None = self._loop.time()  # None while queued
        self.expired_while_queued = False

    def _reschedule(self, when: float) -> None:
        try:
            self._scope.reschedule(when)
        except RuntimeError:  # the limit already fired or the tool already finished
            pass

    def queued(self) -> None:
        if self._since is None:
            return
        now = self._loop.time()
        self._spent += now - self._since
        self._since = None
        self._reschedule(now + settings.CPU_QUEUE_TIMEOUT_SECONDS)

    def running(self) -> None:
        if self._scope.expired():  # called while the cancelled job unwinds
            self.expired_while_queued = self.expired_while_queued or self._since is None
            return
        if self._since is not None:
            return
        now = self._loop.time()
        self._since = now
        self._reschedule(now + max(0.0, self._seconds - self._spent))


def guard_tool(tool: BaseTool) -> BaseTool:
    """The same tool, now limited in time. Safe to call twice on one tool."""
    # Every detection tool is a StructuredTool (@tool); a BaseTool has no coroutine to wrap.
    if not isinstance(tool, StructuredTool):
        return tool
    original = tool.coroutine
    if original is None:
        return tool
    original = _originals.get(original, original)
    seconds = timeout_for(tool.name)

    @functools.wraps(original)
    async def limited(*args, **kwargs):
        clock: _ToolClock | None = None
        try:
            async with asyncio.timeout(seconds) as scope:
                clock = _ToolClock(scope, seconds)
                token = queue_clock.set(clock)
                try:
                    return await original(*args, **kwargs)
                finally:
                    queue_clock.reset(token)
        except TimeoutError:
            if clock is not None and clock.expired_while_queued:
                reason = f"Execution timeout: waited {settings.CPU_QUEUE_TIMEOUT_SECONDS:g} seconds for the CPU worker without starting."
            else:
                reason = f"Execution timeout: no result within {seconds:g} seconds."
            logger.error("%s did not finish: %s", tool.name, reason)
            return _result(tool.name, [], unable=reason)

    tool.coroutine = limited
    _originals[limited] = original
    return tool
