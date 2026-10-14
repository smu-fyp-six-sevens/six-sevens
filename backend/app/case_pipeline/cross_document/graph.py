"""
Cross-document analysis: plan -> run the checks -> judge -> verify in code.
Two model calls in total. Only conclusive, verified findings leave it.
"""

from __future__ import annotations

from typing import Any, Required, TypedDict, cast

from langgraph.graph import END, StateGraph

from ..models import CrossDocFinding, Hypothesis
from .agents import adjudicate, gather_evidence, hypothesise
from .evidence import CaseContext
from .verify import verify_verdict


class AnalysisState(TypedDict, total=False):
    context: Required[CaseContext]
    hypotheses: list[Hypothesis]
    evidence: dict[str, list[dict[str, Any]]]
    calls: dict[str, list[dict[str, Any]]]
    verdicts: dict[str, dict[str, Any]]
    findings: list[CrossDocFinding]
    discarded: list[str]
    tool_calls: list[dict[str, Any]]


async def _plan_node(state: AnalysisState) -> dict:
    return {"hypotheses": await hypothesise(state["context"])}


def _after_plan(state: AnalysisState) -> str:
    return "gather" if state.get("hypotheses") else "finalise"


async def _gather_node(state: AnalysisState) -> dict:
    evidence, calls = await gather_evidence(state["context"], state.get("hypotheses") or [])
    return {"evidence": evidence, "calls": calls}


async def _judge_node(state: AnalysisState) -> dict:
    verdicts = await adjudicate(state["context"], state.get("hypotheses") or [], state.get("evidence") or {})
    return {"verdicts": verdicts}


def _finalise_node(state: AnalysisState) -> dict:
    context = state["context"]
    calls_by_hypothesis = state.get("calls") or {}
    verdicts = state.get("verdicts") or {}
    findings: list[CrossDocFinding] = []
    discarded: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for hypothesis in state.get("hypotheses") or []:
        calls = calls_by_hypothesis.get(hypothesis.id, [])
        tool_calls.extend({"hypothesis": hypothesis.id, **c} for c in calls)
        verdict = verdicts.get(hypothesis.id)
        if verdict is None:
            # Not the same as "not conclusive": the judge gave no verdict (a cut-off or non-JSON reply).
            finding, reason = None, "the judge returned no verdict for it (reply cut off or not JSON)"
        else:
            finding, reason = verify_verdict(context, hypothesis, verdict, calls)
        context.note(
            "verify", hypothesis=hypothesis.id, kept=finding is not None, reason=reason or None,
            finding=finding.model_dump(mode="json") if finding is not None else None,
        )
        if finding is not None:
            findings.append(finding)
        else:
            # Kept only as a count and a reason in the run's audit, never against the case.
            discarded.append(reason)
    return {"findings": findings, "discarded": discarded, "tool_calls": tool_calls}


def build_cross_document_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("plan", _plan_node)
    graph.add_node("gather", _gather_node)
    graph.add_node("judge", _judge_node)
    graph.add_node("finalise", _finalise_node)
    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", _after_plan, {"gather": "gather", "finalise": "finalise"})
    graph.add_edge("gather", "judge")
    graph.add_edge("judge", "finalise")
    graph.add_edge("finalise", END)
    return graph.compile(checkpointer=False)


async def run_cross_document_analysis(context: CaseContext) -> AnalysisState:
    graph = build_cross_document_graph()
    return cast(AnalysisState, await graph.ainvoke({"context": context}))
