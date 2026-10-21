"""Event type names and which role consumes which. Locked: renaming one touches every service."""

from __future__ import annotations

DOCUMENT_UPLOADED = "document.uploaded"
DOCUMENT_RETRY_REQUESTED = "document.retry_requested"
DOCUMENT_RETYPED = "document.retyped"
DOCUMENT_CANCELLED = "document.cancelled"
DOCUMENT_ANALYZED = "document.analyzed"
DOCUMENT_FAILED = "document.failed"
DOCUMENT_APPROVED = "document.approved"
DOCUMENT_REJECTED = "document.rejected"
DOCUMENT_TYPE_CORRECTED = "document.type_corrected"
DOCUMENT_FIELD_EDITED = "document.field_edited"
DOCUMENT_MOVED = "document.moved"
DOCUMENT_DELETED = "document.deleted"

DETECTION_RETRY_REQUESTED = "detection.retry_requested"
DETECTION_CANCEL_REQUESTED = "detection.cancel_requested"
DOCUMENT_DETECTED = "document.detected"
DETECTION_FAILED = "detection.failed"

CASE_CREATED = "case.created"
CASE_RENAMED = "case.renamed"
CASE_DELETED = "case.deleted"
CASE_ANALYSIS_REQUESTED = "case.analysis.requested"
CASE_ANALYSIS_COMPLETED = "case.analysis.completed"

AUTH_LOGIN = "auth.login"

EVERY_TYPE = "*"

# Role -> the types its subscription filters on. The emulator setup and Terraform read this.
SUBSCRIPTIONS: dict[str, frozenset[str]] = {
    "doc-worker": frozenset({DOCUMENT_UPLOADED, DOCUMENT_RETRY_REQUESTED, DOCUMENT_RETYPED, DOCUMENT_CANCELLED}),
    "detection-worker": frozenset({DOCUMENT_APPROVED, DETECTION_RETRY_REQUESTED, DETECTION_CANCEL_REQUESTED}),
    "case-worker": frozenset({CASE_ANALYSIS_REQUESTED}),
    "audit-metrics": frozenset({EVERY_TYPE}),
}
