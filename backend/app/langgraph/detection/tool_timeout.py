"""
A time limit on each detection tool. A tool that hangs (an outside registry, a stuck regex, a lock) ends as
"unable to assess", so the specialist and the run carry on instead of waiting forever.

The limit also covers waiting for the single CPU worker. A thread that is already running cannot be stopped,
so a hung CPU check keeps that worker busy until it returns, and later CPU checks queue behind it and
time out too. Stopping one for good needs a process boundary.
"""

from __future__ import annotations

import asyncio
import functools
import logging

from langchain_core.tools import BaseTool, StructuredTool

from ...config import settings
from .tools import _result

logger = logging.getLogger("classifier_pipeline.langgraph.detection.tool_timeout")

# These call a model or an outside registry.
_SLOW_TOOLS = frozenset({"ai_generation_tool", "numerical_checks_tool", "external_validation_tool"})
_guarded: set[int] = set()


def timeout_for(tool_name: str) -> float:
    return settings.SLOW_TOOL_TIMEOUT_SECONDS if tool_name in _SLOW_TOOLS else settings.TOOL_TIMEOUT_SECONDS


def guard_tool(tool: BaseTool) -> BaseTool:
    """The same tool, now limited in time. Safe to call twice on one tool."""
    # Every detection tool is a StructuredTool (@tool); a BaseTool has no coroutine to wrap.
    if not isinstance(tool, StructuredTool):
        return tool
    original = tool.coroutine
    if original is None or id(tool) in _guarded:
        return tool
    seconds = timeout_for(tool.name)

    @functools.wraps(original)
    async def limited(*args, **kwargs):
        try:
            return await asyncio.wait_for(original(*args, **kwargs), timeout=seconds)
        except TimeoutError:
            logger.error("%s did not finish within %gs", tool.name, seconds)
            return _result(tool.name, [], unable=f"Execution timeout: no result within {seconds:g} seconds.")

    tool.coroutine = limited
    _guarded.add(id(tool))
    return tool
