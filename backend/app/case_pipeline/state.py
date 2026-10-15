"""
What a stored analysis means against the case as it is now: whether it is
stale, whether it is still really running, and which findings may count
towards the score.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from ..models import APPROVED_STATUSES, DocumentRecord
from .models import CaseAnalysis, CrossDocFinding

# The one in-flight analysis task per case, in this process.
running_tasks: dict[str, asyncio.Task] = {}


def compute_fingerprint(documents: list[DocumentRecord], reports: dict[str, dict[str, Any]]) -> str:
    """A hash of everything an analysis reads: the approved documents, their fields and their findings."""
    parts = []
    for document in sorted((d for d in documents if d.status in APPROVED_STATUSES), key=lambda d: d.id):
        report = reports.get(document.id) or {}
        findings = sorted((f.get("id"), f.get("severity")) for f in report.get("findings") or [])
        parts.append([
            document.id, document.status.value, document.document_type,
            json.dumps(document.extracted_fields or {}, sort_keys=True, default=str), findings,
        ])
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()


def current_view(
    analysis: CaseAnalysis, documents: list[DocumentRecord], reports: dict[str, dict[str, Any]]
) -> CaseAnalysis:
    view = analysis.model_copy()
    view.stale = analysis.fingerprint != compute_fingerprint(documents, reports)
    task = running_tasks.get(analysis.case_id)
    if analysis.status == "running" and (task is None or task.done()):
        view.status = "failed"
        view.error = "The analysis was interrupted before it finished. Run it again."
    return view


def usable_findings(analysis: CaseAnalysis | None) -> list[CrossDocFinding]:
    """Only a finished analysis over the documents as they are now counts towards the score."""
    if analysis is None or analysis.status != "complete" or analysis.stale:
        return []
    return analysis.findings
