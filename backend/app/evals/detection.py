"""The detection lead and its specialists: briefs, second looks, which checks each specialist ran, and its report."""

from __future__ import annotations

import json
from typing import Any

from .base import Check, judged, names, of

LEAD = "lead"
_INCOME_AND_BANK = frozenset({"payslip", "notice_of_assessment", "bank_statement"})
# Checks a specialist must run on these document types (the highest-risk ones, for the content check).
# Everything else is the specialist's judgement, so it isn't marked.
_EXPECTED: dict[str, dict[str, str]] = {
    "content_validation": {t: "numerical_checks_tool" for t in _INCOME_AND_BANK},
}
_ALWAYS_EXPECTED = {"digital_authenticity": "metadata_tool"}
# Run by the code before the specialist's first turn (specialists.Specialist.first_tool), so not its own choice.
_PRE_RUN = {"layout_rendering": "hidden_text_tool", "digital_authenticity": "prompt_injection_tool"}
_DEFAULT_DISMISSAL = "Dismissed as a false positive."


def _lead(steps: list[dict[str, Any]], lead_summary: str | None) -> list[Check]:
    plan = next((s for s in reversed(steps) if s.get("step") == "detection_plan"), None)
    fallback = (plan or {}).get("fallback_briefs")
    dropped = [s for s in of(steps, "detection_review")
               if s.get("second_look_requested") and not s.get("second_look_accepted") and not s.get("second_look_already_used")]
    return [
        judged(LEAD, "specific_briefs", "output", None if plan is None or fallback is None else not fallback,
               f"Gave the generic brief to: {names(fallback or [])}"),
        judged(LEAD, "valid_second_look", "trajectory", None if not of(steps, "detection_review") else not dropped,
               "Asked for a second look naming a finding or specialist that doesn't exist (the code dropped it)"),
        judged(LEAD, "wrote_summary", "output", bool((lead_summary or "").strip()),
               "Wrote no overall summary, so the canned one was used"),
    ]


def _specialist(step: dict[str, Any], document_type: str | None) -> list[Check]:
    name = str(step.get("specialist"))
    agent, span = f"specialist:{name}", step.get("in_span")
    called = [str(t) for t in step.get("tools_called") or []]
    expected = _EXPECTED.get(name, {}).get(document_type or "") or _ALWAYS_EXPECTED.get(name)
    if step.get("second_look"):
        expected = None  # a second look answers one question, not the whole review
    own = [t for t in called if t != _PRE_RUN.get(name)]
    calls = [json.dumps([c.get("name"), c.get("args")], sort_keys=True, default=str) for c in step.get("tool_calls") or []]
    repeated = sorted({json.loads(c)[0] for c in calls if calls.count(c) > 1})
    dismissed = step.get("dismissed") or []
    no_reason = [d.get("id") for d in dismissed if not str(d.get("reason") or "").strip() or d.get("reason") == _DEFAULT_DISMISSAL]
    return [
        judged(agent, "ran_expected_checks", "trajectory", None if expected is None else expected in called,
               f"Didn't run {expected}, expected on a {document_type or 'document'}", broken="fail", span=span),
        judged(agent, "chose_own_checks", "trajectory", None if name not in _PRE_RUN or step.get("second_look") else bool(own),
               "Ran only the check the code runs for it, nothing of its own choosing", span=span),
        judged(agent, "no_repeated_calls", "trajectory", not repeated,
               f"Called {names(repeated)} more than once with the same input", span=span),
        judged(agent, "within_tool_rounds", "trajectory", not step.get("wrapped_up"),
               "Ran out of tool rounds and was made to wrap up", span=span),
        judged(agent, "assessed_every_finding", "output", not step.get("unassessed"),
               f"Left {step.get('unassessed')} finding(s) its checks returned unassessed", span=span),
        judged(agent, "respected_floors", "output", not step.get("floor_overruled"),
               f"Tried to dismiss or downgrade {step.get('floor_overruled')} finding(s) a rule holds", span=span),
        judged(agent, "explained_dismissals", "output", not no_reason,
               f"Dismissed {len(no_reason)} finding(s) without its own reason", span=span),
    ]


def checks(steps: list[dict[str, Any]], *, document_type: str | None = None, lead_summary: str | None = None) -> list[Check]:
    if not of(steps, "detection_plan") and not of(steps, "detection_specialist"):
        return []
    results = _lead(steps, lead_summary)
    for step in of(steps, "detection_specialist"):
        results.extend(_specialist(step, document_type))
    return results
