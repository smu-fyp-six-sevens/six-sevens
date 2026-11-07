"""
The case analysis agent's loop (case_pipeline/cross_document/agents.py) with Claude scripted: what code keeps of the
planner's hypotheses before any check runs, how a reply that doesn't call its tool is asked once more and then read
from its text, the run's ceiling on model calls, a judge reply that can't be read, and a check that fails or hangs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.case_pipeline.cross_document import agents
from app.case_pipeline.cross_document.evidence import build_context
from app.case_pipeline.models import Hypothesis, PlannedCheck
from app.case_pipeline.trace import RunTrace
from app.models import DocumentRecord, DocumentStatus


class ScriptedClaude:
    """build_chat_anthropic(...).bind_tools(...): each call answers with the next scripted reply."""

    def __init__(self, *replies: AIMessage) -> None:
        self.replies = list(replies)
        self.prompts: list[list] = []

    def bind_tools(self, tools, tool_choice=None) -> ScriptedClaude:
        return self

    async def ainvoke(self, messages):
        self.prompts.append(list(messages))
        return self.replies.pop(0)


def calls(tool: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": "call-1"}],
                     response_metadata={"stop_reason": "tool_use"})


def says(text: str) -> AIMessage:
    return AIMessage(content=text, response_metadata={"stop_reason": "end_turn"})


@pytest.fixture
def claude(monkeypatch):
    def script(*replies: AIMessage) -> ScriptedClaude:
        model = ScriptedClaude(*replies)
        monkeypatch.setattr(agents, "build_chat_anthropic", lambda *args, **kwargs: model)
        return model
    return script


def document(document_type: str, document_id: str, **fields) -> DocumentRecord:
    return DocumentRecord(id=document_id, case_id="case-1", filename=f"{document_id}.pdf", status=DocumentStatus.DETECTED,
                          uploaded_at=datetime(2026, 11, 1, tzinfo=timezone.utc), document_type=document_type,
                          extracted_fields=fields)


@pytest.fixture
def context():
    case = build_context("case-1", [
        document("payslip", "doc-pay", net_pay="4160.00", employer_name="Brightwave Analytics Pte Ltd"),
        document("bank_statement", "doc-bs", transactions=[{"date": "05 OCT", "description": "SALARY", "amount": 3100}],
                 statement_period_start="01 Oct 2026", statement_period_end="31 Oct 2026"),
    ], {})
    case.trace = RunTrace("run-1")  # in memory: only save() writes it
    return case


def noted(context, step: str) -> dict:
    [event] = [e for e in context.trace.events if e["step"] == step]
    return event


def hypothesis_item(claim: str, checks: list[dict], **extra) -> dict:
    return {"claim": claim, "confidence": 0.6, "checks": checks, **extra}


SALARY_CHECKS = [
    {"tool": "get_fields", "args": {"document": "payslip_1", "names": ["net_pay"]}},
    {"tool": "query_transactions", "args": {"document": "bank_statement_1", "description_contains": "salary"}},
]


# --- What code keeps of the plan ----------------------------------------------------------------------------------


async def test_a_planned_hypothesis_is_kept_with_its_checks_and_a_probe_on_its_refutation_document(context, claude) -> None:
    claude(calls("submit_hypotheses", confidence=0.7, hypotheses=[hypothesis_item(
        "Salary credits fall short of net pay", SALARY_CHECKS, refutation_document="bank_statement_1",
        documents=["payslip_1", "bank_statement_1"],
    )]))

    hypotheses, confidence = await agents.hypothesise(context)

    [kept] = hypotheses
    assert (kept.id, kept.refutation_document, confidence) == ("h1", "bank_statement_1", 0.7)
    # No check was marked the probe: the one on the refutation document is.
    assert [(c.tool, c.refutation_probe) for c in kept.checks] == [("get_fields", False), ("query_transactions", True)]


async def test_hypotheses_that_cannot_be_tested_here_are_dropped_before_any_check_runs(context, claude) -> None:
    claude(calls("submit_hypotheses", hypotheses=[
        hypothesis_item("The director's name suggests he is foreign", SALARY_CHECKS),
        hypothesis_item("Net pay is high for the role", [{"tool": "get_fields", "args": {"document": "payslip_1",
                                                                                        "names": ["net_pay"]}}]),
        hypothesis_item("Uses a tool that does not exist", [{"tool": "delete_everything", "args": {}}]),
        hypothesis_item("   ", SALARY_CHECKS),
        "not a hypothesis",
    ]))

    hypotheses, _ = await agents.hypothesise(context)

    assert hypotheses == []
    planned = noted(context, "hypotheses")
    assert planned["dropped_for_bias"] == [
        {"claim": "The director's name suggests he is foreign", "reason": "touches a protected attribute"}]
    assert planned["dropped_for_scope"] == [{"claim": "Net pay is high for the role", "reason": "one document, nothing computed"}]


async def test_at_most_three_hypotheses_are_tested_and_an_unknown_refutation_document_is_ignored(context, claude) -> None:
    claude(calls("submit_hypotheses", hypotheses=[
        hypothesis_item(f"Claim {n}", SALARY_CHECKS, refutation_document="bank_statement_9") for n in range(5)
    ]))

    hypotheses, _ = await agents.hypothesise(context)

    assert [h.claim for h in hypotheses] == ["Claim 0", "Claim 1", "Claim 2"]
    assert {h.refutation_document for h in hypotheses} == {None}
    # With no refutation document named, the last check is made the probe.
    assert [c.refutation_probe for c in hypotheses[0].checks] == [False, True]


async def test_a_case_with_no_approved_documents_is_not_sent_to_claude(claude) -> None:
    model = claude()

    assert await agents.hypothesise(build_context("case-1", [], {})) == ([], 0.0)
    assert model.prompts == []


# --- A reply that doesn't call its tool -----------------------------------------------------------------------------


async def test_a_reply_without_the_tool_call_is_asked_once_more(context, claude) -> None:
    model = claude(says("Let me think about this case first."), calls("submit_hypotheses", hypotheses=[
        hypothesis_item("Salary credits fall short of net pay", SALARY_CHECKS)]))

    hypotheses, _ = await agents.hypothesise(context)

    assert len(hypotheses) == 1
    reminder = model.prompts[1][-1]
    assert isinstance(reminder, HumanMessage)
    assert reminder.content == "Your last reply did not call submit_hypotheses. Reply only by calling submit_hypotheses."
    assert context.model_calls == 2


async def test_hypotheses_written_as_json_text_are_read(context, claude) -> None:
    claude(says('{"confidence": 0.4, "hypotheses": [{"claim": "Salary credits fall short of net pay", '
                '"checks": [{"tool": "get_fields", "args": {"document": "payslip_1", "names": ["net_pay"]}}, '
                '{"tool": "query_transactions", "args": {"document": "bank_statement_1"}}]}]}'))

    hypotheses, confidence = await agents.hypothesise(context)

    assert ([h.claim for h in hypotheses], confidence) == (["Salary credits fall short of net pay"], 0.4)
    assert context.model_calls == 1  # readable text is an answer: not asked again


# --- The judge ---------------------------------------------------------------------------------------------------


def salary_hypothesis() -> Hypothesis:
    return Hypothesis(id="h1", claim="Salary credits fall short of net pay",
                      checks=[PlannedCheck(**check) for check in SALARY_CHECKS])


async def test_the_judges_verdicts_are_read_by_hypothesis(context, claude) -> None:
    claude(calls("submit_verdicts", verdicts=[
        {"id": "h1", "conclusive": True, "severity": "medium", "citations": ["ref:payslip_1:net_pay"]},
        {"id": "h9", "conclusive": True},  # no such hypothesis: ignored by the caller
        {"conclusive": True},  # no id
    ]))

    verdicts = await agents.adjudicate(context, [salary_hypothesis()], {"h1": []})

    assert set(verdicts) == {"h1", "h9"}
    assert verdicts["h1"]["citations"] == ["ref:payslip_1:net_pay"]


async def test_a_judge_reply_that_cannot_be_read_leaves_every_hypothesis_inconclusive(context, claude) -> None:
    claude(says("I think h1 is probably true."), says("On reflection, h1 is true."))

    verdicts = await agents.adjudicate(context, [salary_hypothesis()], {"h1": []})

    assert verdicts == {}
    assert context.model_calls == 2
    reply = noted(context, "judge_reply")
    assert (reply["via"], reply["parsed"], reply["raw"]) == ("text", False, "On reflection, h1 is true.")
    verdict = noted(context, "verdict")
    assert (verdict["hypothesis"], verdict["parsed"]) == ("h1", False)


async def test_no_hypotheses_means_no_judge_call(context, claude) -> None:
    model = claude()

    assert await agents.adjudicate(context, [], {}) == {}
    assert model.prompts == []


# --- The run's ceiling on model calls --------------------------------------------------------------------------------


async def test_a_run_never_makes_more_model_calls_than_its_ceiling(context, claude) -> None:
    claude(says("thinking"), says("still thinking"), says("and more"), says("and more"), says("never sent"))

    await agents.hypothesise(context)  # two calls, neither usable
    await agents.adjudicate(context, [salary_hypothesis()], {"h1": []})  # two more: the ceiling

    assert context.model_calls == agents.MAX_MODEL_CALLS_PER_RUN
    with pytest.raises(agents.ModelCallBudgetExceeded):
        await agents.adjudicate(context, [salary_hypothesis()], {"h1": []})


# --- Running the checks -----------------------------------------------------------------------------------------------


async def test_each_hypothesis_checks_run_with_the_evidence_tools(context) -> None:
    evidence, calls_by_hypothesis = await agents.gather_evidence(context, [salary_hypothesis()])

    assert [e["tool"] for e in evidence["h1"]] == ["get_fields", "query_transactions"]
    assert all(call["available"] for call in calls_by_hypothesis["h1"])


class BrokenTool:
    """An evidence tool that hangs or raises, in place of the real one."""

    def __init__(self, name: str, behaviour) -> None:
        self.name = name
        self._behaviour = behaviour

    async def ainvoke(self, args: dict):
        return await self._behaviour()


async def _hangs():
    await asyncio.sleep(1)


async def _raises():
    raise ValueError("bad arguments")


async def test_a_check_that_fails_or_hangs_is_recorded_unavailable_and_the_rest_still_run(context, monkeypatch) -> None:
    monkeypatch.setattr(agents.settings, "TOOL_TIMEOUT_SECONDS", 0.05)
    real_build_tools = agents.build_tools
    broken = {"monthly_flow": _hangs, "list_documents": _raises}
    monkeypatch.setattr(agents, "build_tools", lambda case, calls: [
        BrokenTool(t.name, broken[t.name]) if t.name in broken else t for t in real_build_tools(case, calls)
    ])
    hypothesis = Hypothesis(id="h1", claim="Salary credits fall short of net pay", checks=[
        PlannedCheck(tool="monthly_flow", args={"document": "bank_statement_1"}),
        PlannedCheck(tool="list_documents", args={}),
        PlannedCheck(tool="get_fields", args={"document": "payslip_1", "names": ["net_pay"]}),
    ])

    evidence, calls_by_hypothesis = await agents.gather_evidence(context, [hypothesis])

    results = [e["result"] for e in evidence["h1"]]
    assert results[:2] == [
        '{"available": false, "reason": "Tool error: no result within 0.05 seconds"}',
        '{"available": false, "reason": "Tool error: bad arguments"}',
    ]
    assert '"available": true' in results[2]
    assert [c["available"] for c in calls_by_hypothesis["h1"]] == [False, False, True]
