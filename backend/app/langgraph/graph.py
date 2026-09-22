"""
Document graph (Phases 1-3): ingest -> classify -> finalise_classification ->
extract -> ANALYZED/FAILED.

No checkpointer yet (Phase 4) and no reclassify loop yet (also Phase 4) —
see LANGGRAPH_MIGRATION_PLAN.md section 6. This is intentionally the
smallest graph that reaches an equivalent terminal outcome to
orchestrator.process_document for a document that classifies cleanly on the
first pass, so Phase 4 can be judged against a working baseline.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from . import nodes
from .classify_graph import get_classify_graph
from .state import DocumentGraphState


def _route_after_ingest(state: DocumentGraphState) -> str:
    return "fail" if state.get("status") == "failed" else "classify"


def _route_after_check_text(state: DocumentGraphState) -> str:
    return "fail" if state.get("status") == "failed" else "extract"


def _route_after_extract(state: DocumentGraphState) -> str:
    return "fail" if state.get("status") == "failed" else "mark_analyzed"


def build_document_graph():
    graph = StateGraph(DocumentGraphState)
    graph.add_node("ingest", nodes.ingest_node)
    graph.add_node("classify", get_classify_graph())
    graph.add_node("finalise_classification", nodes.finalise_classification_node)
    graph.add_node("check_text_available", nodes.check_text_available)
    graph.add_node("extract", nodes.extract_node)
    graph.add_node("mark_analyzed", nodes.mark_analyzed_node)
    graph.add_node("fail", nodes.fail_node)

    graph.set_entry_point("ingest")
    graph.add_conditional_edges("ingest", _route_after_ingest, {"classify": "classify", "fail": "fail"})
    graph.add_edge("classify", "finalise_classification")
    graph.add_edge("finalise_classification", "check_text_available")
    graph.add_conditional_edges(
        "check_text_available", _route_after_check_text, {"extract": "extract", "fail": "fail"}
    )
    graph.add_conditional_edges(
        "extract", _route_after_extract, {"mark_analyzed": "mark_analyzed", "fail": "fail"}
    )
    graph.add_edge("mark_analyzed", END)
    graph.add_edge("fail", END)

    return graph.compile(checkpointer=False)


document_graph = None  # built lazily — see get_document_graph()


def get_document_graph():
    global document_graph
    if document_graph is None:
        document_graph = build_document_graph()
    return document_graph
