"""The case analysis agents: the hypothesis planner, the judge, the summary writer, evidence requests, adverse media."""

from __future__ import annotations

import re
from typing import Any

from ..case_pipeline.cross_document.bias import has_protected_text
from .base import Check, judged, last, names, of, spans

PLANNER, JUDGE, SUMMARY, REQUESTS, MEDIA = (
    "hypothesis_planner", "hypothesis_judge", "summary_writer", "evidence_requests", "adverse_media",
)
# The model may never recommend an outcome (CROSS_DOCUMENT_AND_RISK_PLAN.md, Governance).
_OUTCOME = re.compile(
    r"\b(approv\w*|declin\w*|reject\w*)\b[^.]{0,30}\b(application|loan|applicant|case)\b"
    r"|\b(application|loan|applicant)\b[^.]{0,30}\b(approv\w*|declin\w*|reject\w*)\b",
    re.IGNORECASE,
)


def _planner(steps: list[dict[str, Any]]) -> list[Check]:
    plan = last(steps, "hypotheses")
    if plan is None:
        return []
    added = plan.get("probes_added_by_code")
    unknown = plan.get("unknown_tools")
    biased = plan.get("dropped_for_bias") or []
    return [
        judged(PLANNER, "answered_through_tool", "trajectory", plan.get("via") != "text",
               "Answered in free text instead of through its tool"),
        judged(PLANNER, "planned_refutation_probes", "output", None if added is None else not added,
               f"Planned no refutation probe for {names(added or [])}; the code added one"),
        judged(PLANNER, "used_real_tools", "trajectory", None if unknown is None else not unknown,
               f"Planned checks with tools that don't exist: {names(unknown or [])}"),
        judged(PLANNER, "no_protected_attributes", "output", not biased,
               f"Proposed {len(biased)} hypothesis(es) resting on a protected attribute (blocked)", broken="fail"),
    ]


def _judge(steps: list[dict[str, Any]]) -> list[Check]:
    reply = last(steps, "judge_reply")
    if reply is None:
        return []
    claimed = {s.get("hypothesis") for s in of(steps, "verdict") if (s.get("verdict") or {}).get("conclusive") is True}
    verified = of(steps, "verify")
    unproven = [s for s in verified if s.get("hypothesis") in claimed and not s.get("kept")]
    thin = [s.get("hypothesis") for s in verified
            if s.get("kept") and len(set((s.get("finding") or {}).get("document_ids") or [])) < 2]
    all_unproven = bool(claimed) and len(unproven) == len(claimed)
    return [
        judged(JUDGE, "answered_through_tool", "trajectory", reply.get("via") != "text",
               "Answered in free text instead of through its tool"),
        judged(JUDGE, "verdicts_stand_up", "output", None if not claimed else not unproven,
               f"Called {len(unproven)} of {len(claimed)} hypothesis(es) conclusive, but the code couldn't verify it: "
               + "; ".join(str(s.get("reason")) for s in unproven if s.get("reason")),
               broken="fail" if all_unproven else "warn"),
        judged(JUDGE, "findings_span_documents", "output", None if not verified else not thin,
               f"Kept a cross-document finding resting on one document: {names(thin)}", broken="fail"),
    ]


def _summary(steps: list[dict[str, Any]]) -> list[Check]:
    summary = last(steps, "summary")
    if summary is None or not spans(steps, "case_pipeline.summary"):
        return []  # no model call: the deterministic summary was used from the start
    model = summary.get("source") == "model"
    text = " ".join([str(summary.get("headline") or ""), str(summary.get("would_help") or ""),
                     *(str(r.get("text") or "") for r in summary.get("reasons") or [])])
    return [
        judged(SUMMARY, "kept_its_limits", "output", model, "Its summary broke a rule and was replaced by the deterministic one"),
        judged(SUMMARY, "no_outcome_advice", "output", not _OUTCOME.search(text) if model else None,
               "Recommends approving or declining the application", broken="fail"),
        judged(SUMMARY, "no_protected_attributes", "output", not has_protected_text(text) if model else None,
               "Mentions a protected attribute", broken="fail"),
    ]


def _requests(steps: list[dict[str, Any]]) -> list[Check]:
    proposed = [r for r in (last(steps, "evidence_requests") or {}).get("proposed") or [] if r.get("source", "agent") == "agent"]
    if not proposed:
        return []
    vague = [str(r.get("title")) for r in proposed if not str(r.get("what_it_shows") or "").strip()]
    rescored = (last(steps, "outlook") or {}).get("requests") or []
    unknown = [str(r.get("title")) for r in rescored if r.get("source") == "agent"
               and any(d.get("reason") == "no such finding" for d in r.get("dropped") or [])]
    return [
        judged(REQUESTS, "says_what_it_shows", "output", not vague, f"Requests that don't say what they would show: {names(vague)}"),
        judged(REQUESTS, "addresses_real_findings", "output", not unknown,
               f"Requests citing a finding that doesn't exist: {names(unknown)}"),
    ]


def _media(steps: list[dict[str, Any]]) -> list[Check]:
    step = last(steps, "adverse_media")
    if step is None or step.get("status") == "skipped" or step.get("reused"):
        return []
    status = step.get("status")
    problem = "The search failed, so the result isn't known" if status == "failed" else "Reported concerns with no source to back them"
    ok = status == "clean" or (status == "concerns" and (step.get("sources") or 0) > 0)
    return [judged(MEDIA, "result_backed", "output", ok, f"{problem}: {step.get('summary') or ''}".rstrip(": "))]


def checks(steps: list[dict[str, Any]]) -> list[Check]:
    return [*_planner(steps), *_judge(steps), *_summary(steps), *_requests(steps), *_media(steps)]
