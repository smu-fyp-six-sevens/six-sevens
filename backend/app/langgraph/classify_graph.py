"""
Classify subgraph: rule_classify runs deterministically (the only
deterministic step), then the agent takes over — it decides itself whether
to call llm_classify_tool and/or vision_llm, but NOT in whatever order: the
agent's first turn is only ever offered llm_classify_tool; vision_llm only
becomes available starting from its second turn onward, and only until it's
been called once. This is a code-enforced ordering (see _available_tools),
not just a prompt suggestion — vision is by far the most expensive tool
(a full image, not just text), so it never gets to be the agent's first or
only move. Within that ordering the agent still genuinely decides whether
to call either tool at all, which is what keeps this agentic rather than a
hardcoded pipeline with an LLM bolted on.

    rule_classify -> agent (turn 1: llm_classify_tool only)
                       -> [tools -> agent (turn 2+: vision_llm only)]* -> validate -> repair

Also holds the reclassify entry point: when Extraction escalates,
graph.py routes back into this same agent/tools/validate/repair chain via
prepare_reclassify_node, continuing the same conversation rather than
re-running rule_classify. Since that conversation already has at least one
prior agent turn, only vision_llm (if not already used) is offered there —
llm_classify_tool already had its one shot on the original pass.

No checkpointer here (`compile(checkpointer=False)` in graph.py); only the
parent graph persists.
"""

from __future__ import annotations

import logging
from typing import cast

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from . import text_cache, trace
from .cache_gate import cache_gate
from .classification_confidence import compute_classification_confidence, tool_results
from .. import telemetry
from .classify_support import agent_prompt_parts, validate_agent_output
from .llm_classify import llm_classify_tool
from .rule_classify import classify_by_rules
from .state import DocumentGraphState
from .vision_tool import vision_llm
from ..config import settings
from ..llm_io import message_text
from ..run_context import spend_model_call

logger = logging.getLogger("classifier_pipeline.langgraph.classify")

_TOOLS = [llm_classify_tool, vision_llm]


def _raw_text(state: DocumentGraphState) -> str:
    return text_cache.get(state["document_id"]) or ""


def _called_tool_names(messages: list) -> set[str]:
    names: set[str] = set()
    for message in messages:
        if isinstance(message, AIMessage):
            names.update(tc["name"] for tc in (message.tool_calls or []))
    return names


def _available_tools(existing_messages: list) -> list:
    """
    Enforces the ordering described in this module's docstring: llm_classify_tool
    only on the agent's first turn, vision_llm only from the second turn
    onward (and only until it's been called once) — never the reverse, and
    never both offered in the same turn.
    """
    if not existing_messages:
        return [llm_classify_tool]
    if "vision_llm" in _called_tool_names(existing_messages):
        return []
    return [vision_llm]


def rule_node(state: DocumentGraphState) -> dict:
    """The only deterministic step — everything after this is the agent's call."""
    filename = state.get("filename")
    assert filename is not None, "ingest_node must set filename before the classify subgraph runs"
    result = classify_by_rules(filename, _raw_text(state))
    top = (result.get("top_candidates") or [None])[0]
    trace.record(
        state["document_id"], "rule_classify",
        rule_top_type=top["document_type"] if top else None,
        rule_top_strength=top["match_strength"] if top else None,
        certain_type=result.get("certain_type"),
    )
    return {"rule_result": result}


async def agent_node(state: DocumentGraphState) -> dict:
    from ._anthropic import build_chat_anthropic

    existing = state.get("agent_messages") or []
    spend_model_call("classify agent")
    available_tools = _available_tools(existing)
    model = build_chat_anthropic(settings.LANGGRAPH_SONNET_MODEL, 2048, effort="low")
    if available_tools:
        model = model.bind_tools(available_tools)

    with telemetry.get_tracer().start_as_current_span("classify.agent_call") as span:
        span.set_attribute("document_id", state["document_id"])
        span.set_attribute("turn", "continuation" if existing else "first")
        span.set_attribute("offered_tools", [t.name for t in available_tools])

        if existing:
            # Tool-loop continuation: the system message (with its
            # cache_control breakpoint) is already in `existing`, so there's
            # nothing new to gate — this call is naturally later than
            # whichever call first warmed the cache for this key.
            # ChatAnthropic always returns an AIMessage; ainvoke's declared
            # return type is the more general BaseMessage.
            response = cast(AIMessage, await model.ainvoke(existing))
            telemetry.record_usage(span, response)
            span.set_attribute("tool_calls", [tc["name"] for tc in (response.tool_calls or [])])
            trace.record(
                state["document_id"], "agent_call",
                offered_tools=[t.name for t in available_tools],
                tool_calls=[tc["name"] for tc in (response.tool_calls or [])],
            )
            return {"agent_messages": [response]}

        # Static instructions and the per-document inputs are two separate
        # content blocks so cache_control only covers the byte-stable half —
        # concatenating them into one block would make the whole thing vary
        # per document and never hit the cache. See classify_support.agent_prompt_parts.
        static_prompt, dynamic_inputs = agent_prompt_parts(state.get("rule_result"), _raw_text(state))
        model_input = [
            SystemMessage(content=[
                {"type": "text", "text": static_prompt, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": dynamic_inputs},
            ]),
            HumanMessage(content="Classify this document."),
        ]
        response = cast(AIMessage, await cache_gate.run("classify_agent", lambda: model.ainvoke(model_input)))
        telemetry.record_usage(span, response)
        span.set_attribute("tool_calls", [tc["name"] for tc in (response.tool_calls or [])])
        trace.record(
            state["document_id"], "agent_call",
            offered_tools=[t.name for t in available_tools],
            tool_calls=[tc["name"] for tc in (response.tool_calls or [])],
        )
        # agent_messages uses the add_messages reducer (see state.py), which
        # appends — only the messages new to this call are returned, not the
        # accumulated history, or they'd be duplicated.
        return {"agent_messages": [*model_input, response]}


def should_continue_agent(state: DocumentGraphState) -> str:
    last = (state.get("agent_messages") or [])[-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "validate"


def build_reconsideration_message(escalation: dict, evidence: list[str] | None = None) -> HumanMessage:
    """
    Builds the reconsideration turn as a chat message: the next turn in the same conversation, not a
    fresh classification — see prepare_reclassify_node.
    """
    reason = escalation.get("reason", "")
    suggested = escalation.get("suggested_document_type")
    evidence = evidence if evidence is not None else (escalation.get("evidence") or [])
    if suggested:
        text = f"Extraction thinks this document may actually be a {suggested}: {reason}"
    else:
        text = f"Extraction judged your classification incorrect: {reason}"
    if evidence:
        bullets = "\n".join(f"- {line}" for line in evidence)
        text += f"\n\nEvidence from the document text:\n{bullets}"
    text += "\n\nPlease reconsider your classification given this."
    return HumanMessage(content=text)


def prepare_reclassify_node(state: DocumentGraphState) -> dict:
    """
    Reclassify entry point (see graph.py's decide_after_extract):
    always re-enters at the agent, never re-runs rule_classify — a bare
    regex re-guess has nothing new to react to, whereas the agent can
    actually weigh Extraction's specific objection (and re-call its tools if
    it decides it needs to).

    Always a continuation of the existing `agent_messages` conversation:
    the agent runs at least
    once for every document now (there's no code path that finalises
    without it), so agent_messages is guaranteed non-empty by the time
    Extraction — and therefore a possible reclassify — could happen.
    """
    document_id = state["document_id"]
    escalation = (state.get("extraction") or {}).get("escalation") or {}
    new_messages = [build_reconsideration_message(escalation)]

    trace.record(document_id, "reclassify", reason=escalation.get("reason"))
    return {"agent_messages": new_messages, "classification": None}


def _apply_computed_confidence(state: DocumentGraphState, classification: dict) -> dict:
    """
    Replaces the agent's self-reported confidence with one computed from the
    sources it actually consulted (see classification_confidence.py). The
    agent's own needs_manual_review stays OR'd in as an extra signal; it can
    only make routing stricter, never looser.
    """
    computed = compute_classification_confidence(
        classification.get("document_type"),
        state.get("rule_result"),
        tool_results(state.get("agent_messages") or []),
    )
    trace.record(
        state["document_id"], "confidence",
        agent_confidence=classification.get("confidence"),
        computed_confidence=computed["confidence"],
        basis=computed["basis"],
        sources=computed["sources"],
    )
    return {
        **classification,
        "confidence": computed["confidence"],
        "needs_manual_review": bool(classification.get("needs_manual_review")) or computed["needs_manual_review"],
        # "certain_match" when a rule trigger fixed the type (e.g. "REPUBLIC OF
        # SINGAPORE IDENTITY CARD"); read when deciding whether to reclassify.
        "basis": computed["basis"],
    }


def validate_node(state: DocumentGraphState) -> dict:
    last = (state.get("agent_messages") or [])[-1]
    content = message_text(last.content)
    classification = validate_agent_output(content)
    trace.record(
        state["document_id"], "validate",
        document_type=classification.get("document_type"), output_status=classification.get("output_status"),
    )
    if classification.get("document_type"):
        classification = _apply_computed_confidence(state, classification)
    return {"classification": classification}


async def repair_node(state: DocumentGraphState) -> dict:
    """
    Deterministic cleanup already happened inside validate_agent_output
    (strips to the {...} span). If that still couldn't produce a
    document_type, one Haiku re-ask; if that also fails, manual review.
    """
    classification = state.get("classification") or {}
    if classification.get("document_type"):
        return {}

    from ._anthropic import build_chat_anthropic

    last = (state.get("agent_messages") or [])[-1]
    bad_output = message_text(last.content)
    repair_model = build_chat_anthropic(settings.LANGGRAPH_HAIKU_MODEL, 1024, effort="low")
    try:
        spend_model_call("classify repair")
        response = await repair_model.ainvoke([
            SystemMessage(content=[
                {
                    "type": "text",
                    "text": "Extract ONLY a valid JSON object of the shape "
                    '{"document_type": "...", "confidence": 0.0, "evidence": [...], '
                    '"reasoning": "...", "needs_manual_review": true, "fulfilled_requirements": [...]} '
                    "from the text below. If no such object can be recovered, respond with "
                    '{"document_type": null}.',
                    "cache_control": {"type": "ephemeral"},
                },
            ]),
            HumanMessage(content=bad_output),
        ])
        repaired = validate_agent_output(message_text(response.content))
    except Exception as exc:
        logger.warning("Repair re-ask failed: %s", exc)
        repaired = {}

    if repaired.get("document_type"):
        return {"classification": _apply_computed_confidence(state, repaired)}

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


def build_classify_graph():
    graph = StateGraph(DocumentGraphState)
    graph.add_node("rule_classify", rule_node)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(_TOOLS, messages_key="agent_messages", handle_tool_errors=True))
    graph.add_node("validate", validate_node)
    graph.add_node("repair", repair_node)

    graph.set_entry_point("rule_classify")
    graph.add_edge("rule_classify", "agent")
    graph.add_conditional_edges("agent", should_continue_agent, {"tools": "tools", "validate": "validate"})
    graph.add_edge("tools", "agent")
    graph.add_edge("validate", "repair")
    graph.add_edge("repair", END)

    return graph.compile(checkpointer=False)


classify_graph = None  # built lazily — see get_classify_graph()


def get_classify_graph():
    global classify_graph
    if classify_graph is None:
        classify_graph = build_classify_graph()
    return classify_graph
