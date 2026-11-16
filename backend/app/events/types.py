"""Event type names and which role consumes which. Locked: renaming one touches every service."""

from __future__ import annotations

import json
from pathlib import Path

DOCUMENT_UPLOADED = "document.uploaded"
DOCUMENT_RETRY_REQUESTED = "document.retry_requested"
DOCUMENT_RETYPED = "document.retyped"
DOCUMENT_CANCELLED = "document.cancelled"
DOCUMENT_ANALYZED = "document.analyzed"
DOCUMENT_FAILED = "document.failed"
DOCUMENT_APPROVED = "document.approved"
DOCUMENT_REJECTED = "document.rejected"
DOCUMENT_TYPE_CORRECTED = "document.type_corrected"
# An officer let a document the content guard stopped go through (document_service.process_anyway): audit only.
DOCUMENT_GUARD_OVERRIDDEN = "document.guard_overridden"
DOCUMENT_FIELD_EDITED = "document.field_edited"
DOCUMENT_MOVED = "document.moved"
DOCUMENT_DELETED = "document.deleted"
# A classify/extract run an officer's cancel stopped, with what it had spent on Claude (workers/documents.py):
# audit only, for the case's cost.
DOCUMENT_RUN_STOPPED = "document.run_stopped"

DETECTION_RETRY_REQUESTED = "detection.retry_requested"
DETECTION_CANCEL_REQUESTED = "detection.cancel_requested"
# An officer re-ran one check on its own (routers/detection.py): audit only, nothing consumes it.
DETECTION_CHECK_RUN = "detection.check_run"
DOCUMENT_DETECTED = "document.detected"
DETECTION_FAILED = "detection.failed"

CASE_CREATED = "case.created"
CASE_RENAMED = "case.renamed"
CASE_DELETED = "case.deleted"
CASE_FINALISED = "case.finalised"
CASE_ANALYSIS_REQUESTED = "case.analysis.requested"
CASE_ANALYSIS_COMPLETED = "case.analysis.completed"
# An officer downloaded the case report PDF (routers/cases.py): audit only, nothing consumes it.
CASE_REPORT_EXPORTED = "case.report_exported"

AUTH_LOGIN = "auth.login"

NOTE_ADDED = "note.added"
NOTE_EDITED = "note.edited"
NOTE_DELETED = "note.deleted"

# An officer's review of detection findings (routers/finding_reviews.py): audit only, nothing consumes them.
# One review.saved per document (or the cross-document group) whose verdicts were saved.
FINDING_REVIEW_SAVED = "finding_review.saved"
MISSED_ISSUE_REPORTED = "finding_review.missed_reported"
MISSED_ISSUE_EDITED = "finding_review.missed_edited"
FINDING_REVIEW_REMOVED = "finding_review.removed"

# Stuck work (HOW_IT_WORKS.md §8, "Stuck work"). Cloud Scheduler publishes sweep.requested; the sweeper
# publishes recovery.requested with detail.owner, delivered only to that worker; the owner reports the outcome.
SWEEP_REQUESTED = "sweep.requested"
RECOVERY_REQUESTED = "recovery.requested"
DOCUMENT_RECOVERED = "document.recovered"
RECOVERY_EXHAUSTED = "recovery.exhausted"

EVERY_TYPE = "*"
# Message attribute naming the role a recovery.requested is for (also detail.owner).
OWNER_ATTRIBUTE = "owner"

# Role -> the types its subscription filters on. The emulator setup reads this; Terraform reads its export,
# deploy/terraform/subscriptions.json (regenerate with `python -m app.events.types`).
SUBSCRIPTIONS: dict[str, frozenset[str]] = {
    "doc-worker": frozenset({
        DOCUMENT_UPLOADED, DOCUMENT_RETRY_REQUESTED, DOCUMENT_RETYPED, DOCUMENT_CANCELLED, RECOVERY_REQUESTED,
    }),
    "detection-worker": frozenset({
        DOCUMENT_APPROVED, DETECTION_RETRY_REQUESTED, DETECTION_CANCEL_REQUESTED, RECOVERY_REQUESTED,
    }),
    "case-worker": frozenset({CASE_ANALYSIS_REQUESTED, RECOVERY_REQUESTED}),
    "audit-metrics": frozenset({EVERY_TYPE}),
}


def subscription_id(role: str) -> str:
    """Named for push, its first delivery mode; the name stays when it switches to pull (a rename replaces it)."""
    return f"{role}-push"


# Pub/Sub's limit on a filter expression; a filter is also immutable, so changing one replaces the subscription.
MAX_FILTER_BYTES = 256


def subscription_filter(types: frozenset[str], role: str | None = None) -> str:
    """
    The Pub/Sub filter for a subscription; empty means every event. Only recovery requests carry the owner
    attribute, so matching it alone delivers just this role's (and keeps the filter short).
    """
    if EVERY_TYPE in types:
        return ""
    clauses = [
        f'attributes.{OWNER_ATTRIBUTE} = "{role}"' if t == RECOVERY_REQUESTED else f'attributes.type = "{t}"'
        for t in sorted(types)
    ]
    expression = " OR ".join(clauses)
    if len(expression.encode()) > MAX_FILTER_BYTES:
        raise ValueError(f"{role}'s filter is {len(expression.encode())} bytes; Pub/Sub allows {MAX_FILTER_BYTES}")
    return expression


def subscriptions_export() -> str:
    return json.dumps(
        {role: {"filter": subscription_filter(t, role)} for role, t in sorted(SUBSCRIPTIONS.items())}, indent=2
    ) + "\n"


if __name__ == "__main__":
    out = Path(__file__).resolve().parents[3] / "deploy" / "terraform" / "subscriptions.json"
    out.write_text(subscriptions_export())
    print(f"Wrote {out}")
