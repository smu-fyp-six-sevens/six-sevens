"""Read-only views of the actual prompt templates used by the configured agent roles."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from .case_pipeline import adverse_media
from .case_pipeline.cross_document import agents as case_agents
from .case_pipeline.risk import outlook, summary
from .langgraph import classify_support, extraction_prompts
from .langgraph.detection import graph as detection_graph
from .langgraph.detection import specialists
from .langgraph.extraction_prompts import build_extraction_prompt
from .guardrails.envelope import DOCUMENT_DATA_RULE


class PromptSection(BaseModel):
    title: str
    text: str

    # Some templates carry separator newlines because they are appended to another prompt at
    # runtime (e.g. classify_support._INPUTS_BLOCK). Shown on their own, those render as blank lines.
    @field_validator("text")
    @classmethod
    def _trim_blank_lines(cls, text: str) -> str:
        return text.strip("\n")


class _KeepPlaceholders(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _as_sent(template: str) -> str:
    """A str.format template as the model receives it: escaped braces ({{ }}) collapse to single
    ones, while runtime placeholders like {catalogue} stay visible for the reader."""
    return template.format_map(_KeepPlaceholders())


class AgentPrompt(BaseModel):
    agent_id: str
    document_type: str | None = None
    note: str
    sections: list[PromptSection]


def get_agent_prompt(agent_id: str, document_type: str | None = None) -> AgentPrompt:
    """Return source prompt text with runtime-supplied document facts clearly represented as placeholders."""
    if agent_id == "document_classifier":
        return AgentPrompt(
            agent_id=agent_id,
            note="The static system prompt is shown below. Each run appends the rule-classifier result and document text availability; images may also be passed to the vision tool.",
            sections=[
                PromptSection(title="System prompt", text=classify_support.AGENT_STATIC_PROMPT),
                PromptSection(title="Per-document input template", text=_as_sent(classify_support._INPUTS_BLOCK)),
            ],
        )

    if agent_id == "field_extractor":
        selected_type = document_type or "bank_statement"
        if selected_type not in extraction_prompts.KNOWN_DOCUMENT_TYPES:
            raise ValueError(f"Unknown document type: {selected_type}")
        template = build_extraction_prompt(selected_type, "[DOCUMENT TEXT INSERTED AT RUNTIME]")
        return AgentPrompt(
            agent_id=agent_id, document_type=selected_type,
            note="Extraction instructions vary by document type. The sample shown uses the selected type; runtime document text is omitted. Some runs also append a classifier correction hint.",
            sections=[
                PromptSection(title=f"Extraction prompt · {selected_type}", text=template),
                PromptSection(title="Document data handling rule", text=DOCUMENT_DATA_RULE),
            ],
        )

    if agent_id == "detection_lead":
        return AgentPrompt(
            agent_id=agent_id,
            note="The lead makes a planning call and a review call. Runtime document facts and specialist reports are supplied separately.",
            sections=[
                PromptSection(title="Planning prompt", text=detection_graph._PLAN_SYSTEM_PROMPT),
                PromptSection(title="Review prompt", text=detection_graph._REVIEW_SYSTEM_PROMPT),
            ],
        )

    if agent_id in specialists.SPECIALISTS:
        spec = specialists.SPECIALISTS[agent_id]
        dynamic = specialists._SPECIALIST_DYNAMIC_PROMPT
        sections = [
            PromptSection(
                title="Specialist system prompt",
                text=specialists._SPECIALIST_STATIC_PROMPT.format(
                    title=spec.title, expertise=spec.expertise, rules=spec.rules,
                ),
            ),
            PromptSection(title="Per-document context template", text=_as_sent(dynamic)),
        ]
        if spec.uses_field_catalogue:
            sections.append(PromptSection(
                title="Field and check-menu instructions", text=_as_sent(specialists._FIELD_CATALOGUE_PROMPT),
            ))
        return AgentPrompt(
            agent_id=agent_id,
            note="The document date, type, file facts, lead brief and available field/check menus are inserted at runtime. Required pre-run tool results may also be included.",
            sections=sections,
        )

    if agent_id in {"hypothesis_planner", "hypothesis_judge"}:
        is_planner = agent_id == "hypothesis_planner"
        instruction = (
            case_agents._PLAN_INSTRUCTIONS.format(
                limit=case_agents.MAX_HYPOTHESES,
                max_checks=case_agents.MAX_CHECKS_PER_HYPOTHESIS,
                dossier="[CASE DOSSIER AND AVAILABLE FINDINGS INSERTED AT RUNTIME]",
                overview="[CASE OVERVIEW COMPUTED BY CODE INSERTED AT RUNTIME]",
                candidates="[CANDIDATE HYPOTHESES COMPUTED BY CODE INSERTED AT RUNTIME]",
            )
            if is_planner else case_agents._JUDGE_INSTRUCTIONS.format(
                hypotheses="[HYPOTHESES AND CHECK RESULTS INSERTED AT RUNTIME]",
            )
        )
        return AgentPrompt(
            agent_id=agent_id,
            note="The planner and judge share a system prompt. The tool catalogue and case-specific evidence are inserted for each run.",
            sections=[
                PromptSection(
                    title="Shared system prompt",
                    text=case_agents._SYSTEM.format(tools="[AVAILABLE CROSS-DOCUMENT TOOLS INSERTED AT RUNTIME]"),
                ),
                PromptSection(title="Planner instructions" if is_planner else "Judge instructions", text=instruction),
            ],
        )

    # The case analysis's single calls: one template each, its runtime values shown as placeholders.
    single_calls = {
        "evidence_requests": (outlook._PROMPT, "The scored findings, the case's documents and the open questions are inserted for each run."),
        "case_summary": (summary._PROMPT, "The level, findings, clean and unrun checks and the evidence requests are inserted for each run."),
        "adverse_media": (adverse_media._PROMPT, "The company's name, former names and the search window are inserted for each run."),
    }
    if agent_id in single_calls:
        template, note = single_calls[agent_id]
        return AgentPrompt(agent_id=agent_id, note=note, sections=[PromptSection(title="Prompt", text=_as_sent(template))])

    raise KeyError(agent_id)
