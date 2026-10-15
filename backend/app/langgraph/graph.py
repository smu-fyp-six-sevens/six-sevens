"""
Document graph: ingest -> classify -> finalise_classification ->
extract -> ANALYZED/FAILED, with a bounded classify<->extract reclassify loop
(settings.MAX_RECLASSIFICATION_ATTEMPTS) when Extraction judges the
classified type wrong. The reclassify branch re-enters at the Sonnet agent
directly (see classify_graph.prepare_reclassify_node), not the whole classify
subgraph: a bare rule re-guess has nothing new to react to, whereas the agent
can actually weigh Extraction's objection — the conversation just continues.

No checkpointer yet (see PENDING_WORK.md).
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from . import classify_graph, nodes
from .classify_graph import get_classify_graph
from .state import DocumentGraphState
from ..config import settings


def _route_after_ingest(state: DocumentGraphState) -> str:
    return "fail" if state.get("status") == "failed" else "classify"


def _route_after_check_text(state: DocumentGraphState) -> str:
    return "fail" if state.get("status") == "failed" else "extract"


def _escalation_disputes_type(state: DocumentGraphState) -> bool:
    """
    Whether Extraction's escalation could change the document type. It can't
    when it names the type the document already has (the generic schema
    reporting "this is an NRIC" on an NRIC), or names none while a rule
    trigger made the type certain: Extraction then really means a field it
    expected is missing (a redacted NRIC number), which manual review
    handles. Reclassifying would only land on the same type and fail.
    """
    escalation = (state.get("extraction") or {}).get("escalation")
    if not escalation:
        return False
    classification = state.get("classification") or {}
    suggested = escalation.get("suggested_document_type") if isinstance(escalation, dict) else None
    if suggested:
        return suggested != classification.get("document_type")
    return classification.get("basis") != "certain_match"


def _decide_after_extract(state: DocumentGraphState) -> str:
    if not _escalation_disputes_type(state):
        return "analyzed"
    if state.get("attempts", 0) < settings.MAX_RECLASSIFICATION_ATTEMPTS:
        return "reclassify"
    return "unresolved"


def build_document_graph():
    graph = StateGraph(DocumentGraphState)
    graph.add_node("ingest", nodes.ingest_node)
    graph.add_node("classify", get_classify_graph())
    graph.add_node("finalise_classification", nodes.finalise_classification_node)
    graph.add_node("check_text_available", nodes.check_text_available)
    graph.add_node("extract", nodes.extract_node)
    graph.add_node("mark_analyzed", nodes.mark_analyzed_node)
    graph.add_node("mark_unresolved_escalation", nodes.mark_unresolved_escalation_node)
    graph.add_node("fail", nodes.fail_node)

    # --- Reclassify loop ---
    # Own nodes, not the "classify" subgraph reused wholesale: this always
    # goes straight to the agent (see prepare_reclassify_node's docstring),
    # skipping rule_classify, which only makes sense on a document's first pass.
    graph.add_node("record_escalation", nodes.record_escalation_node)
    graph.add_node("prepare_reclassify", classify_graph.prepare_reclassify_node)
    graph.add_node("reclassify_agent", classify_graph.agent_node)
    graph.add_node(
        "reclassify_tools",
        ToolNode(classify_graph._TOOLS, messages_key="agent_messages", handle_tool_errors=True),
    )
    graph.add_node("reclassify_validate", classify_graph.validate_node)
    graph.add_node("reclassify_repair", classify_graph.repair_node)

    graph.set_entry_point("ingest")
    graph.add_conditional_edges("ingest", _route_after_ingest, {"classify": "classify", "fail": "fail"})
    graph.add_edge("classify", "finalise_classification")
    graph.add_edge("finalise_classification", "check_text_available")
    graph.add_conditional_edges(
        "check_text_available", _route_after_check_text, {"extract": "extract", "fail": "fail"}
    )
    graph.add_conditional_edges(
        "extract",
        _decide_after_extract,
        {"analyzed": "mark_analyzed", "reclassify": "record_escalation", "unresolved": "mark_unresolved_escalation"},
    )
    graph.add_edge("mark_analyzed", END)
    graph.add_edge("mark_unresolved_escalation", "fail")
    graph.add_edge("fail", END)

    # record_escalation -> prepare_reclassify -> agent loop -> validate ->
    # repair -> back into the shared finalise_classification/extract path,
    # closing the loop (attempts was already incremented by record_escalation).
    graph.add_edge("record_escalation", "prepare_reclassify")
    graph.add_edge("prepare_reclassify", "reclassify_agent")
    graph.add_conditional_edges(
        "reclassify_agent",
        classify_graph.should_continue_agent,
        {"tools": "reclassify_tools", "validate": "reclassify_validate"},
    )
    graph.add_edge("reclassify_tools", "reclassify_agent")
    graph.add_edge("reclassify_validate", "reclassify_repair")
    graph.add_edge("reclassify_repair", "finalise_classification")

    return graph.compile(checkpointer=False)


document_graph = None  # built lazily — see get_document_graph()


def get_document_graph():
    global document_graph
    if document_graph is None:
        document_graph = build_document_graph()
    return document_graph
