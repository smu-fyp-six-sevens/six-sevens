"""
Shapes of a per-case analysis run: cross-document findings, the risk
outlook, and the stored result. See CROSS_DOCUMENT_AND_RISK_PLAN.md.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..models import AdditionalDocuments, CaseRisk, SupportingDocumentRecommendation


class FindingCategory(str, Enum):
    TAMPERING_FORENSIC = "tampering_forensic"
    REGISTRY_DISCREPANCY = "registry_discrepancy"
    IDENTITY_ENTITY_CONFLICT = "identity_entity_conflict"
    CROSS_DOCUMENT_VARIANCE = "cross_document_variance"
    BEHAVIOURAL_ANOMALY = "behavioural_anomaly"
    UNSUPPORTED_CLAIM = "unsupported_claim"


# More evidence can answer these; a further document can't un-forge or re-identify.
REDUCIBLE_CATEGORIES = frozenset({
    FindingCategory.CROSS_DOCUMENT_VARIANCE,
    FindingCategory.BEHAVIOURAL_ANOMALY,
    FindingCategory.UNSUPPORTED_CLAIM,
})
# The only categories the model may choose between; the rest come from code.
MODEL_CHOOSABLE_CATEGORIES = frozenset({FindingCategory.BEHAVIOURAL_ANOMALY, FindingCategory.UNSUPPORTED_CLAIM})

Severity = Literal["low", "medium", "high"]


class PlannedCheck(BaseModel):
    """One evidence-tool call the hypothesis is to be tested with."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    refutation_probe: bool = False


class Hypothesis(BaseModel):
    id: str
    claim: str
    # Document keys (e.g. "bank_statement_1") the claim rests on.
    documents: list[str] = Field(default_factory=list)
    proof: str = ""
    # Fixed before any tool runs: where and how the claim would be shown wrong.
    refutation_document: str | None = None
    refutation_test: str = ""
    checks: list[PlannedCheck] = Field(default_factory=list)


class CrossDocFinding(BaseModel):
    id: str
    hypothesis_id: str
    summary: str
    rationale: str
    severity: Severity
    category: FindingCategory
    citations: list[str] = Field(default_factory=list)
    document_ids: list[str] = Field(default_factory=list)
    # What the refutation probe returned, in the investigator's words.
    probe_summary: str = ""
    # Set by code when a tool result carried a hard breach.
    breach_flags: list[str] = Field(default_factory=list)


class EvidenceRequest(BaseModel):
    title: str
    what_it_shows: str
    addresses: list[str] = Field(default_factory=list)
    expected_effect: Literal["full", "partial"] = "full"
    # "agent" for the case-level recommendation, "rule" for supporting_documents.py.
    source: Literal["agent", "rule"] = "agent"


class OutlookRequest(EvidenceRequest):
    score_if_supports: int | None = None
    level_if_supports: str | None = None
    # An unhelpful document leaves the score where it is.
    score_if_contradicts: int | None = None


class RiskOutlook(BaseModel):
    current_score: int | None = None
    current_level: str | None = None
    requests: list[OutlookRequest] = Field(default_factory=list)
    best_case_score: int | None = None
    best_case_level: str | None = None
    # Findings no further evidence can reduce, so the best case never goes below them.
    unreducible_finding_ids: list[str] = Field(default_factory=list)


class AnalysisAudit(BaseModel):
    """What produced a result, so a past run can be reconstructed."""

    models: dict[str, str] = Field(default_factory=dict)
    prompt_version: str = ""
    hypotheses_tried: int = 0
    findings_discarded: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


AnalysisStatus = Literal["running", "complete", "failed", "superseded"]


class CaseAnalysis(BaseModel):
    run_id: str
    case_id: str
    status: AnalysisStatus
    fingerprint: str
    started_at: datetime
    finished_at: datetime | None = None
    error: str | None = None
    findings: list[CrossDocFinding] = Field(default_factory=list)
    outlook: RiskOutlook | None = None
    summary: str | None = None
    # The case's risk and evidence requests: produced only here, then read by the case views.
    risk: CaseRisk | None = None
    supporting_documents: list[SupportingDocumentRecommendation] = Field(default_factory=list)
    additional_documents: AdditionalDocuments | None = None
    # Comparisons that ran and found the documents agree, so an empty result is not read as "not checked".
    consistent_checks: list[str] = Field(default_factory=list)
    audit: AnalysisAudit = Field(default_factory=AnalysisAudit)
    # Step-by-step log of the run, for debugging. Includes hypotheses that were tried and dropped.
    trace: list[dict[str, Any]] = Field(default_factory=list)
    # Computed on read: the case's documents changed since this run started.
    stale: bool = False


class CaseAnalysisRunItem(BaseModel):
    """One run in a case's list of analyses (GET /cases/{id}/analysis/runs): enough to pick one to open."""

    run_id: str
    status: AnalysisStatus
    started_at: datetime
    finished_at: datetime | None = None
    # The officer who asked for it (the case.analysis.requested event), when the audit trail has it.
    started_by: str | None = None
    risk_level: str | None = None
    risk_score: int | None = None
    # Against the next older run with a score; None for the oldest scored run or a run without a score.
    score_change: int | None = None
    # Cross-document findings, as the card and the audit trail count them.
    finding_count: int = 0
    # The case's documents changed since this run started.
    stale: bool = False
