"""
The `finding_feedback` table (backend/sql/schema.sql): what officers taught the agents.

Every verdict an officer saves on a finding, and every missed issue they report, is copied here with its
confidential details removed (feedback_scrub), unless its text touches a protected attribute (bias.py): that verdict
still stands on the finding, but teaches the agents nothing. A finished run replaces the findings and their verdicts,
but not this: feedback survives re-runs, and goes when the officer clears the verdict or the missed issue, or with its
case. The agents read it counted per check (`counts`), never as single verdicts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, cast

from pydantic import BaseModel

from .case_pipeline.cross_document.bias import has_protected_text
from .feedback_scrub import scrub
from .models import ReviewReason, ReviewScope, ReviewVerdict
from .supabase_client import service_supabase_client

TABLE = "finding_feedback"
# Making an agent less suspicious needs more evidence: a false alarm teaches once this many cases agree.
MIN_FALSE_ALARM_CASES = 2
# The example notes shown per lesson.
MAX_NOTES = 2

_COLUMNS = (
    "id, case_id, document_id, scope, kind, check_name, check_code, document_type, verdict, reason, summary, note, "
    "reviewer, created_at, updated_at"
)

REASON_LABELS: dict[str, str] = {
    "normal_for_document": "Normal for this kind of document",
    "within_tolerance": "Within an acceptable tolerance",
    "tool_misread": "The tool misread the document",
    "explained_elsewhere": "Explained by another document",
    "not_relevant": "Not relevant to this application",
    # Why a finding the specialists set aside was real after all.
    "explanation_not_valid": "The explanation for setting it aside does not hold",
    "part_of_a_pattern": "Part of a wider pattern",
    "other": "Other reason",
}

Lesson = Literal["false_alarm", "confirmed", "set_aside_wrongly", "missed"]


class FeedbackItem(BaseModel):
    """One piece of officer feedback, as the officer opens it."""

    id: str
    case_id: str
    document_id: str | None = None
    scope: ReviewScope
    kind: Literal["verdict", "missed"]
    check_name: str
    check_code: str | None = None
    document_type: str | None = None
    verdict: ReviewVerdict | None = None
    reason: str | None = None
    summary: str = ""
    note: str | None = None
    reviewer: str
    updated_at: str


class FeedbackDetail(FeedbackItem):
    """A feedback item opened: what the officer said, with the case it came from."""

    reason_label: str | None = None
    # The original case's number, and whether it still exists to open.
    greenlink_case_id: str | None = None


class FeedbackGroup(BaseModel):
    """One lesson about one check, counted over other cases (the feedback_counts function)."""

    check_name: str
    check_code: str | None = None
    lesson: Lesson
    # Distinct cases: one case's many documents count once.
    cases: int
    # Cases per reason given.
    reasons: dict[str, int] = {}
    notes: list[str] = []
    summaries: list[str] = []
    ids: list[str] = []
    latest: str

    @property
    def key(self) -> str:
        """What a finding matches it on: its check code, or the check itself for a finding without one."""
        return self.check_code or self.check_name


def verdict_key(case_id: str, document_id: str | None, fingerprint: str) -> str:
    return f"v:{case_id}:{document_id or 'case'}:{fingerprint}"


def missed_key(row_id: str) -> str:
    return f"m:{row_id}"


def _item(row: dict[str, Any]) -> FeedbackItem:
    return FeedbackItem(**{**row, "id": str(row["id"]), "document_id": row.get("document_id"), "updated_at": str(row["updated_at"])})


def save(
    *, source_key: str, case_id: str, document_id: str | None, scope: ReviewScope, kind: Literal["verdict", "missed"],
    check_name: str, check_code: str | None, document_type: str | None, verdict: ReviewVerdict | None,
    reason: ReviewReason | None, summary: str, note: str | None, reviewer: str, names: list[str],
) -> bool:
    """
    Saves (or replaces) the feedback this key names, scrubbed of confidential details. Text touching a protected
    attribute is not kept for the agents (any earlier item under the key goes too): returns False then.
    """
    if has_protected_text(f"{summary} {note or ''}"):
        withdraw(source_key)
        return False
    service_supabase_client().table(TABLE).upsert({
        "source_key": source_key, "case_id": case_id, "document_id": document_id, "scope": scope, "kind": kind,
        "check_name": check_name, "check_code": check_code, "document_type": document_type, "verdict": verdict,
        "reason": reason, "summary": scrub(summary, names) or "", "note": scrub(note, names), "reviewer": reviewer,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="source_key").execute()
    return True


def withdraw(source_key: str) -> None:
    """The officer cleared the verdict or deleted the missed issue: the agents stop seeing it."""
    service_supabase_client().table(TABLE).delete().eq("source_key", source_key).execute()


def get(feedback_id: str) -> FeedbackItem | None:
    rows = cast(list[dict[str, Any]], (
        service_supabase_client().table(TABLE).select(_COLUMNS).eq("id", feedback_id).limit(1).execute().data or []
    ))
    return _item(rows[0]) if rows else None


def get_many(feedback_ids: list[str]) -> list[FeedbackItem]:
    """The items still held (a withdrawn one is gone), in the order asked."""
    if not feedback_ids:
        return []
    rows = cast(list[dict[str, Any]], (
        service_supabase_client().table(TABLE).select(_COLUMNS).in_("id", feedback_ids).execute().data or []
    ))
    by_id = {str(row["id"]): _item(row) for row in rows}
    return [by_id[i] for i in feedback_ids if i in by_id]


def _cases(n: int) -> str:
    return f"{n} case" if n == 1 else f"{n} cases"


def describe(group: FeedbackGroup, missed_by: str) -> str:
    """One prompt line: "FALSE ALARM in 6 cases (Normal for this kind of document in 5) — officers' notes: ..."."""
    said = {
        "false_alarm": "FALSE ALARM",
        "confirmed": "CONFIRMED real",
        "set_aside_wrongly": "SET ASIDE WRONGLY, it was real",
        "missed": f"MISSED by {missed_by}",
    }[group.lesson]
    reasons = ", ".join(
        f"{REASON_LABELS.get(reason, reason)} in {n}"
        for reason, n in sorted(group.reasons.items(), key=lambda pair: -pair[1])
    )
    parts = [f"- {said} in {_cases(group.cases)}" + (f" ({reasons})" if reasons else "")]
    if group.summaries:
        parts.append(f"— e.g. finding: {group.summaries[0]}")
    if group.notes:
        parts.append("— officers' notes: " + "; ".join(f'"{note}"' for note in group.notes))
    return " ".join(parts)


def _clean(texts: list[str] | None, limit: int) -> list[str]:
    # Read-side bias screen too, so a tighter rule later also covers rows saved before it.
    return [t for t in texts or [] if t and not has_protected_text(t)][:limit]


def _teaches(group: FeedbackGroup) -> bool:
    return group.cases >= (MIN_FALSE_ALARM_CASES if group.lesson == "false_alarm" else 1)


def counts(check_names: list[str], scope: ReviewScope, exclude_case_id: str) -> list[FeedbackGroup]:
    """
    The lessons other cases teach about these checks, in one read, newest first: each check code's false alarms (once
    MIN_FALSE_ALARM_CASES cases agree), confirmations, wrongly set-aside findings and missed issues, with the cases
    behind each.
    """
    if not check_names:
        return []
    rows = cast(list[dict[str, Any]], service_supabase_client().rpc("feedback_counts", {
        "p_scope": scope, "p_check_names": check_names, "p_exclude_case_id": exclude_case_id,
    }).execute().data or [])
    groups = [
        FeedbackGroup(
            check_name=row["check_name"], check_code=row.get("check_code"), lesson=row["lesson"],
            cases=int(row["cases"]), reasons={k: int(v) for k, v in (row.get("reasons") or {}).items()},
            notes=_clean(row.get("notes"), MAX_NOTES), summaries=_clean(row.get("summaries"), 1),
            ids=[str(i) for i in row.get("ids") or []], latest=str(row["latest"]),
        )
        for row in rows
    ]
    return [g for g in groups if _teaches(g)]
