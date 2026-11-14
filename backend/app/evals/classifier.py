"""The classifier agent: did it read the document before deciding, in the allowed order, and does its type stand up."""

from __future__ import annotations

from typing import Any

from .base import Check, judged, last, names, of

AGENT = "classifier"
_TEXT_TOOL = "llm_classify_tool"
_VISION_TOOL = "vision_llm"
# Its own confidence this far above the one computed from its sources is overconfidence.
OVERCONFIDENCE_GAP = 0.3


def _tools_run(steps: list[dict[str, Any]]) -> set[str]:
    return {
        s["name"][len("tool."):] for s in steps
        if s.get("step") == "span" and str(s.get("name")).startswith("tool.") and not s.get("error")
        and s["name"][len("tool."):] in (_TEXT_TOOL, _VISION_TOOL)
    }


def _reconsidered(steps: list[dict[str, Any]]) -> bool | None:
    """After extraction objected: did it change the type, read again, or ask for review? None when it never had to."""
    marks = [i for i, s in enumerate(steps) if s.get("step") == "reclassify"]
    if not marks:
        return None
    before = [s for s in steps[:marks[-1]] if s.get("step") == "classification_finalised"]
    after = steps[marks[-1] + 1:]
    final = next((s for s in reversed(after) if s.get("step") == "classification_finalised"), None)
    if final is None:
        return None
    changed = bool(before) and before[-1].get("document_type") != final.get("document_type")
    read_again = any(s.get("step") == "agent_call" and s.get("tool_calls") for s in after)
    return changed or read_again or bool(final.get("needs_manual_review"))


def checks(steps: list[dict[str, Any]]) -> list[Check]:
    rule = last(steps, "rule_classify")
    final = last(steps, "classification_finalised")
    if rule is None and not of(steps, "agent_call"):
        return []  # the officer set the type, so the classifier didn't run
    ingest = last(steps, "ingest") or {}
    certain = (rule or {}).get("certain_type")
    ran = _tools_run(steps)
    refused = [t for s in of(steps, "classify_tool_refused") for t in s.get("tools") or []]
    confidence = last(steps, "confidence") or {}
    claimed, computed = confidence.get("agent_confidence"), confidence.get("computed_confidence")
    no_text = ingest.get("text_available") is False
    return [
        judged(AGENT, "read_before_deciding", "trajectory", None if certain or no_text else bool(ran),
               "Decided the type without reading the text or the pages"),
        judged(AGENT, "looked_when_no_text", "trajectory", _VISION_TOOL in ran if no_text else None,
               "The document had no readable text, and it never looked at the pages", broken="fail"),
        judged(AGENT, "vision_only_when_needed", "trajectory",
               None if _VISION_TOOL not in ran else not certain,
               "Looked at the page images though a rule had already fixed the type"),
        judged(AGENT, "tool_order", "trajectory", not refused,
               f"Tried {names(refused)} out of order (the guard refused it)"),
        judged(AGENT, "agrees_with_certain_rule", "output",
               None if not certain or final is None else final.get("document_type") == certain,
               f"Chose {(final or {}).get('document_type')} though a rule fixed the type as {certain}", broken="fail"),
        judged(AGENT, "not_overconfident", "output",
               None if not isinstance(claimed, (int, float)) or not isinstance(computed, (int, float))
               else claimed - computed <= OVERCONFIDENCE_GAP,
               f"Claimed confidence {claimed}, but its sources support only {computed}"),
        judged(AGENT, "answers_objection", "trajectory", _reconsidered(steps),
               "Extraction objected, and it kept the type without reading again or asking for review"),
    ]
