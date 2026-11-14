"""Read-only inventory of the agent roles and controls currently configured in this service.

This is intentionally explicit: entries marked unversioned or unassigned reflect real gaps in
the current system. It is not the source of runtime permissions or a control plane.
"""

from __future__ import annotations

from pydantic import BaseModel

from .config import settings
from .case_pipeline.cross_document.agents import MAX_MODEL_CALLS_PER_RUN, PROMPT_VERSION
from .langgraph.extraction_prompts import KNOWN_DOCUMENT_TYPES


class AgentRecord(BaseModel):
    id: str
    name: str
    group: str
    purpose: str
    limitations: str
    model: str
    prompt_version: str | None
    tools: list[str]
    access_summary: list[str]
    call_budget: str
    owner: str | None = None
    prompt_options: list[str] = []
    # A hash of this agent's prompts, model, tools and budget (agent_versions.py); set by the /agents route.
    fingerprint: str | None = None


def list_agents() -> list[AgentRecord]:
    """Describe known roles using the model defaults and limits enforced by the current pipelines."""
    document_budget = f"Shared document run cap: {settings.DOCUMENT_MAX_MODEL_CALLS} calls"
    detection_budget = f"Shared detection run cap: {settings.DETECTION_MAX_MODEL_CALLS} calls"
    case_budget = f"{MAX_MODEL_CALLS_PER_RUN} calls across case-analysis agents per run"
    return [
        AgentRecord(
            id="document_classifier", name="Document classifier", group="Document processing",
            purpose="Identifies the uploaded document type.",
            limitations="OCR or unusual layouts can lead to a wrong type; uncertain cases can require officer review.",
            model=settings.LANGGRAPH_SONNET_MODEL,
            prompt_version=None, tools=["llm_classify_tool", "vision_llm"],
            access_summary=["Read document text", "Inspect page images when needed", "Select a document type"],
            call_budget=document_budget,
        ),
        AgentRecord(
            id="field_extractor", name="Field extractor", group="Document processing",
            purpose="Extracts document details into the fields for its document type.",
            limitations="OCR and ambiguous source text can produce missing or incorrect values; extracted fields need review.",
            model=settings.LANGGRAPH_SONNET_MODEL, prompt_version=None, tools=[],
            access_summary=["Read document text and page images", "Return schema-constrained extracted fields"],
            call_budget=document_budget, prompt_options=KNOWN_DOCUMENT_TYPES,
        ),
        AgentRecord(
            id="detection_lead", name="Detection lead", group="Fraud detection",
            purpose="Plans and reviews specialist checks for one document.",
            limitations="Its assessment depends on the checks that apply and complete; unavailable checks remain unassessed.",
            model=settings.LANGGRAPH_SONNET_MODEL, prompt_version=None,
            tools=["Specialist agents", "Detection review tools"],
            access_summary=["Read one document and its extracted fields", "Request relevant specialist checks"],
            call_budget=detection_budget,
        ),
        AgentRecord(
            id="layout_rendering", name="Layout & Rendering Analyst", group="Fraud detection specialists",
            purpose="Checks visual layout, typography, hidden text and image integrity.",
            limitations="Visual heuristics can raise false alarms and cannot establish authenticity on their own.",
            model=settings.LANGGRAPH_HAIKU_MODEL, prompt_version=None,
            tools=["hidden_text_tool", "font_consistency_tool", "layout_anomalies_tool",
                   "image_manipulation_tool", "image_tampering_tool"],
            access_summary=["Read one document and its extracted fields", "Report or dismiss assigned check results"],
            call_budget=detection_budget,
        ),
        AgentRecord(
            id="content_validation", name="Content Validation Analyst", group="Fraud detection specialists",
            purpose="Checks dates, figures, spelling, page consistency and relevant public records.",
            limitations="Some checks depend on OCR quality, available reference data or external services.",
            model=settings.LANGGRAPH_HAIKU_MODEL, prompt_version=None,
            tools=["spelling_tool", "date_consistency_tool", "page_consistency_tool",
                   "numerical_checks_tool", "external_validation_tool"],
            access_summary=["Read one document and its extracted fields", "Use assigned validation checks"],
            call_budget=detection_budget,
        ),
        AgentRecord(
            id="digital_authenticity", name="Digital Authenticity Analyst", group="Fraud detection specialists",
            purpose="Checks file metadata and signs of AI-generated or altered imagery.",
            limitations="Metadata and image checks may be unavailable for some files; their absence is not proof of authenticity.",
            model=settings.LANGGRAPH_HAIKU_MODEL, prompt_version=None,
            tools=["prompt_injection_tool", "metadata_tool", "ai_generation_tool"],
            access_summary=["Read one document and file metadata", "Report or dismiss assigned check results"],
            call_budget=detection_budget,
        ),
        AgentRecord(
            id="hypothesis_planner", name="Hypothesis planner", group="Case analysis",
            purpose="Proposes cross-document inconsistencies that can be tested.",
            limitations="A proposed hypothesis is only a lead; it is not a finding until evidence checks support it.",
            model=settings.LANGGRAPH_SONNET_MODEL, prompt_version=PROMPT_VERSION,
            tools=["Cross-document evidence tools"],
            access_summary=["Read approved case facts", "Propose testable hypotheses"],
            call_budget=case_budget,
        ),
        AgentRecord(
            id="hypothesis_judge", name="Hypothesis judge", group="Case analysis",
            purpose="Assesses evidence and counter-evidence for proposed hypotheses.",
            limitations="May return inconclusive when evidence is insufficient; the output still needs human review.",
            model=settings.LANGGRAPH_SONNET_MODEL, prompt_version=PROMPT_VERSION,
            tools=["Cross-document evidence tools"],
            access_summary=["Read approved case facts and test results", "Return supported, refuted or inconclusive decisions"],
            call_budget=case_budget,
        ),
    ]
