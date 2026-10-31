"""
What a stored analysis means against the case as it is now: whether it is
stale, whether it is still really running, and which findings may count
towards the score.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import settings
from ..models import APPROVED_STATUSES, DocumentRecord, DocumentStatus, WaitingDocument
from .models import CaseAnalysis
from .risk.case_risk import counts_towards_risk


def compute_fingerprint(documents: list[DocumentRecord], reports: dict[str, dict[str, Any]]) -> str:
    """A hash of everything an analysis reads: the approved documents, their fields and their findings. Approving,
    rejecting, moving or deleting a document, or a rerun of its detection, changes it."""
    parts: list[Any] = []
    for document in sorted((d for d in documents if d.status in APPROVED_STATUSES), key=lambda d: d.id):
        report = reports.get(document.id) or {}
        findings = sorted((f.get("id"), f.get("severity")) for f in report.get("findings") or [])
        parts.append([
            document.id, document.status.value, document.document_type,
            json.dumps(document.extracted_fields or {}, sort_keys=True, default=str), findings,
        ])
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


_IN_PIPELINE = frozenset({
    DocumentStatus.UPLOADED, DocumentStatus.QUEUED, DocumentStatus.PROCESSING, DocumentStatus.EXTRACTING,
})


def waiting_documents(documents: list[DocumentRecord], reports: dict[str, dict[str, Any]]) -> list[WaitingDocument]:
    """The documents the risk score counts that aren't scanned yet, each with what it is waiting for, in upload
    order. Empty means a rerun now produces a score."""
    waiting = []
    for document in sorted(documents, key=lambda d: d.uploaded_at):
        if not counts_towards_risk(document, reports, set()):
            continue
        if document.status == DocumentStatus.DETECTED and document.id in reports:
            continue
        if document.status == DocumentStatus.FAILED:
            reason = "failed"
        elif document.status in APPROVED_STATUSES:
            reason = "scanning"
        elif document.status in _IN_PIPELINE:
            reason = "processing"
        else:
            reason = "needs_approval"
        waiting.append(WaitingDocument(document_id=document.id, filename=document.filename, reason=reason))
    return waiting


def is_live(status: str, started_at: datetime) -> bool:
    """A run reads as running only within the time limit; past it the worker was lost (no heartbeat yet)."""
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    limit = timedelta(seconds=settings.CASE_ANALYSIS_TIMEOUT_SECONDS)
    return status == "running" and datetime.now(timezone.utc) - started_at < limit


def current_view(
    analysis: CaseAnalysis, documents: list[DocumentRecord], reports: dict[str, dict[str, Any]]
) -> CaseAnalysis:
    view = analysis.model_copy()
    view.stale = analysis.fingerprint != compute_fingerprint(documents, reports)
    if analysis.status == "running" and not is_live(analysis.status, analysis.started_at):
        view.status = "failed"
        view.error = "The analysis was interrupted before it finished. Run it again."
    return view
