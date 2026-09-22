"""
Typed graph state (Phase 1 — skeleton).

Deliberately small and PII-free (see LANGGRAPH_MIGRATION_PLAN.md section 3
and 5): only ids, a storage reference, capped tool results and summaries.
Never the document's raw text or its extracted fields — those stay in
Postgres (extracted_fields table / documents.extracted_fields) or, for
raw_text, in the run-scoped cache in `text_cache.py`. This is what actually
gets checkpointed once persistence lands (phase 4), so keeping it small and
free of PII is what keeps a Supabase-free-tier checkpoint table small and
safe to log.
"""

from __future__ import annotations

from typing import Annotated, Any, Optional, TypedDict

from langgraph.graph.message import add_messages


class ClassificationResult(TypedDict, total=False):
    document_type: Optional[str]
    confidence: float
    evidence: list[str]
    reasoning: str
    needs_manual_review: bool
    fulfilled_requirements: list[str]


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
    document_id: str
    case_id: Optional[str]
    filename: str
    storage_path: str

    # Set once by `ingest`; used to detect drift on a re-ingest instead of
    # storing the text itself (see "Settled" section of the migration plan).
    text_hash: Optional[str]
    text_available: bool

    # Classify subgraph. rule_result/llm_result written by independent
    # parallel nodes (no shared key, no reducer needed); route/agent state
    # only populated when the fast path is skipped.
    rule_result: Optional[dict]
    llm_result: Optional[dict]
    route: Optional[str]  # "fast" | "agent"
    agent_messages: Annotated[list[Any], add_messages]

    classification: Optional[ClassificationResult]
    extraction: Optional[ExtractionSummary]

    # Bounded classify<->extract reclassification loop — mirrors
    # orchestrator.MAX_RECLASSIFICATION_ATTEMPTS.
    attempts: int

    status: str
    failure_reason: Optional[str]
