"""
The case report a loan officer downloads as a PDF (GET /cases/{id}/report.pdf): the case's risk, the case
analysis's summary and findings, the recommended additional documents, the checklist, the documents and the notes.

gather() reads everything the report shows; render() lays it out, so the layout is testable without a database.
The sections that come from the case analysis appear only once an analysis has finished; until then a banner
says why (not run yet, still running, or did not finish). Officer Notes appears only when the case has notes.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    PageBreak,
    Flowable,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from . import audit, case_service, document_repository, document_service, extracted_fields_repository, notes_repository
from .case_match import check_case_match
from .case_pipeline import repository as analysis_repository
from .case_pipeline import service as analysis_service
from .case_pipeline.models import CaseAnalysis
from .config import settings
from .labels import document_type_label, requirement_label
from .events import types
from .models import (
    ADDITIONAL_DOCUMENT_TYPES,
    APPROVED_STATUSES,
    REQUIRED_DOCUMENT_TYPES,
    CaseNote,
    CaseRisk,
    CaseSummary,
    DocumentRecord,
    DocumentStatus,
    RoutingStatus,
    is_classification_confirmed,
)

# Singapore has no daylight saving, so a fixed offset is exact and needs no tz database in the image.
SGT = timezone(timedelta(hours=8), "SGT")

# The app's palette (frontend/src/styles/base.css, tokens.css).
INK = colors.HexColor("#12203d")
INK_SOFT = colors.HexColor("#5b6478")
STEEL = colors.HexColor("#355f96")
LINE = colors.HexColor("#e3e2de")
PANEL = colors.HexColor("#f7f8fa")
PANEL_LINE = colors.HexColor("#e0e5eb")
TEAL = colors.HexColor("#166d5f")
TEAL_BG = colors.HexColor("#e1f1ec")
AMBER = colors.HexColor("#c6820a")
AMBER_BG = colors.HexColor("#fbf0da")
RED = colors.HexColor("#b3402a")
RED_BG = colors.HexColor("#fbe8e2")
BLUE_BG = colors.HexColor("#eef6ff")
BLUE = colors.HexColor("#2563b8")


def _hex(colour: colors.Color) -> str:
    """A colour as "#rrggbb", for <font color=...> inside Paragraph markup."""
    return "#" + colour.hexval()[2:]


SOFT = _hex(INK_SOFT)

# The crimson risk scale, light to dark, so no level reads as "safe" (tokens.css --risk-*): (badge fill, badge
# text, gauge band). Low's word is ink-soft, as on the case page.
_RISK_LOW_TINT = colors.HexColor("#fdf3f5")
_RISK_MEDIUM_TINT = colors.HexColor("#f8d7de")
_RISK_MEDIUM_INK = colors.HexColor("#7a1530")
_RISK_HIGH = colors.HexColor("#b4233c")
RISK_COLOURS: dict[str, tuple[colors.Color, colors.Color, colors.Color]] = {
    "Low": (_RISK_LOW_TINT, INK_SOFT, colors.HexColor("#e3a6b2")),
    "Medium": (_RISK_MEDIUM_TINT, _RISK_MEDIUM_INK, colors.HexColor("#d9667f")),
    "High": (_RISK_HIGH, colors.white, _RISK_HIGH),
    "Critical": (colors.HexColor("#6b1024"), colors.white, colors.HexColor("#6b1024")),
}
# The 0-100 score's bands (case_risk.SCORE_BANDS).
RISK_BANDS = [("Low", 0, 24), ("Medium", 25, 49), ("High", 50, 79), ("Critical", 80, 100)]

# A finding's severity on the same crimson scale: (fill, text).
SEVERITY_COLOURS: dict[str, tuple[colors.Color, colors.Color]] = {
    "high": (_RISK_HIGH, colors.white),
    "medium": (_RISK_MEDIUM_TINT, _RISK_MEDIUM_INK),
    "low": (_RISK_LOW_TINT, INK_SOFT),
}
# A severity's mark on white (a list's dot), the scale's colour for that level.
SEVERITY_MARKS: dict[str, colors.Color] = {
    "high": _RISK_HIGH, "medium": colors.HexColor("#d9667f"), "low": colors.HexColor("#e3a6b2"),
}
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}

# The checklist in the sidebar's order (frontend REQUIRED_DOCUMENT_TYPES).
REQUIREMENT_ORDER = [
    "id_document", "bank_statement", "proof_of_address", "fatca_crs_declaration", "certificate_of_incorporation",
    "certificate_of_incumbency", "company_constitution", "credit_report", "payslip", "source_of_wealth",
    "notice_of_assessment",
]
CATEGORY_LABELS = {
    "cross_document_variance": "Documents differ",
    "identity_entity_conflict": "Identity conflict",
    "registry_discrepancy": "Registry mismatch",
    "behavioural_anomaly": "Unusual behaviour",
    "unsupported_claim": "Unsupported claim",
    "tampering_forensic": "Tampering",
}
STATUS_LABELS = {
    "uploaded": "Uploaded",
    "queued": "Queued",
    "processing": "Classifying",
    "extracting": "Extracting",
    "analyzed": "Extracted",
    "approved": "Approved",
    "detecting": "Scanning",
    "detected": "Detected",
    "rejected": "Rejected",
    "failed": "Failed",
    "cancelled": "Cancelled",
}
# The case's stage as the case page's badge names it (frontend utils/caseStages.ts).
CASE_STAGE_LABELS = {
    "open": "Awaiting Documents",
    "processing_documents": "Processing Documents",
    "pending_review": "Pending Review",
    "documents_incomplete": "Missing Documents",
    "fraud_detection": "Detecting Fraud",
    "ready_for_analysis": "Ready to Analyse",
    "pending_evaluation": "Pending Evaluation",
    "case_analysed": "Ready to Finalise",
    "finalised": "Finalised",
    "failed": "Failed",
}
# The badge's colour for each stage, by who acts next (caseStages.ts tones, case-stage.css --tone-tint and
# --tone-ink): (fill, ink).
_TONES = {
    "idle": ("#f3f6fa", "#43536b"), "system": ("#e8f0fb", "#2f5fa8"), "officer": ("#fff8e8", "#8a5a00"),
    "done": ("#eaf7f3", "#17603c"), "closed": ("#e8edf3", "#34455f"), "failed": ("#fbe8e2", "#9a3522"),
}
_STAGE_TONES = {
    "open": "idle", "processing_documents": "system", "fraud_detection": "system", "pending_review": "officer",
    "documents_incomplete": "officer", "ready_for_analysis": "officer", "pending_evaluation": "officer",
    "case_analysed": "done", "finalised": "closed", "failed": "failed",
}
CASE_STAGE_COLOURS: dict[str, tuple[colors.Color, colors.Color]] = {
    stage: (colors.HexColor(_TONES[tone][0]), colors.HexColor(_TONES[tone][1])) for stage, tone in _STAGE_TONES.items()
}


def _stage_label(stage: str) -> str:
    return CASE_STAGE_LABELS.get(stage, stage)
# Same as the header (frontend lib/officers.ts); a username not listed is shown as it is.
OFFICER_NAMES = {"greenlink-tester": "J. Tan", "demo": "K. Lim", "bob": "B. Smith", "carol": "C. Wong"}

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


# --- formatting ------------------------------------------------------------------------------------------------


def _text(value: object) -> str:
    """User or model text, safe inside a Paragraph's markup."""
    return escape(str(value)).replace("\n", "<br/>")


def _when(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SGT).strftime("%d %b %Y, %H:%M SGT")


def _date(value: datetime | None) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SGT).strftime("%d %b %Y")


def _check(tool: str | None) -> str:
    """"font_consistency_tool" -> "Font consistency" (frontend utils/checkLabels.ts)."""
    if not tool:
        return "Check"
    if tool in CATEGORY_LABELS:
        return CATEGORY_LABELS[tool]
    words = tool.removesuffix("_tool").replace("_", " ")
    words = words[:1].upper() + words[1:]
    return "AI" + words[2:] if words.startswith("Ai ") else words


def _officer(username: str | None) -> str:
    if not username:
        return "Loan Officer"
    return OFFICER_NAMES.get(username, username)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# --- styles ----------------------------------------------------------------------------------------------------

_BASE = ParagraphStyle("base", fontName="Helvetica", fontSize=9.5, leading=13.5, textColor=INK)
STYLES = {
    "body": _BASE,
    "soft": ParagraphStyle("soft", parent=_BASE, textColor=INK_SOFT),
    "small": ParagraphStyle("small", parent=_BASE, fontSize=8.5, leading=11.5, textColor=INK_SOFT),
    "cell": ParagraphStyle("cell", parent=_BASE, fontSize=8.5, leading=11.5),
    "cell_bold": ParagraphStyle("cell_bold", parent=_BASE, fontSize=8.5, leading=11.5, fontName="Helvetica-Bold"),
    "cell_right": ParagraphStyle("cell_right", parent=_BASE, fontSize=8.5, leading=11.5, alignment=TA_RIGHT),
    "th": ParagraphStyle("th", parent=_BASE, fontSize=7.5, leading=10, fontName="Helvetica-Bold", textColor=INK_SOFT),
    "th_right": ParagraphStyle(
        "th_right", parent=_BASE, fontSize=7.5, leading=10, fontName="Helvetica-Bold", textColor=INK_SOFT,
        alignment=TA_RIGHT,
    ),
    "title": ParagraphStyle("title", parent=_BASE, fontName="Helvetica-Bold", fontSize=22, leading=27),
    "eyebrow": ParagraphStyle(
        "eyebrow", parent=_BASE, fontName="Helvetica-Bold", fontSize=8, leading=11, textColor=STEEL,
    ),
    "headline": ParagraphStyle("headline", parent=_BASE, fontName="Helvetica-Bold", fontSize=11.5, leading=16),
    "kpi_label": ParagraphStyle(
        "kpi_label", parent=_BASE, fontName="Helvetica-Bold", fontSize=7, leading=9, textColor=INK_SOFT,
    ),
    "pill": ParagraphStyle("pill", parent=_BASE, fontName="Helvetica-Bold", fontSize=7, leading=9, alignment=TA_CENTER),
    "notice": ParagraphStyle("notice", parent=_BASE, fontSize=9, leading=13),
}

PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN = 18 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN


# --- building blocks -------------------------------------------------------------------------------------------


class SectionHeading(Flowable):
    """A section's number and title over a thin rule, kept with what follows it."""

    def __init__(self, number: int, title: str, note: str | None = None):
        super().__init__()
        self.number, self.title, self.note = number, title, note
        self.height = 30

    def wrap(self, aW, aH):
        self.width = aW
        return aW, self.height

    def draw(self):
        c = self.canv
        c.setFillColor(INK)
        c.roundRect(0, 9, 18, 18, 4, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(9, 15, str(self.number))
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(26, 13, self.title)
        if self.note:
            c.setFillColor(INK_SOFT)
            c.setFont("Helvetica", 8.5)
            c.drawRightString(self.width, 13, self.note)
        c.setStrokeColor(LINE)
        c.setLineWidth(0.8)
        c.line(0, 2, self.width, 2)


class RiskGauge(Flowable):
    """The four score bands, the current one solid, with a marker at the score (the risk card's gauge)."""

    def __init__(self, score: int | None, level: str | None, width: float):
        super().__init__()
        self.score, self.level, self.width, self.height = score, level, width, 30

    def wrap(self, aW, aH):
        return self.width, self.height

    def draw(self):
        c = self.canv
        gap, bar_y, bar_h = 2, 14, 8
        x = 0.0
        for name, low, high in RISK_BANDS:
            band_width = (self.width - gap * 3) * (high - low + 1) / 101
            c.setFillColor(RISK_COLOURS[name][2])
            c.setFillAlpha(1 if name == self.level else 0.35)
            c.roundRect(x, bar_y, band_width, bar_h, 2, stroke=0, fill=1)
            c.setFillAlpha(1)
            c.setFillColor(INK if name == self.level else INK_SOFT)
            c.setFont("Helvetica-Bold" if name == self.level else "Helvetica", 7)
            c.drawString(x, 3, f"{name} {low}–{high}")
            x += band_width + gap
        if self.score is not None:
            marker_x = (self.width - gap * 3) * min(max(self.score, 0), 100) / 101 + gap * _band_index(self.score)
            c.setFillColor(INK)
            c.roundRect(marker_x - 1.5, bar_y - 3, 3, bar_h + 6, 1.5, stroke=0, fill=1)


def _band_index(score: int) -> int:
    return next(i for i, (_, low, high) in enumerate(RISK_BANDS) if score <= high or i == len(RISK_BANDS) - 1)


def _pill(text: str, fill: colors.Color, ink: colors.Color, width: float = 17 * mm) -> Table:
    style = ParagraphStyle("pill_coloured", parent=STYLES["pill"], textColor=ink)
    table = Table([[Paragraph(_text(text.upper()), style)]], colWidths=[width], rowHeights=[13])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), fill),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    return table


def _severity_pill(severity: str) -> Table:
    fill, ink = SEVERITY_COLOURS.get(severity.lower(), SEVERITY_COLOURS["low"])
    return _pill(severity, fill, ink, width=15 * mm)


def _risk_pill(level: str) -> Table:
    fill, ink, _ = RISK_COLOURS[level]
    return _pill(level, fill, ink, width=20 * mm)


NoticeTone = Literal["info", "warning", "error", "success", "empty"]
_NOTICE_COLOURS: dict[str, tuple[colors.Color, colors.Color]] = {
    "info": (BLUE_BG, BLUE),
    "warning": (AMBER_BG, AMBER),
    "error": (RED_BG, RED),
    "success": (TEAL_BG, TEAL),
    "empty": (PANEL, colors.HexColor("#98a2b3")),
}


def _notice(title: str, body: str | None = None, tone: NoticeTone = "empty", body_is_markup: bool = False) -> Table:
    """
    A tinted box with a coloured edge: an empty section's placeholder, or a warning about the results shown.
    The body is plain text, escaped here, unless body_is_markup says the caller already escaped and marked it up.
    """
    fill, edge = _NOTICE_COLOURS[tone]
    content = f"<b>{_text(title)}</b>"
    if body:
        content += f"<br/><font color='{SOFT}'>{body if body_is_markup else _text(body)}</font>"
    table = Table([["", Paragraph(content, STYLES["notice"])]], colWidths=[3, CONTENT_WIDTH - 3])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), edge),
        ("BACKGROUND", (1, 0), (1, 0), fill),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 10),
        ("RIGHTPADDING", (1, 0), (1, 0), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return table


def _data_table(header: list[Paragraph], rows: list[list], widths: list[float], gutter: float = 5) -> Table:
    """A header row and zebra-striped rows that repeat their header on a new page. `gutter` pads each cell's sides."""
    table = Table([header, *rows], colWidths=widths, repeatRows=1)
    commands = [
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), gutter),
        ("RIGHTPADDING", (0, 0), (-1, -1), gutter),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, PANEL_LINE),
    ]
    for index in range(1, len(rows) + 1):
        if index % 2 == 0:
            commands.append(("BACKGROUND", (0, index), (-1, index), PANEL))
    table.setStyle(TableStyle(commands))
    return table


def _th(text: str, right: bool = False) -> Paragraph:
    return Paragraph(_text(text.upper()), STYLES["th_right" if right else "th"])


class _Mark(Flowable):
    """A drawn tick, cross or dot: Helvetica has none of those glyphs, and a symbol font isn't embedded everywhere."""

    def __init__(self, kind: Literal["tick", "cross", "dot"], colour: colors.Color, size: float = 8):
        super().__init__()
        self.kind, self.colour, self.size = kind, colour, size

    def wrap(self, aW, aH):
        return self.size, self.size + 3

    def draw(self):
        c, s = self.canv, self.size
        c.setStrokeColor(self.colour)
        c.setFillColor(self.colour)
        c.setLineWidth(1.6)
        c.setLineCap(1)
        if self.kind == "tick":
            path = c.beginPath()
            path.moveTo(0.5, s * 0.5)
            path.lineTo(s * 0.38, s * 0.15)
            path.lineTo(s - 0.5, s * 0.9)
            c.drawPath(path, stroke=1, fill=0)
        elif self.kind == "cross":
            c.line(1, 1, s - 1, s - 1)
            c.line(1, s - 1, s - 1, 1)
        else:
            c.circle(s / 2, s / 2, s * 0.32, stroke=0, fill=1)


def _check_mark(ok: bool) -> _Mark:
    return _Mark("tick", TEAL) if ok else _Mark("cross", RED)


class _Chip(Flowable):
    """A rounded label sized to its text, with an optional status dot before it."""

    def __init__(self, text: str, fill: colors.Color, ink: colors.Color, size: float = 8, dot: bool = False):
        super().__init__()
        self.text, self.fill, self.ink, self.size, self.dot = text, fill, ink, size, dot
        self.pad = size * 1.1
        self.dot_space = size * 1.2 if dot else 0
        self.width = stringWidth(text, "Helvetica-Bold", size) + 2 * self.pad + self.dot_space
        self.height = size * 2.3

    def wrap(self, aW, aH):
        return self.width, self.height

    def draw(self):
        c, h = self.canv, self.height
        c.setFillColor(self.fill)
        c.roundRect(0, 0, self.width, h, h / 2, stroke=0, fill=1)
        c.setFillColor(self.ink)
        if self.dot:
            c.circle(self.pad + self.size * 0.3, h / 2, self.size * 0.3, stroke=0, fill=1)
        c.setFont("Helvetica-Bold", self.size)
        c.drawString(self.pad + self.dot_space, h / 2 - self.size * 0.35, self.text)


# --- sections --------------------------------------------------------------------------------------------------


def _title_block(data: ReportData) -> list[Flowable]:
    """A navy panel: the company, its case number and opening date, and its stage in the case page's colour."""
    summary = data.summary
    company = summary.company_name or "Unnamed company"
    stage = summary.status
    status_fill, status_ink = CASE_STAGE_COLOURS.get(stage, CASE_STAGE_COLOURS["open"])

    case_chip = _Chip(summary.greenlink_case_id, colors.HexColor("#2b3d61"), colors.white, size=8.5)
    meta = Table(
        [[case_chip, Paragraph(f"<font color='#c8d4e6'>Opened {_date(summary.created_at)}</font>", STYLES["soft"])]],
        colWidths=[case_chip.width + 8, None],
    )
    meta.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    left = [
        Paragraph(_text(company), ParagraphStyle("hero_title", parent=STYLES["title"], textColor=colors.white)),
        Spacer(1, 8),
        meta,
    ]
    status_chip = _Chip(_stage_label(stage).upper(), status_fill, status_ink, size=7, dot=True)
    right = Table([[status_chip]], colWidths=[status_chip.width], hAlign="RIGHT", style=TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    right_width = status_chip.width + 12
    hero = Table([[left, right]], colWidths=[CONTENT_WIDTH - right_width, right_width])
    hero.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), INK),
        ("ROUNDEDCORNERS", [8, 8, 8, 8]),
        ("VALIGN", (0, 0), (0, 0), "MIDDLE"),
        # The status sits small in the top right corner.
        ("VALIGN", (1, 0), (1, 0), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (0, 0), 18),
        # Keeps a long company name clear of the status.
        ("RIGHTPADDING", (0, 0), (0, 0), 24),
        ("LEFTPADDING", (1, 0), (1, 0), 0),
        ("RIGHTPADDING", (1, 0), (1, 0), 12),
        ("TOPPADDING", (0, 0), (0, 0), 18),
        ("BOTTOMPADDING", (0, 0), (0, 0), 18),
        ("TOPPADDING", (1, 0), (1, 0), 12),
    ]))
    return [Paragraph("LOAN APPLICATION · CASE REPORT", STYLES["eyebrow"]), Spacer(1, 6), hero, Spacer(1, 12)]


class _StatCard(Flowable):
    """One summary card: a coloured strip along the top, a label with a dot, a big figure and a line under it."""

    def __init__(self, label: str, value: str, suffix: str, note: str, accent: colors.Color, width: float):
        super().__init__()
        self.label, self.value, self.suffix, self.note, self.accent = label, value, suffix, note, accent
        self.width, self.height = width, 80

    def wrap(self, aW, aH):
        return self.width, self.height

    def draw(self):
        c, w, h = self.canv, self.width, self.height
        c.saveState()
        clip = c.beginPath()
        clip.roundRect(0, 0, w, h, 6)
        c.clipPath(clip, stroke=0, fill=0)
        c.setFillColor(colors.white)
        c.rect(0, 0, w, h, stroke=0, fill=1)
        c.setFillColor(self.accent)
        c.rect(0, h - 3.5, w, 3.5, stroke=0, fill=1)
        c.restoreState()
        c.setStrokeColor(PANEL_LINE)
        c.setLineWidth(0.7)
        c.roundRect(0, 0, w, h, 6, stroke=1, fill=0)

        c.setFillColor(self.accent)
        c.circle(12, h - 15.5, 2.6, stroke=0, fill=1)
        c.setFillColor(INK_SOFT)
        c.setFont("Helvetica-Bold", 6.8)
        c.drawString(19, h - 18, self.label)
        c.setFillColor(INK)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(10, h - 42, self.value)
        if self.suffix:
            c.setFillColor(INK_SOFT)
            c.setFont("Helvetica", 9)
            c.drawString(12 + stringWidth(self.value, "Helvetica-Bold", 20), h - 42, self.suffix)
        c.setFillColor(self.accent)
        c.setFont("Helvetica-Bold", 7.5)
        # Up to two lines, broken at plain spaces (a no-break space keeps a number with its word).
        lines, line = [], ""
        for word in self.note.split(" "):
            if line and stringWidth(f"{line} {word}", "Helvetica-Bold", 7.5) > w - 20:
                lines.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        lines.append(line)
        for index, text in enumerate(lines[:2]):
            c.drawString(10, 17 - index * 9.5, text)


def manual_review_count(data: ReportData) -> int:
    """Documents waiting on the officer: the case page's Flagged Items (not yet approved or rejected)."""
    return sum(1 for d in data.documents if _outcome(data, d) == "Manual Review")


def _outcome(data: ReportData, document: DocumentRecord) -> str:
    return data.routing.get(document.id) or routing_outcome(document)


def _stat_cards(data: ReportData) -> Table:
    """The four summary cards under the title: approved, manual review, risk score and findings."""
    completed = data.completed
    risk = completed.risk if completed else None
    # Nothing to show or do yet (no score, no documents waiting): the card stays quiet.
    grey = colors.HexColor("#b9c2ce")

    total = len(data.documents)
    approved = sum(1 for d in data.documents if d.status in APPROVED_STATUSES)
    review = manual_review_count(data)

    all_approved = total > 0 and approved == total
    if not total:
        approved_note = "None uploaded"
    else:
        approved_note = "All approved" if all_approved else f"{total - approved} not approved yet"
    cards = [
        ("DOCUMENTS APPROVED", str(approved), f"/ {total}", approved_note, TEAL if all_approved else STEEL),
        ("NEED MANUAL REVIEW", str(review), "",
         "Waiting for an officer" if review else "Not required", AMBER if review else grey),
    ]
    if risk and risk.state == "scored" and risk.level:
        cards.append(("RISK SCORE", str(risk.score) if risk.score is not None else "—", "/ 100",
                      f"{risk.level} Risk", RISK_COLOURS[risk.level][2]))
    else:
        cards.append(("RISK SCORE", "—", "", "Not scored yet", grey))
    if risk:
        count = len(risk.contributing_findings)
        # Contributing findings come from one document's own scan, or from comparing documents with each other.
        cross = sum(1 for f in risk.contributing_findings if f.source == "cross_document")
        if not count:
            findings_note = "Nothing raised"
        elif cross:
            findings_note = f"{count - cross} in single documents, {cross}\u00a0cross-document"
        else:
            findings_note = "All in single documents"
        cards.append(("FINDINGS", str(count), "", findings_note, RED if count else TEAL))
    else:
        cards.append(("FINDINGS", "—", "", "No analysis yet", grey))

    gap = 8
    width = (CONTENT_WIDTH - 3 * gap) / 4
    row: list = []
    for index, card in enumerate(cards):
        row.append(_StatCard(*card, width=width))
        if index < len(cards) - 1:
            row.append("")
    table = Table([row], colWidths=[width, gap, width, gap, width, gap, width])
    table.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return table


_CHANGES_SHOWN = 6


def _analysis_status_banner(data: ReportData) -> Table:
    """One line on where the case analysis stands, under the tiles, so every reader knows what the report rests on."""
    latest, completed = data.latest, data.completed
    if latest is None:
        return _notice("No case analysis has been run yet.", tone="warning")
    state = analysis_state(latest)
    if state == "complete":
        return _notice(f"Case analysis complete and up to date as of {_when(latest.finished_at)}.", tone="success")
    previous = f" The results below come from the analysis finished {_when(completed.finished_at)}." if completed else ""
    if state == "running":
        return _notice(
            "The case analysis is still running",
            f"Started {_when(latest.started_at)}."
            + (previous or " Download the report again once it finishes to see the score and findings."),
            "info",
        )
    if state == "failed":
        return _notice(
            "The case analysis did not finish",
            f"{latest.error or 'The run stopped before it finished.'}{previous} Run it again for current results.",
            "error",
        )
    intro = (
        f"The results below come from the analysis finished {_when(latest.finished_at)}. "
        "The case's documents have changed since, so the score and findings don't include those changes yet."
    )
    if not data.changed_documents:
        return _notice("Case analysis is out of date", f"{intro} Run it again to bring them up to date.", "warning")
    shown = data.changed_documents[:_CHANGES_SHOWN]
    lines = "".join(
        # No-break spaces keep the date on one line.
        f"<br/>•&nbsp; <b>{_text(name)}</b> - {label.capitalize()} as of {_date(when).replace(' ', '&nbsp;')}"
        for name, label, when in shown
    )
    more = len(data.changed_documents) - len(shown)
    if more:
        lines += f"<br/>•&nbsp; and {_plural(more, 'other document')}"
    return _notice(
        "Case analysis is out of date",
        f"{_text(intro)}<br/><br/>Changed since then:{lines}<br/><br/>Run the case analysis again to include them.",
        "warning",
        body_is_markup=True,
    )


def _summary_section(completed: CaseAnalysis) -> list[Flowable]:
    structured = completed.structured_summary
    risk = completed.risk
    out: list[Flowable] = []
    if structured is None:
        return [_notice("The analysis did not write a summary")]

    level = risk.level if risk and risk.state == "scored" else None
    headline = Paragraph(_text(structured.headline), STYLES["headline"])
    if level:
        row = Table([[_risk_pill(level), headline]], colWidths=[24 * mm, CONTENT_WIDTH - 24 * mm])
        row.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        out.append(row)
    else:
        out.append(headline)
    out.append(Spacer(1, 8))

    if structured.reasons:
        findings = risk.contributing_findings if risk else []
        out.append(Paragraph("KEY REASONS", STYLES["kpi_label"]))
        out.append(Spacer(1, 4))
        rows = []
        for reason in structured.reasons:
            finding = next(
                (
                    f for f in findings
                    if f.finding_id == reason.finding_id
                    and (f.source == "cross_document" or f.document_id == reason.document_id)
                ),
                None,
            )
            severity = finding.severity if finding else "low"
            dot = _Mark("dot", SEVERITY_MARKS.get(severity, SEVERITY_MARKS["low"]))
            where = f"<br/><font size='7.5' color='{SOFT}'>{_text(finding.filename)}</font>" if finding and finding.source == "document" else ""
            rows.append([dot, Paragraph(_text(reason.text) + where, STYLES["body"])])
        table = Table(rows, colWidths=[6 * mm, CONTENT_WIDTH - 6 * mm])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        out += [table, Spacer(1, 6)]

    if structured.would_help:
        out.append(_notice("What would help", structured.would_help, "info"))
    return out


def _risk_section(risk: CaseRisk | None) -> list[Flowable]:
    if risk is None:
        return [_notice("This analysis did not record a risk score", "Run the case analysis again to score the case.")]
    out: list[Flowable] = []

    if risk.state != "scored" or not risk.level:
        if risk.state == "pending":
            out.append(_notice(
                "Not scored yet: documents still being scanned",
                f"{risk.documents_scanned} of {risk.documents_total} documents had been scanned when the analysis ran.",
                "info",
            ))
        elif risk.state == "failed":
            out.append(_notice("Not scored: fraud detection failed on a document", "; ".join(risk.reasons) or None, "error"))
        else:
            out.append(_notice("Not scored: the case had no scanned documents when the analysis ran"))
        return out

    band = RISK_COLOURS[risk.level][2]
    score = Paragraph(
        f"<font size='30'><b>{risk.score if risk.score is not None else '—'}</b></font>"
        f"<font size='10' color='{SOFT}'> / 100</font>",
        ParagraphStyle("score", parent=STYLES["body"], leading=34),
    )
    level_line = Paragraph(
        f"<font color='{_hex(band)}'><b>{risk.level.upper()} RISK</b></font>"
        f"<font color='{SOFT}'> &nbsp;·&nbsp; {risk.documents_scanned} of {risk.documents_total} documents scanned</font>",
        STYLES["body"],
    )
    gauge_width = CONTENT_WIDTH - 40 * mm - 24
    header = Table(
        [[score, [level_line, Spacer(1, 6), RiskGauge(risk.score, risk.level, gauge_width)]]],
        colWidths=[40 * mm, CONTENT_WIDTH - 40 * mm],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, -1), PANEL),
        ("BOX", (0, 0), (-1, -1), 0.6, PANEL_LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    out += [header, Spacer(1, 10)]

    findings = sorted(
        risk.contributing_findings,
        key=lambda f: (-f.points, -_SEVERITY_RANK.get(f.severity.lower(), 0)),
    )
    out.append(Paragraph(f"CONTRIBUTING FINDINGS ({len(findings)})", STYLES["kpi_label"]))
    out.append(Spacer(1, 4))
    if not findings:
        out.append(_notice("No findings contributed to the score", None, "success"))
    else:
        rows = []
        for finding in findings:
            source = (
                "Across documents"
                if finding.source == "cross_document"
                else f"{_text(finding.filename)}<br/><font color='{SOFT}'>{document_type_label(finding.document_type)}</font>"
            )
            check = _check(finding.category if finding.source == "cross_document" else finding.tool)
            detail = f"<b>{_text(finding.summary)}</b>"
            if finding.rationale:
                detail += f"<br/><font color='{SOFT}'>{_text(finding.rationale)}</font>"
            if finding.also_on:
                detail += f"<br/><font size='7.5' color='{SOFT}'>Also on {_plural(finding.also_on, 'other document')}</font>"
            rows.append([
                _severity_pill(finding.severity),
                Paragraph(detail, STYLES["cell"]),
                Paragraph(f"{source}<br/><font size='7.5' color='{_hex(BLUE)}'>{_text(check)}</font>", STYLES["cell"]),
                Paragraph(f"{finding.points:g}", STYLES["cell_right"]),
            ])
        out.append(_data_table(
            [_th("Severity"), _th("Finding"), _th("Where · check"), _th("Points", right=True)],
            rows,
            [19 * mm, CONTENT_WIDTH - 19 * mm - 46 * mm - 14 * mm, 46 * mm, 14 * mm],
        ))

    if risk.unassessed_checks:
        out.append(Spacer(1, 8))
        lines = "; ".join(
            f"{_check(c.tool)} on {c.filename}" + (f" ({c.reason})" if c.reason else "") for c in risk.unassessed_checks
        )
        out.append(_notice(
            f"{_plural(len(risk.unassessed_checks), 'check')} could not run (not scored)",
            lines, "warning",
        ))
    return out


def _cross_document_section(completed: CaseAnalysis) -> list[Flowable]:
    out: list[Flowable] = []
    if not completed.findings:
        out.append(_notice(
            "No cross-document issues found",
            "The analysis compared the case's documents with each other and found nothing that conflicts.",
            "success",
        ))
    else:
        findings = sorted(completed.findings, key=lambda f: -_SEVERITY_RANK.get(f.severity, 0))
        rows = []
        for finding in findings:
            detail = f"<b>{_text(finding.summary)}</b>"
            if finding.rationale:
                detail += f"<br/><font color='{SOFT}'>{_text(finding.rationale)}</font>"
            rows.append([
                _severity_pill(finding.severity),
                Paragraph(_text(CATEGORY_LABELS.get(finding.category.value, finding.category.value)), STYLES["cell_bold"]),
                Paragraph(detail, STYLES["cell"]),
            ])
        out.append(_data_table(
            [_th("Severity"), _th("Type"), _th("Finding")],
            rows,
            [19 * mm, 32 * mm, CONTENT_WIDTH - 51 * mm],
        ))
    return out


def _additional_documents_section(completed: CaseAnalysis, summary: CaseSummary) -> list[Flowable]:
    additional = completed.additional_documents or summary.additional_documents
    items = additional.items if additional else []
    if not items:
        reason = additional.reason if additional else None
        body = {
            "no_reducible_findings": "None of the findings is one that a further document could explain.",
            "model_failed": "The recommendation could not be produced on this run; run the analysis again to retry.",
            "budget": "The recommendation was skipped on this run to stay within the cost limit.",
        }.get(reason or "", "The documents on file are enough for this case.")
        tone: NoticeTone = "warning" if reason in ("model_failed", "budget") else "success"
        return [_notice("No additional documents to request", body, tone)]

    out: list[Flowable] = []
    if additional and additional.best_case_score is not None and additional.current_score is not None:
        out.append(Paragraph(
            f"If every document below supports the application, the score could fall from "
            f"<b>{additional.current_score}</b> ({_text(additional.current_level or '')}) to "
            f"<b>{additional.best_case_score}</b> ({_text(additional.best_case_level or '')}).",
            STYLES["body"],
        ))
        out.append(Spacer(1, 6))
    rows = []
    for index, item in enumerate(items, start=1):
        why = f"<b>{_text(item.title)}</b><br/><font color='{SOFT}'>{_text(item.why)}</font>"
        if item.recommendation and item.recommendation.request:
            why += f"<br/><font size='7.5'><i>Ask for: {_text(item.recommendation.request)}</i></font>"
        if item.affects_score and item.score_if_supports is not None:
            effect = f"<b>{item.score_if_supports}</b><br/><font size='7.5' color='{SOFT}'>{_text(item.level_if_supports or '')}</font>"
        else:
            effect = f"<font color='{SOFT}'>{'Checklist' if item.source == 'checklist' else '—'}</font>"
        rows.append([
            Paragraph(f"<b>{index}</b>", STYLES["cell"]),
            Paragraph(why, STYLES["cell"]),
            Paragraph(effect, STYLES["cell_right"]),
        ])
    out.append(_data_table(
        [_th("#"), _th("Document to request"), _th("Score if it supports", right=True)],
        rows,
        [8 * mm, CONTENT_WIDTH - 8 * mm - 30 * mm, 30 * mm],
    ))
    return out


def _checklist_section(data: ReportData) -> list[Flowable]:
    summary = data.summary
    fulfilled = set(summary.fulfilled_requirements)
    ordered = [r for r in REQUIREMENT_ORDER if r in REQUIRED_DOCUMENT_TYPES]
    ordered += [r for r in REQUIRED_DOCUMENT_TYPES if r not in ordered]

    def note_for(requirement: str) -> str:
        if requirement == "proof_of_address" and summary.proof_of_address_coverage.total > 0:
            coverage = summary.proof_of_address_coverage
            text = f"{coverage.covered} of {coverage.total} individuals covered"
            if coverage.unmatched_names:
                text += f"; missing for {', '.join(coverage.unmatched_names)}"
            return text
        if requirement == "id_document" and summary.id_document_coverage.total > 0:
            coverage = summary.id_document_coverage
            text = f"{coverage.covered} of {coverage.total} individuals covered"
            if coverage.missing_side:
                text += f"; {coverage.missing_side} side missing"
            return text
        return ""

    def column(requirements: list[str]) -> Table:
        rows = [
            [
                _check_mark(r in fulfilled),
                Paragraph(
                    _text(requirement_label(r))
                    + (f"<br/><font size='7.5' color='{SOFT}'>{_text(note_for(r))}</font>" if note_for(r) else ""),
                    STYLES["cell"] if r in fulfilled else ParagraphStyle("missing", parent=STYLES["cell"], textColor=RED),
                ),
            ]
            for r in requirements
        ]
        table = Table(rows, colWidths=[6 * mm, CONTENT_WIDTH / 2 - 6 * mm - 6])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return table

    half = (len(ordered) + 1) // 2
    met = sum(1 for r in ordered if r in fulfilled)
    out: list[Flowable] = [
        Paragraph(f"REQUIRED DOCUMENTS · {met} OF {len(ordered)} PRESENT", STYLES["kpi_label"]),
        Spacer(1, 5),
    ]
    grid = Table([[column(ordered[:half]), column(ordered[half:])]], colWidths=[CONTENT_WIDTH / 2] * 2)
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    out.append(grid)

    extras = [r for r in ADDITIONAL_DOCUMENT_TYPES if r in fulfilled]
    if extras:
        out += [
            Spacer(1, 6),
            Paragraph(
                "<b>Also on file:</b> " + ", ".join(_text(requirement_label(r)) for r in extras),
                STYLES["small"],
            ),
        ]
    return out


def _wrap_filename(name: str, width: float, font: str = "Helvetica-Bold", size: float = 8.5) -> str:
    """The name as markup broken onto lines after its underscores or dashes, never mid-word, to fit `width`."""
    pieces, piece = [], ""
    for char in name:
        piece += char
        if char in "_-":
            pieces.append(piece)
            piece = ""
    if piece:
        pieces.append(piece)
    lines, line = [], ""
    for piece in pieces:
        if line and stringWidth(line + piece, font, size) > width:
            lines.append(line)
            line = piece
        else:
            line += piece
    lines.append(line)
    return "<br/>".join(_text(line) for line in lines)


def _documents_section(data: ReportData) -> list[Flowable]:
    if not data.documents:
        return [_notice("No documents have been uploaded to this case")]
    status_tone = {
        "detected": (TEAL_BG, TEAL), "approved": (TEAL_BG, TEAL), "analyzed": (TEAL_BG, TEAL),
        "detecting": (BLUE_BG, BLUE), "rejected": (RED_BG, RED), "failed": (RED_BG, RED),
        "cancelled": (PANEL, INK_SOFT),
    }
    outcome_colour = {"Manual Review": RED, "Rejected": RED, "Accepted": TEAL, "Approved": TEAL}
    gutter = 8
    # Type, status, routing outcome, uploaded; the file name gets the rest and wraps at its underscores.
    widths = [36 * mm, 25 * mm, 28 * mm, 24 * mm]
    file_width = CONTENT_WIDTH - sum(widths)
    rows = []
    for document in sorted(data.documents, key=lambda d: d.uploaded_at):
        status = document.status.value
        fill, ink = status_tone.get(status, (BLUE_BG, BLUE))
        outcome = _outcome(data, document)
        colour = _hex(outcome_colour.get(outcome, INK_SOFT))
        rows.append([
            Paragraph(_wrap_filename(document.filename, file_width - 2 * gutter), STYLES["cell_bold"]),
            Paragraph(_text(document_type_label(document.document_type)), STYLES["cell"]),
            _pill(STATUS_LABELS.get(status, status), fill, ink, width=18 * mm),
            Paragraph(f"<font color='{colour}'><b>{_text(outcome)}</b></font>", STYLES["cell"]),
            Paragraph(_date(document.uploaded_at), STYLES["cell_right"]),
        ])
    return [_data_table(
        [_th("File"), _th("Type"), _th("Status"), _th("Routing outcome"), _th("Uploaded", right=True)],
        rows,
        [file_width, *widths],
        gutter=gutter,
    )]


def _notes_section(data: ReportData) -> list[Flowable]:
    if data.notes is None:
        return [_notice("The case's notes could not be loaded", "Try downloading the report again.", "warning")]
    notes = [n for n in data.notes if n.deleted_at is None]
    filenames = {d.id: d.filename for d in data.documents}
    out: list[Flowable] = []
    for note in notes:
        about = f" · on {_text(filenames[note.document_id])}" if note.document_id in filenames else ""
        reply = "Reply from " if note.parent_id else ""
        header = Paragraph(
            f"<b>{reply}{_text(_officer(note.author))}</b>"
            f"<font color='{SOFT}'> · {_when(note.created_at)}{about}{' · edited' if note.edited_at else ''}</font>",
            STYLES["small"],
        )
        body = Paragraph(_text(note.body), STYLES["body"])
        indent = 8 * mm if note.parent_id else 0
        # One row, so a note never splits across pages without needing a KeepTogether of its own: nested inside
        # the section heading's KeepTogether, that pushed the notes a page too far and left a blank page.
        box = Table([[[header, Spacer(1, 2), body]]], colWidths=[CONTENT_WIDTH - indent])
        box.setStyle(TableStyle([
            ("LINEBEFORE", (0, 0), (0, -1), 2, STEEL if not note.parent_id else PANEL_LINE),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        wrapper = Table([[box]], colWidths=[CONTENT_WIDTH])
        wrapper.setStyle(TableStyle([
            ("LEFTPADDING", (0, 0), (-1, -1), indent),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        out.append(wrapper)
    return out


# --- page furniture --------------------------------------------------------------------------------------------


def _page_furniture(report: ReportData, total_pages: int | None, pages_seen: list[int]):
    """Draws the header band and footer on each page. "Page n of N" needs N, which only the first build learns."""
    case_label = f"{report.summary.greenlink_case_id} · {report.summary.company_name or 'Unnamed company'}"
    footer = f"Confidential · Generated {_when(report.generated_at)} by {_officer(report.generated_by)}"

    def draw(canvas, doc):
        canvas.saveState()
        # A navy band across the top, the app's brand on it.
        canvas.setFillColor(INK)
        canvas.rect(0, PAGE_HEIGHT - 12 * mm, PAGE_WIDTH, 12 * mm, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(MARGIN, PAGE_HEIGHT - 7.5 * mm, "Green Link Digital Bank")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(PAGE_WIDTH - MARGIN, PAGE_HEIGHT - 7.5 * mm, case_label[:90])
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, 14 * mm, PAGE_WIDTH - MARGIN, 14 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(INK_SOFT)
        canvas.drawString(MARGIN, 10 * mm, footer)
        page = canvas.getPageNumber()
        pages_seen.append(page)
        if total_pages is not None:
            canvas.drawRightString(PAGE_WIDTH - MARGIN, 10 * mm, f"Page {page} of {total_pages}")
        canvas.restoreState()

    return draw


def render(data: ReportData) -> bytes:
    """The report as PDF bytes: built once to count the pages, then again with "Page n of N" in the footer."""
    pages_seen: list[int] = []
    _build(data, None, pages_seen)
    return _build(data, max(pages_seen, default=1), [])


def _story(data: ReportData) -> list[Flowable]:
    completed = data.completed
    # (title, note beside the title, body, starts on a new page)
    sections: list[tuple[str, str | None, list[Flowable], bool]] = []
    # Without a finished analysis these would only repeat the status banner's message, so they are left out.
    if completed is not None:
        sections += [
            ("Summary", None, _summary_section(completed), False),
            ("Risk Assessment", _when(completed.finished_at), _risk_section(completed.risk), False),
            ("Cross-Document Analysis", _plural(len(completed.findings), "finding"), _cross_document_section(completed), False),
            ("Additional Documents to Request", None, _additional_documents_section(completed, data.summary), False),
        ]
    sections += [
        ("Document Checklist", None, _checklist_section(data), False),
        ("Documents", _plural(len(data.documents), "document"), _documents_section(data), False),
    ]
    # Shown only when there is something in it (or the notes couldn't be read, which the reader should know).
    if data.notes is None or any(n.deleted_at is None for n in data.notes):
        sections.append(("Officer Notes", None, _notes_section(data), True))

    story: list[Flowable] = [*_title_block(data), _stat_cards(data), Spacer(1, 10), _analysis_status_banner(data)]
    for number, (title, note, body, new_page) in enumerate(sections, start=1):
        story += [PageBreak()] if new_page else [Spacer(1, 16), CondPageBreak(60 * mm)]
        # The heading stays with the section's first block, never alone at the foot of a page.
        story.append(KeepTogether([SectionHeading(number, title, note), Spacer(1, 6), body[0]]))
        story += body[1:]
    return story


def _build(data: ReportData, total_pages: int | None, pages_seen: list[int]) -> bytes:
    buffer = io.BytesIO()
    doc = BaseDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=20 * mm, bottomMargin=20 * mm,
        title=f"{data.summary.greenlink_case_id} Case Report",
        author="Green Link Digital Bank", subject=data.summary.company_name or "",
    )
    # No inner padding, so full-width tables line up with the headings and text instead of overhanging them.
    frame = Frame(
        doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="content",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    onpage = _page_furniture(data, total_pages, pages_seen)
    doc.addPageTemplates([PageTemplate(id="report", frames=[frame], onPage=onpage)])
    # Flowables keep layout state, so each build gets a fresh story.
    doc.build(_story(data))
    return buffer.getvalue()
