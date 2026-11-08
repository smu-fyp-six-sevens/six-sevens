"""The report's labels and the helpers that turn values into its text (escaped for Paragraph markup)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from xml.sax.saxutils import escape

from reportlab.pdfbase.pdfmetrics import stringWidth

# Singapore has no daylight saving, so a fixed offset is exact and needs no tz database in the image.
SGT = timezone(timedelta(hours=8), "SGT")

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
    "pending_evaluation": "Pending Evaluation",
    "case_analysed": "Ready to Finalise",
    "finalised": "Finalised",
    "failed": "Failed",
}

def _stage_label(stage: str) -> str:
    return CASE_STAGE_LABELS.get(stage, stage)
# Same as the header (frontend lib/officers.ts); a username not listed is shown as it is.
OFFICER_NAMES = {"greenlink-tester": "J. Tan", "demo": "K. Lim", "bob": "B. Smith", "carol": "C. Wong"}


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
