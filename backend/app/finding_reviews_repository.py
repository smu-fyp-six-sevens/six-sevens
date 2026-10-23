"""
The `finding_reviews` table (backend/sql/finding_reviews.sql): loan officers' verdicts on detection findings
and the issues they found that detection missed. detection_metrics turns them into precision and recall.

A verdict names one finding by the id the case risk gives it, which only holds for that detection run: a
document's verdicts and missed issues are cleared when its detection runs again, and the case's cross-document
ones when a new case analysis completes (clear_document / clear_case_scope). Reviews go with their document or
case when either is deleted, and follow a document moved to another case.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast

from .models import FindingReview, ReviewScope, ReviewVerdict
from .supabase_client import service_supabase_client

TABLE = "finding_reviews"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_for_case(case_id: str) -> list[FindingReview]:
    rows = (
        service_supabase_client().table(TABLE).select("*")
        .eq("case_id", case_id).order("created_at").execute().data or []
    )
    return [FindingReview.model_validate(row) for row in rows]


def list_all() -> list[FindingReview]:
    rows = service_supabase_client().table(TABLE).select("*").execute().data or []
    return [FindingReview.model_validate(row) for row in rows]


def get(review_id: str) -> FindingReview | None:
    rows = service_supabase_client().table(TABLE).select("*").eq("id", review_id).limit(1).execute().data or []
    return FindingReview.model_validate(rows[0]) if rows else None


def set_verdict(
    *, case_id: str, scope: ReviewScope, document_id: str | None, finding_id: str, check_name: str,
    check_code: str | None, summary: str, verdict: ReviewVerdict, reviewer: str,
) -> None:
    """Records the officer's verdict on one finding, replacing any earlier one (whoever gave it)."""
    client = service_supabase_client()
    existing = cast(list[dict[str, Any]], (
        client.table(TABLE).select("id").eq("case_id", case_id).eq("kind", "verdict")
        .eq("finding_id", finding_id).limit(1).execute().data or []
    ))
    values = {
        "scope": scope, "document_id": document_id, "check_name": check_name, "check_code": check_code,
        "summary": summary, "verdict": verdict, "reviewer": reviewer, "updated_at": _now(),
    }
    if existing:
        client.table(TABLE).update(values).eq("id", existing[0]["id"]).execute()
    else:
        client.table(TABLE).insert({"case_id": case_id, "kind": "verdict", "finding_id": finding_id, **values}).execute()


def clear_verdict(case_id: str, finding_id: str) -> None:
    service_supabase_client().table(TABLE).delete().eq("case_id", case_id).eq("kind", "verdict").eq(
        "finding_id", finding_id
    ).execute()


def add_missed(
    *, case_id: str, scope: ReviewScope, document_id: str | None, check_name: str, note: str | None, reviewer: str,
) -> None:
    result = service_supabase_client().table(TABLE).insert({
        "case_id": case_id, "scope": scope, "document_id": document_id, "kind": "missed",
        "check_name": check_name, "note": note, "reviewer": reviewer,
    }).execute()
    if not result.data:
        raise RuntimeError(f"The missed issue on case {case_id} was not saved")


def delete(review_id: str) -> None:
    service_supabase_client().table(TABLE).delete().eq("id", review_id).execute()


def clear_document(document_id: str) -> None:
    """A fresh detection run replaces the document's findings, so the reviews of the old ones go."""
    service_supabase_client().table(TABLE).delete().eq("document_id", document_id).execute()


def clear_case_scope(case_id: str) -> None:
    """A new case analysis replaces the cross-document findings, so the reviews of the old ones go."""
    service_supabase_client().table(TABLE).delete().eq("case_id", case_id).eq("scope", "case").execute()


def move_with_documents(document_ids: list[str], case_id: str) -> None:
    """A moved document takes its reviews to its new case."""
    if document_ids:
        service_supabase_client().table(TABLE).update({"case_id": case_id}).in_("document_id", document_ids).execute()
