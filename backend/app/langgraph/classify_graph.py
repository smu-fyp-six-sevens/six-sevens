"""
Classify subgraph (Phase 2): rule_classify | llm_classify in parallel ->
deterministic router -> either finalise straight from those two sources, or
hand off to a Sonnet ReAct agent (one tool: vision_llm) -> validate -> repair.

No checkpointer here (`compile(checkpointer=False)` in graph.py) — only the
parent graph persists, per LANGGRAPH_MIGRATION_PLAN.md section 5.
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from . import router, text_cache, trace
from .cache_gate import cache_gate
from .classify_support import agent_prompt_parts, validate_agent_output
from .llm_classify import classify_by_llm
from .rule_classify import classify_by_rules
from .state import DocumentGraphState
from .vision_tool import vision_llm
from ..config import settings

logger = logging.getLogger("classifier_pipeline.langgraph.classify")

_TOOLS = [vision_llm]


def _raw_text(state: DocumentGraphState) -> str:
    return text_cache.get(state["document_id"]) or ""


def rule_node(state: DocumentGraphState) -> dict:
    result = classify_by_rules(state["filename"], _raw_text(state))
    top = (result.get("top_candidates") or [None])[0]
    trace.record(
        state["document_id"], "rule_classify",
        rule_top_type=top["document_type"] if top else None,
        rule_top_strength=top["match_strength"] if top else None,
    )
    return {"rule_result": result}


async def llm_node(state: DocumentGraphState) -> dict:
    result = await classify_by_llm(_raw_text(state))
    top = (result.get("top_candidates") or [None])[0]
    trace.record(
        state["document_id"], "llm_classify",
        llm_top_type=top["document_type"] if top else None,
        llm_top_strength=top["match_strength"] if top else None,
    )
    return {"llm_result": result}


def route_after_classifiers(state: DocumentGraphState) -> str:
    decision = router.decide_route(state.get("rule_result"), state.get("llm_result"), _raw_text(state))
    trace.record(state["document_id"], "route", route=decision)
    return decision


def _capped_evidence(*sources: list[str], limit: int = 4) -> list[str]:
    merged: list[str] = []
    for source in sources:
        for item in source:
            if item not in merged:
                merged.append(item)
    return merged[:limit]


def finalise_fast(state: DocumentGraphState) -> dict:
    """
    No Claude call — rule_classify and llm_classify already agree strongly
    enough (see router.py) that there is nothing for the agent to add.
    """
    rule_result = state.get("rule_result") or {}
    llm_result = state.get("llm_result") or {}
    rule_top = rule_result["top_candidates"][0]
    llm_top = llm_result["top_candidates"][0]
    document_type = rule_top["document_type"]
    confidence = min(0.95, (rule_top["match_strength"] + llm_top["match_strength"]) / 2)

    fulfilled_requirements = ["id_document"] if document_type in ("passport", "nric") else [document_type]
    if document_type in ("nric", "bank_statement") and router.address_check(rule_result, True) == "visible":
        fulfilled_requirements.append("proof_of_address")

    classification = {
        "document_type": document_type,
        "confidence": round(confidence, 4),
        "evidence": _capped_evidence(rule_top.get("evidence", []), llm_top.get("evidence", [])),
        "reasoning": (
            f"Rule and LLM classifiers agreed on {document_type} with strengths "
            f"{rule_top['match_strength']:.2f}/{llm_top['match_strength']:.2f}; no agent call needed."
        ),
        "needs_manual_review": False,
        "fulfilled_requirements": fulfilled_requirements,
    }
    return {"route": router.FAST, "classification": classification}


async def agent_node(state: DocumentGraphState) -> dict:
    from langchain_anthropic import ChatAnthropic  # local import: optional dep until phase 0 is installed

    existing = state.get("agent_messages") or []
    model = ChatAnthropic(
        model=settings.LANGGRAPH_SONNET_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        temperature=0,
        max_tokens=1024,
    ).bind_tools(_TOOLS)

    if existing:
        # Tool-loop continuation: the system message (with its cache_control
        # breakpoint) is already in `existing`, so there's nothing new to
        # gate — this call is naturally later than whichever call first
        # warmed the cache for this key.
        response: AIMessage = await model.ainvoke(existing)
        trace.record(state["document_id"], "agent_call", tool_calls=[tc["name"] for tc in (response.tool_calls or [])])
        return {"agent_messages": [response], "route": router.AGENT}

    # Static instructions and the per-document inputs are two separate
    # content blocks so cache_control only covers the byte-stable half —
    # concatenating them into one block would make the whole thing vary
    # per document and never hit the cache. See classify_support.agent_prompt_parts.
    static_prompt, dynamic_inputs = agent_prompt_parts(
        state.get("rule_result"), state.get("llm_result"), _raw_text(state)
    )
    model_input = [
        SystemMessage(content=[
            {"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic_inputs},
        ]),
        HumanMessage(content="Classify this document."),
    ]
    response = await cache_gate.run("classify_agent", lambda: model.ainvoke(model_input))
    trace.record(state["document_id"], "agent_call", tool_calls=[tc["name"] for tc in (response.tool_calls or [])])
    # agent_messages uses the add_messages reducer (see state.py), which
    # appends — only the messages new to this call are returned, not the
    # accumulated history, or they'd be duplicated.
    return {"agent_messages": [*model_input, response], "route": router.AGENT}


def should_continue_agent(state: DocumentGraphState) -> str:
    last = state["agent_messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "validate"


def validate_node(state: DocumentGraphState) -> dict:
    last = state["agent_messages"][-1]
    content = last.content if isinstance(last.content, str) else str(last.content)
    classification = validate_agent_output(content)
    trace.record(state["document_id"], "validate", document_type=classification.get("document_type"))
    return {"classification": classification}


async def repair_node(state: DocumentGraphState) -> dict:
    """
    Deterministic cleanup already happened inside validate_agent_output
    (strips to the {...} span). If that still couldn't produce a
    document_type, one Haiku re-ask; if that also fails, manual review.
    See LANGGRAPH_MIGRATION_PLAN.md section 5, "Repair node".
    """
    classification = state.get("classification") or {}
    if classification.get("document_type"):
        return {}

    from langchain_anthropic import ChatAnthropic  # local import: optional dep until phase 0 is installed

    last = state["agent_messages"][-1]
    bad_output = last.content if isinstance(last.content, str) else str(last.content)
    repair_model = ChatAnthropic(
        model=settings.LANGGRAPH_HAIKU_MODEL,
        api_key=settings.ANTHROPIC_API_KEY,
        temperature=0,
        max_tokens=512,
    )
    try:
        response = await repair_model.ainvoke([
            SystemMessage(
                content="Extract ONLY a valid JSON object of the shape "
                '{"document_type": "...", "confidence": 0.0, "evidence": [...], '
                '"reasoning": "...", "needs_manual_review": true, "fulfilled_requirements": [...]} '
                "from the text below. If no such object can be recovered, respond with "
                '{"document_type": null}.'
            ),
            HumanMessage(content=bad_output),
        ])
        repaired = validate_agent_output(response.content if isinstance(response.content, str) else str(response.content))
    except Exception as exc:  # noqa: BLE001 — repair failing is itself a valid outcome, not a crash
        logger.warning("Repair re-ask failed: %s", exc)
        repaired = {}

    if repaired.get("document_type"):
        return {"classification": repaired}

    return {
        "classification": {
            "document_type": "unrecognised",
            "confidence": 0.0,
            "evidence": [],
            "reasoning": "Agent output could not be parsed even after a repair re-ask; routed to manual review.",
            "needs_manual_review": True,
            "fulfilled_requirements": [],
        }
    }


def _join(state: DocumentGraphState) -> dict:
    """No-op join point: both rule_classify and llm_classify feed into this
    node via plain edges, so LangGraph waits for both branches of the
    parallel fan-out to finish before running it (and therefore before the
    conditional edge below ever sees a partial result)."""
    return {}


def build_classify_graph():
    graph = StateGraph(DocumentGraphState)
    graph.add_node("rule_classify", rule_node)
    graph.add_node("llm_classify", llm_node)
    # Named "join_classifiers", not "route" — a node name can't collide with
    # a state key, and "route" is already a DocumentGraphState field.
    graph.add_node("join_classifiers", _join)
    graph.add_node("finalise_fast", finalise_fast)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(_TOOLS, messages_key="agent_messages", handle_tool_errors=True))
    graph.add_node("validate", validate_node)
    graph.add_node("repair", repair_node)

    graph.set_entry_point("rule_classify")
    graph.set_entry_point("llm_classify")
    graph.add_edge("rule_classify", "join_classifiers")
    graph.add_edge("llm_classify", "join_classifiers")

    graph.add_conditional_edges(
        "join_classifiers",
        route_after_classifiers,
        {router.FAST: "finalise_fast", router.AGENT: "agent"},
    )

    graph.add_conditional_edges("agent", should_continue_agent, {"tools": "tools", "validate": "validate"})
    graph.add_edge("tools", "agent")
    graph.add_edge("validate", "repair")
    graph.add_edge("finalise_fast", END)
    graph.add_edge("repair", END)

    return graph.compile(checkpointer=False)


classify_graph = None  # built lazily — see get_classify_graph()


def get_classify_graph():
    global classify_graph
    if classify_graph is None:
        classify_graph = build_classify_graph()
    return classify_graph
