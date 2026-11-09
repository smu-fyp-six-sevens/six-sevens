"""
Everything the report shows, read in one go by gather(), and the rules it applies to that data: each document's
Routing Outcome, where the case analysis stands, and which documents changed since it ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from .. import audit, case_service, document_repository, document_service, extracted_fields_repository, notes_repository
from ..case_match import check_case_match
from ..case_pipeline import repository as analysis_repository
from ..case_pipeline import service as analysis_service
from ..case_pipeline.models import CaseAnalysis
from ..config import settings
from ..events import types
from ..models import (
    APPROVED_STATUSES,
    CaseNote,
    CaseSummary,
    DocumentRecord,
    DocumentStatus,
    RoutingStatus,
    is_classification_confirmed,
)

AnalysisState = Literal["not_run", "running", "failed", "stale", "complete"]

# The document events that make an analysis out of date (they change what state.compute_fingerprint reads), and
# how the banner names each. Notes and finding reviews don't change the analysis, so they aren't listed.
STALE_EVENT_LABELS = {
    types.DOCUMENT_UPLOADED: "uploaded",
    types.DOCUMENT_ANALYZED: "extracted",
    types.DOCUMENT_RETYPED: "type changed",
    types.DOCUMENT_TYPE_CORRECTED: "type confirmed",
    types.DOCUMENT_FIELD_EDITED: "fields edited",
    types.DOCUMENT_APPROVED: "approved",
    types.DOCUMENT_DETECTED: "scanned",
    types.DOCUMENT_REJECTED: "rejected",
    types.DOCUMENT_FAILED: "failed processing",
    types.DOCUMENT_MOVED: "moved",
    types.DOCUMENT_DELETED: "deleted",
}


# A document changed since the analysis: its name, the latest thing that happened to it, and when.
ChangedDocument = tuple[str, str, datetime]


@dataclass
class ReportData:
    summary: CaseSummary
    documents: list[DocumentRecord]
    # The case's newest run, whatever its status; None when the case has never been analysed.
    latest: CaseAnalysis | None
    # The newest finished run, whose results the analysis sections show (the latest one when it finished).
    completed: CaseAnalysis | None
    # None when the notes couldn't be read: the section then says so rather than "no notes".
    notes: list[CaseNote] | None = field(default_factory=list)
    # When the analysis is out of date: each document changed since it ran, with its latest change, oldest first
    # (from the audit trail). Empty when nothing is out of date or the trail couldn't be read.
    changed_documents: list[ChangedDocument] = field(default_factory=list)
    # document id -> its Routing Outcome, worked out in gather() from fields with their confidences and the case
    # match. A document missing here (a report built by hand, as in tests) is worked out from the record alone.
    routing: dict[str, str] = field(default_factory=dict)
    generated_by: str | None = None
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def gather(case_id: str, generated_by: str | None = None) -> ReportData | None:
    """Everything the report shows, or None when there is no such case (sync: run on a worker thread)."""
    summary = case_service.get_case_summary(case_id)
    if summary is None:
        return None
    documents = document_repository.list_documents(case_id=case_id, include_fields=False)
    fields_by_document = extracted_fields_repository.get_extracted_fields_for_documents([d.id for d in documents])
    for document in documents:
        # Plain {name: value}, as list_documents gives them, for the checklist and the case match.
        document.extracted_fields = {
            f.field_name: f.field_value for f in fields_by_document.get(document.id, [])
        } or None
    latest = analysis_service.get_current(case_id)
    completed = latest if latest is not None and latest.status == "complete" else None
    if latest is not None and completed is None:
        # The newest run hasn't finished (or failed): the last finished one is still the case's result.
        finished = next((row for row in analysis_repository.list_runs(case_id) if row.get("status") == "complete"), None)
        if finished is not None:
            completed = analysis_service.get_run(case_id, str(finished["run_id"]))
    try:
        notes: list[CaseNote] | None = notes_repository.list_notes(case_id)
    except Exception:
        notes = None
    try:
        case_matches = {m.document_id: m.status for m in check_case_match(summary, documents).documents}
    except Exception:
        # Without it a mismatched document reads by its own routing alone, as before the match ran.
        case_matches = {}
    # The Routing Outcome needs each field's confidence and the missing required fields, as the case page has them.
    routing = {
        d.id: routing_outcome(
            document_service.present(d.model_copy(), fields_by_document.get(d.id, [])), case_matches.get(d.id),
        )
        for d in documents
    }
    return ReportData(
        summary=summary, documents=documents, latest=latest, completed=completed, notes=notes,
        changed_documents=_changed_since(case_id, latest, documents) if latest is not None and latest.stale else [],
        routing=routing, generated_by=generated_by,
    )


def _changed_since(case_id: str, analysis: CaseAnalysis, documents: list[DocumentRecord]) -> list[ChangedDocument]:
    """The documents changed after the analysis started, each with its latest change and when, from the audit trail."""
    try:
        events = audit.list_events(case_id=case_id, limit=500)
    except Exception:
        return []
    filenames = {d.id: d.filename for d in documents}
    latest: dict[str, tuple[str, datetime]] = {}
    # Newest first from the trail; walked oldest first so each document ends at its latest change.
    for event in reversed(events):
        label = STALE_EVENT_LABELS.get(event.type)
        if label is None or event.document_id is None or event.occurred_at < analysis.started_at:
            continue
        name = filenames.get(event.document_id) or str(event.detail.get("filename") or "A removed document")
        latest[name] = (label, event.occurred_at)
    return sorted(((name, label, when) for name, (label, when) in latest.items()), key=lambda change: change[2])


def _field_value(raw: object) -> object:
    return raw.get("value") if isinstance(raw, dict) and "value" in raw else raw


def _has_extracted_information(fields: dict | None) -> bool:
    return any(not case_service.is_empty_field_value(_field_value(raw)) for raw in (fields or {}).values())


def _lowest_confidence(fields: dict | None) -> float | None:
    found = [
        raw["confidence"] for raw in (fields or {}).values()
        if isinstance(raw, dict) and isinstance(raw.get("confidence"), (int, float))
    ]
    return min(found) if found else None


def routing_outcome(document: DocumentRecord, case_match_status: str | None = None) -> str:
    """The document's Routing Outcome as the case page shows it (frontend documentStatus.formatRoutingOutcome)."""
    if document.status == DocumentStatus.REJECTED:
        return "Rejected"
    if document.status in APPROVED_STATUSES:
        return "Approved"
    lowest = _lowest_confidence(document.extracted_fields)
    if (
        document.status == DocumentStatus.FAILED
        or document.routing_status == RoutingStatus.MANUAL_REVIEW
        or (document.status == DocumentStatus.ANALYZED and not is_classification_confirmed(document))
        or (document.status == DocumentStatus.ANALYZED and not _has_extracted_information(document.extracted_fields))
        or case_match_status == "manual_review"
        or (lowest is not None and lowest < settings.EXTRACTION_CONFIDENCE_THRESHOLD)
        or document.missing_required_fields
    ):
        return "Manual Review"
    if document.routing_status == RoutingStatus.ACCEPTED:
        return "Accepted" if document.status == DocumentStatus.ANALYZED else "Pending"
    return "—"


def analysis_state(latest: CaseAnalysis | None) -> AnalysisState:
    if latest is None:
        return "not_run"
    if latest.status == "running":
        return "running"
    if latest.status == "failed":
        return "failed"
    if latest.stale or latest.status == "superseded":
        return "stale"
    return "complete"


def filename_for(summary: CaseSummary) -> str:
    """e.g. "GLDB-137 Case Report.pdf"."""
    return f"{summary.greenlink_case_id} Case Report.pdf"



def manual_review_count(data: ReportData) -> int:
    """Documents waiting on the officer: the case page's Flagged Items (not yet approved or rejected)."""
    return sum(1 for d in data.documents if _outcome(data, d) == "Manual Review")


def _outcome(data: ReportData, document: DocumentRecord) -> str:
    return data.routing.get(document.id) or routing_outcome(document)
