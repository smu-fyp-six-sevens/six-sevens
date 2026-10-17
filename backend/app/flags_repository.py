"""
The `flags` table: the per-document findings a specialist approved as genuine.

Written by code from a finished detection report, never by the model. The
trace keeps every finding a tool returned; this table keeps only the ones a
specialist assessed and did not dismiss. Case-level findings (a name or
address that differs between documents, an identity mismatch) are not flags
here: they belong to the case analysis.
"""

from __future__ import annotations

import uuid
from typing import Any

from .case_pipeline.categories import category_for_finding
from .case_pipeline.models import FindingCategory
from .supabase_client import service_supabase_client, supabase_client

_TABLE = "flags"
_CASE_LEVEL = frozenset({FindingCategory.CROSS_DOCUMENT_VARIANCE, FindingCategory.IDENTITY_ENTITY_CONFLICT})
# Keys stored as columns or under named evidence keys; everything else a tool returned goes in evidence.details.
_COLUMN_KEYS = frozenset({
    "id", "tool", "summary", "severity", "rationale", "specialists", "via_second_look",
    "ai_accepted", "flag_id", "confidence",
})


def is_flag(finding: dict[str, Any]) -> bool:
    return bool(finding.get("ai_accepted")) and category_for_finding(finding) not in _CASE_LEVEL


def assign_flag_ids(findings: list[dict[str, Any]]) -> None:
    """Gives each finding that will be a flag its row id, in place, before the report is saved,
    so the report, the flag row and the risk score all name the same id."""
    for finding in findings:
        if is_flag(finding):
            finding["flag_id"] = str(uuid.uuid4())


def _confidence(finding: dict[str, Any]) -> float | None:
    value = finding.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        return None
    return round(float(value), 3)


def build_rows(document_id: str, case_id: str | None, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for finding in findings:
        if not finding.get("flag_id"):
            continue
        specialists = [s for s in finding.get("specialists") or [] if isinstance(s, str)]
        rows.append({
            "id": finding["flag_id"],
            "document_id": document_id,
            "case_id": case_id,
            "detection_dimension": specialists[0] if specialists else "unknown",
            "check_name": finding.get("tool") or "unknown",
            "description": finding.get("summary") or "",
            "severity": finding.get("severity") or "medium",
            "confidence": _confidence(finding),
            "evidence": {
                "finding_id": finding.get("id"),
                "rationale": finding.get("rationale"),
                "specialists": specialists,
                "via_second_look": bool(finding.get("via_second_look")),
                "details": {k: v for k, v in finding.items() if k not in _COLUMN_KEYS},
            },
        })
    return rows


def replace_for_document(document_id: str, case_id: str | None, findings: list[dict[str, Any]]) -> int:
    """
    Replaces this document's open flags with those of a fresh detection run, so
    a retry doesn't stack a second copy. A flag an officer has already moved
    out of "open" is left alone. Returns how many flags were written.
    """
    rows = build_rows(document_id, case_id, findings)
    # The backend role may only read and insert flags (RLS); a delete it isn't allowed would match
    # nothing and still report success, leaving the old flags in place. So the delete uses the service client.
    service_supabase_client().table(_TABLE).delete().eq("document_id", document_id).eq("status", "open").execute()
    if rows:
        supabase_client.table(_TABLE).insert(rows).execute()
    return len(rows)
