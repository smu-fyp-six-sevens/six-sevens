"""
The `finding_feedback` table (backend/sql/schema.sql): what officers taught the agents.

Every verdict an officer saves on a finding, and every missed issue they report, is copied here with its
confidential details removed (feedback_scrub). A finished run replaces the findings and their verdicts, but not this:
feedback survives re-runs, and goes only when the officer clears the verdict or the missed issue. Before a
specialist reviews a document, `similar` hands it up to 5 items for the same check from other cases.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, cast

from pydantic import BaseModel

from .feedback_scrub import scrub
from .models import ReviewReason, ReviewScope, ReviewVerdict
from .supabase_client import service_supabase_client

TABLE = "finding_feedback"
# The most items a specialist is shown for a check.
MAX_SHOWN = 5

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


class FeedbackItem(BaseModel):
    """One piece of officer feedback, as the agents are shown it and as the officer opens it."""

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
) -> None:
    """Saves (or replaces) the feedback this key names, scrubbed of confidential details."""
    service_supabase_client().table(TABLE).upsert({
        "source_key": source_key, "case_id": case_id, "document_id": document_id, "scope": scope, "kind": kind,
        "check_name": check_name, "check_code": check_code, "document_type": document_type, "verdict": verdict,
        "reason": reason, "summary": scrub(summary, names) or "", "note": scrub(note, names), "reviewer": reviewer,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }, on_conflict="source_key").execute()


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


def similar(check_name: str, scope: ReviewScope, exclude_case_id: str, document_type: str | None = None) -> list[FeedbackItem]:
    """
    Up to MAX_SHOWN items for this check from other cases, newest first, with one confirmation (a finding an officer
    found real) among them when one exists, so the agents see what was right as well as what was a false alarm.
    Items on the same kind of document come first.
    """
    rows = cast(list[dict[str, Any]], (
        service_supabase_client().table(TABLE).select(_COLUMNS)
        .eq("check_name", check_name).eq("scope", scope).neq("case_id", exclude_case_id)
        .order("updated_at", desc=True).limit(50).execute().data or []
    ))
    items = sorted((_item(row) for row in rows), key=lambda i: 0 if document_type and i.document_type == document_type else 1)
    return _pick(items)


def _pick(items: list[FeedbackItem]) -> list[FeedbackItem]:
    chosen = items[:MAX_SHOWN]
    if len(items) > MAX_SHOWN and not any(i.verdict == "correct" for i in chosen):
        confirmation = next((i for i in items[MAX_SHOWN:] if i.verdict == "correct"), None)
        if confirmation is not None:
            chosen = [*chosen[: MAX_SHOWN - 1], confirmation]
    return chosen
