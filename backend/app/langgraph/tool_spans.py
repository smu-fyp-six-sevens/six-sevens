"""
A span for each tool call an agent makes, so the run's trace shows the call with its input, its outcome and how
long it took (telemetry.steps_into). Detection tools get it from guard_tool, the classifier's from trace_tool.
"""

from __future__ import annotations

import functools
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TypeVar

from langchain_core.tools import BaseTool, StructuredTool

from .. import telemetry

T = TypeVar("T")

# Arguments the code fills in, not the model: they say nothing about the call.
_HIDDEN_ARGS = frozenset({
    "document_id", "state", "config", "tool_call_id", "file_bytes", "raw_text", "storage_path", "filename",
})


def _outcome(result: Any) -> dict[str, Any]:
    """What a tool returned, in brief: a detection tool's contract fields, else the value itself."""
    if isinstance(result, Mapping) and "findings" in result:
        findings = result.get("findings") or []
        return {
            "findings": len(findings) if isinstance(findings, list) else findings,
            "unable_to_assess": bool(result.get("unable_to_assess")),
            "nothing_to_check": bool(result.get("nothing_to_check")),
            "reason": result.get("reason"),
            "output": telemetry.short(result),
        }
    return {"output": telemetry.short(result)}


async def traced_tool_call(name: str, args: Mapping[str, Any], call: Callable[[], Awaitable[T]]) -> T:
    with telemetry.get_tracer().start_as_current_span(f"tool.{name}"):
        telemetry.annotate(kind="tool", tool=name, input=telemetry.short({k: v for k, v in args.items() if k not in _HIDDEN_ARGS}))
        result = await call()
        telemetry.annotate(**_outcome(result))
        return result


def trace_tool(tool: BaseTool) -> BaseTool:
    """The same tool, its calls traced. For a tool with a coroutine (every @tool here is one)."""
    if not isinstance(tool, StructuredTool) or tool.coroutine is None:
        return tool
    original = tool.coroutine

    @functools.wraps(original)
    async def traced(*args, **kwargs):
        return await traced_tool_call(tool.name, kwargs, lambda: original(*args, **kwargs))

    tool.coroutine = traced
    return tool
