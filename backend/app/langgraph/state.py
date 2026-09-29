"""
Typed graph state (Phase 1 — skeleton).

Deliberately small and PII-free (see LANGGRAPH_MIGRATION_PLAN.md section 3
and 5): only ids, a storage reference, capped tool results and summaries.
Never the document's raw text or its extracted fields — those stay in
Postgres (the extracted_fields table) or, for raw_text, in the run-scoped
cache in `text_cache.py`. This is what actually
gets checkpointed once persistence lands (phase 4), so keeping it small and
free of PII is what keeps a Supabase-free-tier checkpoint table small and
safe to log.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, Required, TypedDict

from langgraph.graph.message import add_messages


class ClassificationResult(TypedDict, total=False):
    # All six are always set together — see classify_support.validate_agent_output
    # and classify_graph.py's repair fallback, the only two places this dict is
    # built — so Required here reflects a real invariant, not a hopeful cast.
    document_type: Required[Optional[str]]
    confidence: Required[float]
    evidence: Required[list[str]]
    reasoning: Required[str]
    needs_manual_review: Required[bool]
    fulfilled_requirements: Required[list[str]]


class ExtractionSummary(TypedDict, total=False):
    """
    Extraction's *summary*, not its payload — the actual extracted_fields
    dict is persisted straight to Postgres by the persist node and never
    copied into graph state (see module docstring).
    """

    confidence: float
    field_count: int
    escalation: Optional[dict]


class DocumentGraphState(TypedDict, total=False):
    # Identity / reference — never bytes, never raw text (see files/parameter
    # binding in the migration plan). Nodes and tools fetch by these.
    # document_id is Required: pipeline.process_document's initial ainvoke()
    # always supplies it (see graph.py) — every node runs after that.
    document_id: Required[str]
    case_id: Optional[str]
    filename: str
    storage_path: str

    # Set once by `ingest`; used to detect drift on a re-ingest instead of
    # storing the text itself (see "Settled" section of the migration plan).
    text_hash: Optional[str]
    text_available: bool

    # Classify subgraph. rule_result is the only deterministically-computed
    # input — llm_classify_tool/vision_llm results live only inside
    # agent_messages (as ToolMessages), since the agent decides itself
    # whether to call either, not a graph node.
    rule_result: Optional[dict]
    agent_messages: Annotated[list[Any], add_messages]

    classification: Optional[ClassificationResult]
    extraction: Optional[ExtractionSummary]

    # Bounded classify<->extract reclassification loop — mirrors
    # settings.MAX_RECLASSIFICATION_ATTEMPTS.
    attempts: int

    status: str
    failure_reason: Optional[str]
