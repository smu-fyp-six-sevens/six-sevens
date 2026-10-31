"""
The `findings` table (backend/sql/schema.sql): every finding, what the agents decided about it, and the
officer's verdict.

* A document's detection findings, kept and dismissed, written from its finished report (sync_document).
* A case's cross-document findings, written when a case analysis completes and is the newest (sync_case). Linked
  to the case only.
* Missed issues: what an officer found that no finding raised (source "officer"), for recall.

A finished run replaces the previous run's findings outright, with the verdicts on them (the missed issues
officers reported stay), in one transaction (the sync_findings function).
Officers review at the end, on the findings that stand. A finding is named by its fingerprint (finding_keys), which
the run's report and risk carry too. Rows follow a document moved to another case; the database removes them with
their document or case.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

from .case_pipeline.categories import category_for_finding
from .case_pipeline.models import CrossDocFinding, FindingCategory
from .finding_keys import report_findings
from .models import FindingReview, ReviewScope, ReviewVerdict
from .supabase_client import service_supabase_client
from .supabase_rows import fetch_all

TABLE = "findings"
_CASE_CHUNK = 100
_CASE_LEVEL = frozenset({FindingCategory.CROSS_DOCUMENT_VARIANCE, FindingCategory.IDENTITY_ENTITY_CONFLICT})
# Stored as columns, or under named evidence keys; everything else the tool returned goes in evidence.details.
_COLUMN_KEYS = frozenset({
    "id", "tool", "summary", "severity", "rationale", "reason", "specialists", "via_second_look",
    "ai_accepted", "confidence", "agent_confidences", "fingerprint", "check_code",
})
_REVIEW_COLUMNS = (
    "id, case_id, document_id, scope, source, fingerprint, check_name, check_code, summary, agent_decision, verdict, "
    "reviewer, review_note, reviewed_at, created_at, updated_at"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _confidence(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        return None
    return round(float(value), 3)


def _document_row(finding: dict[str, Any], decision: str) -> dict[str, Any]:
    dismissed = decision == "dismissed"
    specialists = [s for s in finding.get("specialists") or [] if isinstance(s, str)]
    return {
        "fingerprint": finding["fingerprint"],
        "report_finding_id": finding.get("id"),
        "check_name": finding.get("tool") or "unknown",
        "check_code": finding.get("check_code") if isinstance(finding.get("check_code"), str) else None,
        "category": category_for_finding(finding).value,
        # A dismissed finding has no specialist severity, only the tool's hint (in evidence.details).
        "severity": None if dismissed else finding.get("severity"),
        "summary": finding.get("summary") or "",
        "rationale": finding.get("reason") if dismissed else finding.get("rationale"),
        "confidence": _confidence(finding.get("confidence")),
        "evidence": {
            "specialists": specialists,
            "via_second_look": bool(finding.get("via_second_look")),
            "agent_confidences": finding.get("agent_confidences") or [],
            "details": {k: v for k, v in finding.items() if k not in _COLUMN_KEYS},
        },
        "agent_decision": decision,
    }


def _case_row(finding: CrossDocFinding) -> dict[str, Any]:
    return {
        "fingerprint": finding.fingerprint,
        "report_finding_id": finding.id,
        "check_name": finding.category.value,
        "check_code": None,
        "category": finding.category.value,
        "severity": finding.severity,
        "summary": finding.summary,
        "rationale": finding.rationale,
        "confidence": _confidence(finding.confidence),
        "evidence": {
            "document_ids": finding.document_ids,
            "citations": finding.citations,
            "hypothesis_id": finding.hypothesis_id,
            "probe_summary": finding.probe_summary,
            "breach_flags": finding.breach_flags,
        },
        "agent_decision": None,
    }


def _sync(case_id: str, document_id: str | None, rows: list[dict[str, Any]]) -> int:
    result = service_supabase_client().rpc(
        "sync_findings", {"p_case_id": case_id, "p_document_id": document_id, "p_rows": rows},
    ).execute()
    return int(cast(Any, result.data) or 0)


def sync_document(document_id: str, case_id: str, report: dict[str, Any]) -> int:
    """A finished detection report's findings, kept and dismissed (fingerprinted by the graph). Returns how many."""
    return _sync(case_id, document_id, [_document_row(f, decision) for f, decision in report_findings(report)])


def sync_case(case_id: str, findings: list[CrossDocFinding]) -> int:
    """The newest complete case analysis's cross-document findings. Returns how many."""
    return _sync(case_id, None, [_case_row(f) for f in findings])


def _review(row: dict[str, Any]) -> FindingReview:
    missed = row["source"] == "officer"
    return FindingReview(
        id=row["id"], case_id=row["case_id"], document_id=row.get("document_id"), scope=row["scope"],
        kind="missed" if missed else "verdict", fingerprint=row.get("fingerprint"),
        check_name=row["check_name"], check_code=row.get("check_code"), summary=row.get("summary") or "",
        agent_decision=row.get("agent_decision"), verdict=row.get("verdict"), note=row.get("review_note"), reviewer=row["reviewer"],
        created_at=row["created_at"] if missed else row["reviewed_at"], updated_at=row["reviewed_at"],
    )


def _reviews_query():
    """Findings an officer has marked, and missed issues."""
    return (
        service_supabase_client().table(TABLE).select(_REVIEW_COLUMNS)
        .not_.is_("reviewer", "null")
    )


def list_reviews_for_case(case_id: str) -> list[FindingReview]:
    rows = fetch_all(lambda: _reviews_query().eq("case_id", case_id), "id")
    return sorted((_review(row) for row in rows), key=lambda review: review.created_at)


def list_all_reviews() -> list[FindingReview]:
    return [_review(row) for row in fetch_all(_reviews_query, "id")]


def get_review(row_id: str) -> FindingReview | None:
    rows = cast(list[dict[str, Any]], (
        service_supabase_client().table(TABLE).select(_REVIEW_COLUMNS).eq("id", row_id)
        .not_.is_("reviewer", "null").limit(1).execute().data or []
    ))
    return _review(rows[0]) if rows else None


def find_current(case_id: str, scope: ReviewScope, document_id: str | None, fingerprint: str) -> dict[str, Any] | None:
    """The finding this fingerprint names in the case now, or None when a later run replaced it."""
    query = (
        service_supabase_client().table(TABLE).select("id, document_id, check_name")
        .eq("case_id", case_id).eq("scope", scope).eq("fingerprint", fingerprint)
    )
    query = query.eq("document_id", document_id) if document_id is not None else query.is_("document_id", "null")
    rows = cast(list[dict[str, Any]], query.limit(1).execute().data or [])
    return rows[0] if rows else None


def set_verdict(row_id: str, verdict: ReviewVerdict | None, reviewer: str) -> None:
    """The officer's verdict on one finding, replacing any earlier one; None clears it."""
    values: dict[str, Any] = (
        {"verdict": verdict, "reviewer": reviewer, "reviewed_at": _now()}
        if verdict is not None
        else {"verdict": None, "reviewer": None, "review_note": None, "reviewed_at": None}
    )
    result = service_supabase_client().table(TABLE).update({**values, "updated_at": _now()}).eq("id", row_id).execute()
    if not result.data:
        raise RuntimeError(f"Finding {row_id} was not updated")


def add_missed(
    *, case_id: str, scope: ReviewScope, document_id: str | None, check_name: str, note: str | None, reviewer: str,
) -> None:
    result = service_supabase_client().table(TABLE).insert({
        "case_id": case_id, "document_id": document_id, "scope": scope, "source": "officer",
        "check_name": check_name, "review_note": note, "reviewer": reviewer, "reviewed_at": _now(),
    }).execute()
    if not result.data:
        raise RuntimeError(f"The missed issue on case {case_id} was not saved")


def update_missed(row_id: str, *, check_name: str, note: str | None, reviewer: str) -> None:
    """Corrects a missed issue's check and note; the officer who edited it becomes its reviewer."""
    result = service_supabase_client().table(TABLE).update({
        "check_name": check_name, "review_note": note, "reviewer": reviewer, "reviewed_at": _now(),
        "updated_at": _now(),
    }).eq("id", row_id).eq("source", "officer").execute()
    if not result.data:
        raise RuntimeError(f"Missed issue {row_id} was not updated")


def delete_missed(row_id: str) -> None:
    service_supabase_client().table(TABLE).delete().eq("id", row_id).eq("source", "officer").execute()


def delete_case_findings_citing(document_ids: list[str]) -> None:
    """
    Cross-document findings that rest on a document being deleted go with it, as its own findings do (by the
    foreign key). The case's analysis is out of date until it runs again.
    """
    client = service_supabase_client()
    for document_id in document_ids:
        client.table(TABLE).delete().eq("scope", "case").filter(
            "evidence->document_ids", "cs", json.dumps([document_id])
        ).execute()


def move_with_documents(document_ids: list[str], case_id: str) -> None:
    """A moved document takes its findings, and the missed issues reported on it, to its new case."""
    if document_ids:
        service_supabase_client().table(TABLE).update({"case_id": case_id}).in_("document_id", document_ids).execute()


def list_flags_for_cases(case_ids: list[str]) -> list[dict[str, Any]]:
    """
    These cases' current document findings a specialist accepted, other than the case-level kinds the case
    analysis covers: what the case list counts. A query per 100 cases (and per 1,000 rows).
    """
    rows: list[dict[str, Any]] = []
    for start in range(0, len(case_ids), _CASE_CHUNK):
        chunk = case_ids[start : start + _CASE_CHUNK]
        rows += fetch_all(
            lambda chunk=chunk: service_supabase_client().table(TABLE)
            .select("id, case_id, document_id, check_name, severity, category")
            .in_("case_id", chunk).eq("source", "detection").eq("agent_decision", "accepted"),
            "id",
        )
    return [row for row in rows if row.get("category") not in {c.value for c in _CASE_LEVEL}]
