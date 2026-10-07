"""
Deterministic classification confidence. The classify agent still decides
the document_type (and which tools to consult), but the confidence recorded
against that type is computed here from the sources it actually consulted,
instead of being a number the agent writes down itself.

The rules are the ones the agent's prompt already describes (see
classify_support's "Deciding the type + confidence"), moved into code so the
same inputs always produce the same confidence:

  - Sources: rule_result (always), plus llm_classify_tool / vision_llm only
    if the agent called them. A source's "pick" is its rank-1 candidate; a
    source with no candidates (or an unparseable/errored tool result) is
    absent. A rule_result `certain_type` overrides the rule's own pick at
    CERTAIN_STRENGTH.
  - No usable source, or an unrecognised type -> 0.0.
  - Every consulted source agrees with the chosen type -> average of their
    match_strength, capped at CERTAIN_CAP if any of them is a certain match,
    else AGREEMENT_CAP. With only one source (and not certain), capped at
    UNCORROBORATED_CAP instead.
  - Any consulted source picked a different type, or the lone source's
    runner-up is within CLOSE_MARGIN of its pick -> capped at
    DISAGREEMENT_CAP and flagged for manual review.

These inputs (the tools' match_strength values, which tools the agent chose
to call, the type it chose) still come from LLM calls, so two runs on the
same document can still differ — what's deterministic is how they combine.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import ToolMessage

CERTAIN_STRENGTH = 0.97
CERTAIN_CAP = 0.97
AGREEMENT_CAP = 0.95
UNCORROBORATED_CAP = 0.85
DISAGREEMENT_CAP = 0.6
CLOSE_MARGIN = 0.1

_TOOL_SOURCES = {"llm_classify_tool": "llm", "vision_llm": "vision"}


def _candidates(result: Any) -> list[dict]:
    if not isinstance(result, dict):
        return []
    return [c for c in result.get("top_candidates") or [] if isinstance(c, dict) and c.get("document_type")]


def _strength(candidate: dict) -> float:
    try:
        return max(0.0, min(1.0, float(candidate.get("match_strength", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def tool_results(messages: list) -> dict[str, dict]:
    """
    {"llm": result, "vision": result} for every classify tool the agent
    actually called, read back from its ToolMessages. The latest call wins
    (a reclassify pass continues the same conversation). A tool error or
    unparseable content is left out — the same "treat as absent" rule the
    agent's prompt gives it.
    """
    results: dict[str, dict] = {}
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        source = _TOOL_SOURCES.get(message.name or "")
        if source is None or getattr(message, "status", "success") == "error":
            continue
        content = message.content
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except ValueError:
                continue
        if isinstance(content, dict):
            results[source] = content
    return results


def compute_classification_confidence(
    document_type: str | None,
    rule_result: dict | None,
    tools: dict[str, dict],
) -> dict:
    """
    Returns {"confidence", "needs_manual_review", "basis", "sources"} for
    the agent's chosen document_type. Never raises.
    """
    # source -> (picked type, strength, certain, runner-up strength or None)
    picks: dict[str, tuple[str, float, bool, float | None]] = {}

    rule_candidates = _candidates(rule_result)
    certain_type = (rule_result or {}).get("certain_type")
    if certain_type:
        picks["rule"] = (certain_type, CERTAIN_STRENGTH, True, None)
    elif rule_candidates:
        runner_up = _strength(rule_candidates[1]) if len(rule_candidates) > 1 else None
        picks["rule"] = (rule_candidates[0]["document_type"], _strength(rule_candidates[0]), False, runner_up)

    for source, tool_result in tools.items():
        candidates = _candidates(tool_result)
        if candidates:
            runner_up = _strength(candidates[1]) if len(candidates) > 1 else None
            picks[source] = (candidates[0]["document_type"], _strength(candidates[0]), False, runner_up)

    sources = {
        name: {"document_type": pick[0], "match_strength": round(pick[1], 4), "certain": pick[2]}
        for name, pick in picks.items()
    }

    def result(confidence: float, needs_review: bool, basis: str) -> dict:
        return {
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "needs_manual_review": needs_review,
            "basis": basis,
            "sources": sources,
        }

    if not document_type or document_type == "unrecognised":
        return result(0.0, True, "unrecognised")
    if not picks:
        return result(0.0, True, "no_usable_source")

    agreeing = [pick for pick in picks.values() if pick[0] == document_type]
    support = sum(pick[1] for pick in agreeing) / len(agreeing) if agreeing else 0.0

    if len(agreeing) < len(picks):
        return result(min(support, DISAGREEMENT_CAP), True, "sources_disagree")

    if len(picks) == 1:
        _, strength, certain, runner_up = agreeing[0]
        if certain:
            return result(min(strength, CERTAIN_CAP), False, "certain_match")
        if runner_up is not None and strength - runner_up <= CLOSE_MARGIN:
            return result(min(strength, DISAGREEMENT_CAP), True, "uncorroborated_close")
        return result(min(strength, UNCORROBORATED_CAP), False, "uncorroborated")

    cap = CERTAIN_CAP if any(pick[2] for pick in agreeing) else AGREEMENT_CAP
    return result(min(support, cap), False, "sources_agree")
