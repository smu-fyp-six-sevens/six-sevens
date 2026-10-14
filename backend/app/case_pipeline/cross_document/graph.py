"""
Cross-document analysis graph: hypothesise -> investigate each in parallel
-> verify in code. Only conclusive, verified findings leave it.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Required, TypedDict, cast

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from ..models import CrossDocFinding, Hypothesis
from .agents import hypothesise, investigate
from .evidence import CaseContext
from .verify import verify_verdict


class AnalysisState(TypedDict, total=False):
    context: Required[CaseContext]
    hypotheses: list[Hypothesis]
    results: Annotated[list[dict[str, Any]], operator.add]
    findings: list[CrossDocFinding]
    discarded: list[str]
    tool_calls: list[dict[str, Any]]


class InvestigatorInput(TypedDict):
    context: CaseContext
    hypothesis: Hypothesis


async def _hypothesise_node(state: AnalysisState) -> dict:
    return {"hypotheses": await hypothesise(state["context"])}


def _dispatch(state: AnalysisState):
    hypotheses = state.get("hypotheses") or []
    if not hypotheses:
        return "finalise"
    return [Send("investigate", {"context": state["context"], "hypothesis": h}) for h in hypotheses]


async def _investigate_node(state: InvestigatorInput) -> dict:
    verdict, calls = await investigate(state["context"], state["hypothesis"])
    return {"results": [{"hypothesis": state["hypothesis"], "verdict": verdict, "calls": calls}]}


def _finalise_node(state: AnalysisState) -> dict:
    context = state["context"]
    findings: list[CrossDocFinding] = []
    discarded: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for result in state.get("results") or []:
        hypothesis: Hypothesis = result["hypothesis"]
        tool_calls.extend({"hypothesis": hypothesis.id, **c} for c in result["calls"])
        finding, reason = verify_verdict(context, hypothesis, result["verdict"], result["calls"])
        if finding is not None:
            findings.append(finding)
        else:
            # Kept only as a count and a reason in the run's audit, never against the case.
            discarded.append(reason)
    return {"findings": findings, "discarded": discarded, "tool_calls": tool_calls}


def build_cross_document_graph():
    graph = StateGraph(AnalysisState)
    graph.add_node("hypothesise", _hypothesise_node)
    graph.add_node("investigate", _investigate_node)
    graph.add_node("finalise", _finalise_node)
    graph.set_entry_point("hypothesise")
    graph.add_conditional_edges("hypothesise", _dispatch, ["investigate", "finalise"])
    graph.add_edge("investigate", "finalise")
    graph.add_edge("finalise", END)
    return graph.compile(checkpointer=False)


async def run_cross_document_analysis(context: CaseContext) -> AnalysisState:
    graph = build_cross_document_graph()
    return cast(AnalysisState, await graph.ainvoke({"context": context, "results": []}))
