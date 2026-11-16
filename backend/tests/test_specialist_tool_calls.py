"""A specialist's tool calls: identical repeats refused, rebinds kept apart, unassessed findings, the lead's fallback."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from app.langgraph.detection import graph, specialists

_TOOL = "numerical_checks_tool"


def _call(call_id: str, args: dict | None = None, name: str = _TOOL) -> dict:
    return {"name": name, "args": args or {}, "id": call_id}


def _result(call_id: str, findings: list[dict] | None = None, *, unable: bool = False, name: str = _TOOL) -> ToolMessage:
    body = {"tool": name, "unable_to_assess": unable, "reason": "no fields" if unable else None, "findings": findings or []}
    return ToolMessage(content=json.dumps(body), tool_call_id=call_id, name=name)


def _finding(n: int = 1, summary: str = "Net pay doesn't add up") -> dict:
    return {"id": f"{_TOOL}-{n}", "summary": summary, "severity_hint": "inconsistent"}


async def _run_wrapper(messages: list, call: dict, returned: ToolMessage | None = None):
    execute = AsyncMock(return_value=returned or _result(call["id"]))
    request = SimpleNamespace(state={"messages": messages}, tool_call=call)
    return await specialists._refuse_repeats(request, execute), execute


# --- repeats ------------------------------------------------------------------------------------------


async def test_an_identical_call_after_a_success_is_refused_and_not_run() -> None:
    bindings = {"bindings": [{"role": "net_pay", "field": "net_pay"}]}
    messages = [AIMessage(content="", tool_calls=[_call("c1", bindings)]), _result("c1", [_finding()]),
                AIMessage(content="", tool_calls=[_call("c2", bindings)])]

    message, execute = await _run_wrapper(messages, _call("c2", bindings))

    execute.assert_not_called()
    assert message.additional_kwargs[specialists._REPEAT_KEY] == "c1"
    assert message.tool_call_id == "c2" and "Already run" in message.content


async def test_the_same_call_twice_in_one_turn_runs_once() -> None:
    messages = [AIMessage(content="", tool_calls=[_call("c1"), _call("c2")])]

    first, run_first = await _run_wrapper(messages, _call("c1"))
    second, run_second = await _run_wrapper(messages, _call("c2"))

    run_first.assert_awaited_once()
    run_second.assert_not_called()
    assert second.additional_kwargs[specialists._REPEAT_KEY] == "c1"


async def test_a_failed_or_unassessed_call_may_be_redone_as_it_was() -> None:
    for earlier in (
        ToolMessage(content="boom", tool_call_id="c1", name=_TOOL, status="error"),
        _result("c1", unable=True),
    ):
        messages = [AIMessage(content="", tool_calls=[_call("c1")]), earlier,
                    AIMessage(content="", tool_calls=[_call("c2")])]

        message, execute = await _run_wrapper(messages, _call("c2"), _result("c2", [_finding()]))

        execute.assert_awaited_once()
        assert json.loads(message.content)["findings"][0]["id"] == f"{_TOOL}-1"  # nothing earlier to clash with


async def test_a_rebind_runs_and_its_findings_get_ids_of_their_own() -> None:
    messages = [AIMessage(content="", tool_calls=[_call("c1", {"bindings": []})]), _result("c1", [_finding()]),
                AIMessage(content="", tool_calls=[_call("c2", {"bindings": [{"role": "net_pay", "field": "pay"}]})])]
    rebind = _call("c2", {"bindings": [{"role": "net_pay", "field": "pay"}]})

    message, execute = await _run_wrapper(messages, rebind, _result("c2", [_finding(summary="CPF rate is wrong")]))

    execute.assert_awaited_once()
    assert json.loads(message.content)["findings"][0]["id"] == f"{_TOOL}-1-call2"


def test_a_rebinds_findings_are_kept_beside_the_first_calls_and_a_refusal_is_not_a_gap() -> None:
    first_args, second_args = {"bindings": []}, {"bindings": [{"role": "net_pay", "field": "pay"}]}
    second = _result("c2", [{**_finding(), "id": f"{_TOOL}-1-call2", "summary": "CPF rate is wrong"}])
    refused = ToolMessage(content=specialists._REPEAT_NOTE, tool_call_id="c3", name=_TOOL,
                          additional_kwargs={specialists._REPEAT_KEY: "c1"})
    messages = [
        AIMessage(content="", tool_calls=[_call("c1", first_args)]), _result("c1", [_finding()]),
        AIMessage(content="", tool_calls=[_call("c2", second_args), _call("c3", first_args)]), second, refused,
        AIMessage(content=json.dumps({"summary": "done", "assessments": [
            {"finding_id": f"{_TOOL}-1", "dismiss": False, "severity": "low", "rationale": "a", "confidence": 0.9},
            {"finding_id": f"{_TOOL}-1-call2", "dismiss": False, "severity": "high", "rationale": "b", "confidence": 0.9},
        ]})),
    ]
    state = {"messages": messages, "specialist": "content_validation", "document_type": "nric"}

    report = specialists._collect_node(cast(Any, state))["report"]

    assert sorted((f["id"], f["severity"]) for f in report["findings"]) == [
        (f"{_TOOL}-1", "low"), (f"{_TOOL}-1-call2", "high"),
    ]
    assert report["unable_to_assess"] == []


# --- unassessed findings ------------------------------------------------------------------------------


def test_an_unassessed_finding_is_low_kept_and_not_approved() -> None:
    messages = [
        AIMessage(content="", tool_calls=[_call("c1", name="spelling_tool")]),
        _result("c1", [{"id": "spelling_tool-1", "summary": "Misspelt: 'Singpore'"}], name="spelling_tool"),
        AIMessage(content=json.dumps({"summary": "done", "assessments": []})),
    ]
    state = {"messages": messages, "specialist": "content_validation", "document_type": "nric"}

    [kept] = specialists._collect_node(cast(Any, state))["report"]["findings"]

    assert kept["severity"] == "low" and kept["ai_accepted"] is False
    assert kept["rationale"] == "Not assessed by the specialist."


def test_an_assessed_finding_with_an_unreadable_severity_stays_medium() -> None:
    messages = [
        AIMessage(content="", tool_calls=[_call("c1", name="spelling_tool")]),
        _result("c1", [{"id": "spelling_tool-1", "summary": "Misspelt"}], name="spelling_tool"),
        AIMessage(content=json.dumps({"summary": "done", "assessments": [
            {"finding_id": "spelling_tool-1", "dismiss": False, "severity": "severe", "rationale": "x", "confidence": 0.5},
        ]})),
    ]
    state = {"messages": messages, "specialist": "content_validation", "document_type": "nric"}

    [kept] = specialists._collect_node(cast(Any, state))["report"]["findings"]

    assert kept["severity"] == "medium" and kept["ai_accepted"] is True


# --- the metadata check runs off the event loop -------------------------------------------------------


async def test_the_metadata_check_runs_in_a_worker_thread() -> None:
    from app.langgraph.detection import tools as detection_tools

    seen: list[threading.Thread] = []

    def check(*_args):
        seen.append(threading.current_thread())
        return SimpleNamespace(unable_to_assess=True, unable_to_assess_reason="no metadata")

    document = SimpleNamespace(metadata={}, document_type="payslip")
    with (
        patch.object(detection_tools, "_document_with_metadata", AsyncMock(return_value=document)),
        patch.object(detection_tools, "check_metadata_anomalies", check),
    ):
        result = await detection_tools.metadata_tool.coroutine("doc-1")

    assert result["unable_to_assess"] is True
    assert seen and seen[0] is not threading.main_thread()


# --- the lead's fallback says why ---------------------------------------------------------------------


async def test_a_failed_lead_call_records_why_the_briefs_fell_back() -> None:
    failures: list[str] = []
    with (
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=MagicMock()),
        patch.object(graph, "spend_model_call", side_effect=RuntimeError("overloaded")),
    ):
        reply = await graph._ask_lead("system", {}, graph.LeadPlan, span_name="detection.lead_plan",
                                      cache_key="k", failures=failures)

    assert reply is None
    assert failures == ["The call failed: RuntimeError: overloaded"]


async def test_an_unreadable_lead_reply_records_why() -> None:
    failures: list[str] = []
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content="not json"))

    async def run(_key, call):
        return await call()

    with (
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model),
        patch.object(graph, "spend_model_call"),
        patch("app.langgraph.cache_gate.cache_gate.run", new=run),
    ):
        reply = await graph._ask_lead("system", {}, graph.LeadPlan, span_name="detection.lead_plan",
                                      cache_key="k", failures=failures)

    assert reply is None
    assert failures == ["The reply could not be read."]


async def test_the_plan_step_puts_the_reason_on_the_trace() -> None:
    async def fail(*_args, failures=None, **_kwargs):
        failures.append("The call failed: TimeoutError: ")
        return None

    with patch.object(graph, "_ask_lead", fail), patch.object(graph.trace, "record") as record:
        await graph.lead_plan_node(cast(Any, {"document_id": "doc-1", "context": {"document_type": "payslip"}}))

    kwargs = record.call_args.kwargs
    assert kwargs["fallback_reason"] == "The call failed: TimeoutError: "
    assert set(kwargs["fallback_briefs"]) == set(specialists.SPECIALISTS)
