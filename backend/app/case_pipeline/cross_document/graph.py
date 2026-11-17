"""
Cross-document analysis: plan -> run the checks -> judge -> verify in code.
Two model calls in total. Only conclusive, verified findings leave it.
"""

from __future__ import annotations

from typing import Any, Required, TypedDict, cast

from langgraph.graph import END, StateGraph

from ...llm_io import probability
from ..models import CrossDocFinding, Hypothesis
from .agents import adjudicate, gather_evidence, hypothesise
from .bias import has_protected_text
from .deterministic import brief, restates_a_code_finding, run_deterministic_checks
from .evidence import CaseContext
from .feedback import ids_by_category, load_feedback, render as render_feedback
from .verify import verify_verdict


class AnalysisState(TypedDict, total=False):
    context: Required[CaseContext]
    hypotheses: list[Hypothesis]
    planner_confidence: float
    evidence: dict[str, list[dict[str, Any]]]
    calls: dict[str, list[dict[str, Any]]]
    verdicts: dict[str, dict[str, Any]]
    findings: list[CrossDocFinding]
    discarded: list[str]
    tool_calls: list[dict[str, Any]]
    consistent_checks: list[str]
    agent_decisions: list[dict[str, Any]]
    # Hypotheses the judge couldn't settle, with the document it says would: what the evidence requests start from.
    open_questions: list[dict[str, str]]


def _consistent_line(context: CaseContext, call: dict[str, Any]) -> str | None:
    """One plain line for a comparison that found no difference, e.g. "UEN is the same on a.pdf and b.pdf"."""
    tool, args = call.get("tool"), call.get("args") or {}
    if tool in ("company_name_consistency_tool", "address_consistency_tool"):
        view = context.resolve(str(args.get("document") or ""))
        what = "Company name" if tool == "company_name_consistency_tool" else "Addresses"
        return f"{what} on {view.filename} match the other documents" if view else None
    if tool == "compare_linked_people":
        first, second = (context.resolve(str(args.get(k) or "")) for k in ("document_a", "document_b"))
        if first is None or second is None:
            return None
        what = "name" if args.get("check") == "name" else "ID number"
        return f"The individual's {what} is the same on {first.filename} and {second.filename}"
    if tool in ("compare_identifiers", "compare_people"):
        items = args.get("items") or []
        views = [context.resolve(str(i.get("document") or "")) for i in items if isinstance(i, dict)]
        names = [v.filename for v in views if v]
        fields = sorted({str(i.get("field")) for i in items if isinstance(i, dict) and i.get("field")})
        if len(names) >= 2 and fields:
            return f"{', '.join(f.replace('_', ' ') for f in fields)} is the same on {', '.join(names)}"
    return None


async def _plan_node(state: AnalysisState) -> dict:
    hypotheses, confidence = await hypothesise(state["context"])
    return {"hypotheses": hypotheses, "planner_confidence": confidence}


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
    consistent: list[str] = []
    agent_decisions: list[dict[str, Any]] = []
    open_questions: list[dict[str, str]] = []
    hypotheses = state.get("hypotheses") or []
    agent_decisions.append({
        "agent": "hypothesis_planner",
        "decision_id": "plan",
        "decision": f"proposed {len(hypotheses)} hypothesis(es)",
        "confidence": probability(state.get("planner_confidence")),
    })
    for hypothesis in hypotheses:
        agent_decisions.append({
            "agent": "hypothesis_planner",
            "decision_id": hypothesis.id,
            "decision": "proposed for evidence testing",
            "confidence": hypothesis.confidence,
        })
        calls = calls_by_hypothesis.get(hypothesis.id, [])
        tool_calls.extend({"hypothesis": hypothesis.id, **c} for c in calls)
        for call in calls:
            line = _consistent_line(context, call) if call.get("consistent") else None
            if line and line not in consistent:
                consistent.append(line)
        verdict = verdicts.get(hypothesis.id)
        agent_decisions.append({
            "agent": "hypothesis_judge",
            "decision_id": hypothesis.id,
            "decision": "conclusive" if verdict and verdict.get("conclusive") is True else "inconclusive",
            "confidence": probability(verdict.get("confidence")) if verdict else 0.0,
        })
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
            question = _open_question(hypothesis, verdict)
            if question is not None:
                open_questions.append(question)
    return {
        "findings": findings,
        "discarded": discarded,
        "tool_calls": tool_calls,
        "consistent_checks": consistent,
        "agent_decisions": agent_decisions,
        "open_questions": open_questions,
    }


def _open_question(hypothesis: Hypothesis, verdict: dict[str, Any] | None) -> dict[str, str] | None:
    """An unsettled hypothesis and the document the judge says would settle it; None when it names none."""
    if verdict is None or verdict.get("conclusive") is True:
        return None
    document = " ".join(str(verdict.get("would_settle_with") or "").split())[:200]
    # Claims were screened before testing; the judge's own words are screened again before they reach a request.
    if not document or has_protected_text(f"{hypothesis.claim} {document}"):
        return None
    return {"question": hypothesis.claim[:300], "would_settle_with": document}


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
    """Code-side comparisons first (always), then the agent. The result holds both, one finding per difference."""
    established = run_deterministic_checks(context)
    context.established = brief(established, context)
    context.note(
        "deterministic", findings=[f.model_dump(mode="json") for f in established.findings],
        consistent=established.consistent,
    )
    # What officers taught it on other cases: looked up once, shown to both model calls. Never stops the analysis.
    try:
        shown = await load_feedback(context.case_id)
    except Exception:
        shown = {}
    context.feedback_text = render_feedback(shown)
    context.feedback_ids = ids_by_category(shown)
    context.note("feedback", shown={category: len(items) for category, items in shown.items()})
    graph = build_cross_document_graph()
    state = cast(AnalysisState, await graph.ainvoke({"context": context}))
    # The agent may still say again what the code found; one row for each fact is enough.
    from_agent = state.get("findings") or []
    repeated = [f for f in from_agent if restates_a_code_finding(established.findings, f)]
    if repeated:
        context.note("restated_by_agent", dropped=[f.id for f in repeated])
        state["discarded"] = [*(state.get("discarded") or []), *("restates a comparison made by code" for _ in repeated)]
    state["findings"] = [*established.findings, *(f for f in from_agent if f not in repeated)]
    state["consistent_checks"] = list(dict.fromkeys([*established.consistent, *(state.get("consistent_checks") or [])]))
    return state
