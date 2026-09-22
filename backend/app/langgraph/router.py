"""
Deterministic fast-path router — the actual saving this migration is about
(see LANGGRAPH_MIGRATION_PLAN.md section 2: "the agent (2.8M tokens) runs
even when rule and LLM already agree strongly"). This mirrors, as a
negation, Agent 3's own escalation rules a-d (see classify_support's ported
prompt): if none of those rules would have told the agent to call
vision_llm, there is nothing for the agent to add, so skip the Sonnet call
entirely and finalise straight from rule_result + llm_result.
"""

from __future__ import annotations

from .classify_support import address_check, has_usable_text
from ..config import settings

FAST = "fast"
AGENT = "agent"

_NO_MATCH_THRESHOLD = 0.3
_CLOSE_MARGIN = 0.15
_ADDRESS_SENSITIVE_TYPES = {"nric", "bank_statement"}


def _rank1(result: dict | None):
    candidates = (result or {}).get("top_candidates") or []
    return candidates[0] if candidates else None


def _is_no_match_or_absent(result: dict | None) -> bool:
    top = _rank1(result)
    return top is None or top.get("match_strength", 0.0) < _NO_MATCH_THRESHOLD


def _is_close(result: dict | None) -> bool:
    candidates = (result or {}).get("top_candidates") or []
    if len(candidates) < 2:
        return False
    return abs(candidates[0].get("confidence", 0.0) - candidates[1].get("confidence", 0.0)) < _CLOSE_MARGIN


def decide_route(rule_result: dict | None, llm_result: dict | None, extracted_text) -> str:
    rule_top = _rank1(rule_result)
    llm_top = _rank1(llm_result)

    # rule (a): disagree on rank-1 (including one side having no rank-1 at all)
    if rule_top is None or llm_top is None or rule_top["document_type"] != llm_top["document_type"]:
        return AGENT

    # rule (b): either source is no-match / absent / close
    if _is_no_match_or_absent(rule_result) or _is_no_match_or_absent(llm_result):
        return AGENT
    if _is_close(rule_result) or _is_close(llm_result):
        return AGENT

    agreed_type = rule_top["document_type"]
    agreed_strength = min(rule_top["match_strength"], llm_top["match_strength"])

    # rule (d): agreed match_strength below the threshold
    if agreed_strength < settings.LANGGRAPH_ROUTER_AGREEMENT_THRESHOLD:
        return AGENT

    # rule (c): nric/bank_statement with an unresolved address check
    text_available = has_usable_text(extracted_text)
    if agreed_type in _ADDRESS_SENSITIVE_TYPES and address_check(rule_result, text_available) == "unknown":
        return AGENT

    return FAST
