"""The shape of each reply a model is asked for, and of each result a detection tool returns."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field
from typing_extensions import Annotated

from .llm_io import DictOrNone, Escalation, Probability, Text, TextList, TextMap, keep_valid


class ClassifierReply(BaseModel):
    """The classify agent's closing answer. A field that can't be read falls back to the safe value."""

    document_type: Text = ""
    confidence: Probability = 0.0
    evidence: TextList = []
    reasoning: Text = ""
    needs_manual_review: bool = True
    fulfilled_requirements: TextList = []


class ExtractionReply(BaseModel):
    """
    The fields, and the model's objection to the document's type if it has one. A reply with neither is not an
    extraction, even if it is valid JSON: see `parse_extraction_output`. An objection may come with no fields.
    """

    extracted_fields: DictOrNone = None
    escalation: Escalation = None


class LeadPlan(BaseModel):
    briefs: TextMap = {}
    confidence: Probability = 0.0


class LeadReview(BaseModel):
    summary: Text = ""
    second_look: DictOrNone = None
    confidence: Probability = 0.0


class AssessmentItem(BaseModel):
    finding_id: Text = ""
    severity: Text = ""
    dismiss: bool = False
    rationale: Text = ""
    confidence: Probability = 0.0


class SpecialistAssessment(BaseModel):
    summary: Text = ""
    confidence: Probability = 0.0
    uncertain: bool = False
    uncertainty_reason: Text = ""
    assessments: Annotated[list[AssessmentItem], BeforeValidator(keep_valid(AssessmentItem))] = []


class EvidenceRequestItem(BaseModel):
    title: Text
    what_it_shows: Text = ""
    addresses: TextList = []
    expected_effect: Text = "full"


class EvidenceRequests(BaseModel):
    requests: Annotated[list[EvidenceRequestItem], BeforeValidator(keep_valid(EvidenceRequestItem))] = []


class SummaryReasonReply(BaseModel):
    text: Text = ""
    finding_id: Text = ""


class SummaryReply(BaseModel):
    headline: Text = ""
    reasons: Annotated[list[SummaryReasonReply], BeforeValidator(keep_valid(SummaryReasonReply))] = []
    would_help: Text = ""


def _placeholder_summary(value: Any) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return text or "A finding was reported without a description."


class ToolFinding(BaseModel):
    """What the rest of detection reads from a finding. The tool's own fields ride along."""

    model_config = ConfigDict(extra="allow")

    id: Text = ""
    summary: Annotated[str, BeforeValidator(_placeholder_summary)] = Field("", validate_default=True)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    tool: Text = ""
    unable_to_assess: bool = False
    nothing_to_check: bool = False
    reason: Text = ""
    findings: Annotated[list[ToolFinding], BeforeValidator(keep_valid(ToolFinding))] = []
    truncated: int = 0
    dropped_findings: int = 0  # set by the reader: findings in the raw result that did not validate
