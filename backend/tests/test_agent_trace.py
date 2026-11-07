"""
Trace Agent Runs (SCRUM-59): each agent run's spans are kept on the run's own trace (telemetry.steps_into), with
their tool calls in order, their Claude calls and any reply that failed validation.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from app import llm_io, telemetry
from app.langgraph import trace
from app.langgraph.tool_spans import traced_tool_call


def _recorder() -> tuple[list[dict], object]:
    steps: list[dict] = []
    return steps, telemetry.steps_into(lambda step, **fields: steps.append({"step": step, **fields}))


def test_a_span_is_recorded_when_it_ends_under_its_parent() -> None:
    steps, recording = _recorder()
    with recording, telemetry.get_tracer().start_as_current_span("outer"):
        telemetry.annotate(kind="run")
        with telemetry.get_tracer().start_as_current_span("inner"):
            telemetry.add_llm_call({"model": "claude-haiku-5-5", "output_tokens": 3, "thinking": ["Checked the totals."]})

    inner, outer = steps
    assert (inner["name"], inner["parent"]) == ("inner", outer["id"])
    assert inner["llm_calls"] == [{"model": "claude-haiku-5-5", "output_tokens": 3, "thinking": ["Checked the totals."]}]
    assert (outer["name"], outer["parent"], outer["kind"]) == ("outer", None, "run")
    assert outer["duration_s"] >= inner["duration_s"] >= 0


def test_a_span_that_raises_keeps_its_error() -> None:
    steps, recording = _recorder()
    with recording, pytest.raises(ValueError), telemetry.get_tracer().start_as_current_span("broken"):
        raise ValueError("bad reply")

    assert steps[0]["error"] == "ValueError: bad reply"


def test_nothing_is_recorded_outside_a_run() -> None:
    with telemetry.get_tracer().start_as_current_span("alone"):
        telemetry.annotate(kind="run")
        assert telemetry.current_step_id() is None


class _Reply(BaseModel):
    summary: str
    confidence: float = 0.0


def test_a_reply_that_fails_validation_is_kept_with_its_raw_payload_agent_and_time() -> None:
    steps, recording = _recorder()
    raw = '{"summary": "One font differs.", "confidence": "high"}'
    with recording, telemetry.get_tracer().start_as_current_span("detection.specialist.layout"):
        checked = llm_io.validate(_Reply, {"summary": "One font differs.", "confidence": "high"}, site="specialist assessment", raw=raw)

    assert checked.status == "degraded"
    (failure,) = steps[0]["validation_failures"]
    assert failure["raw"] == raw
    assert (failure["site"], failure["agent"], failure["status"]) == ("specialist assessment", "detection.specialist.layout", "degraded")
    assert datetime.fromisoformat(failure["at"]).tzinfo is not None


def test_a_documents_steps_name_the_span_they_happened_in() -> None:
    try:
        with trace.steps_into("doc-trace-test"), telemetry.get_tracer().start_as_current_span("detection.run"):
            trace.record("doc-trace-test", "detection_plan", confidence=0.8)
        steps = trace.get("doc-trace-test")["steps"]  # type: ignore[index]
    finally:
        trace.forget(["doc-trace-test"])

    plan, span = steps
    assert plan["in_span"] == span["id"]
    assert (span["step"], span["name"]) == ("span", "detection.run")
    assert plan["at"] <= span["at"]


async def test_a_tool_call_is_a_span_with_its_input_and_outcome() -> None:
    steps, recording = _recorder()

    async def tool() -> dict:
        return {"tool": "font_consistency_tool", "findings": [{"id": "f1"}], "unable_to_assess": False}

    with recording:
        await traced_tool_call("font_consistency_tool", {"document_id": "d", "pages": [1]}, tool)

    (step,) = steps
    assert (step["name"], step["kind"], step["findings"]) == ("tool.font_consistency_tool", "tool", 1)
    assert step["input"] == '{"pages": [1]}'


@pytest.mark.parametrize("tool_calls", [[], [{"name": "font_consistency_tool", "args": {}}, {"name": "spelling_tool", "args": {}}]])
async def test_a_specialist_review_keeps_its_tool_calls_in_order_even_when_there_are_none(tool_calls) -> None:
    from app.langgraph.detection import graph

    report = {
        "specialist": "layout", "tools_called": [c["name"] for c in tool_calls], "tool_calls": tool_calls,
        "findings": [], "dismissed": [], "uncertain": False, "summary": "Nothing worth flagging.", "output_status": "ok",
    }
    steps, recording = _recorder()
    with recording, patch.object(graph, "run_specialist", AsyncMock(return_value=report)), patch.object(graph, "trace"):
        await graph.specialist_node({"document_id": "d", "document_type": "payslip", "name": "layout", "brief": "Fonts."})

    (review,) = steps
    assert (review["name"], review["kind"]) == ("detection.review.layout", "agent")
    assert review["tool_calls"] == tool_calls
