"""The sections about the case itself, shown whether or not it has been analysed: checklist, documents, notes."""

from __future__ import annotations

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, Spacer, Table, TableStyle

from ...labels import document_type_label, requirement_label
from ...models import ADDITIONAL_DOCUMENT_TYPES, REQUIRED_DOCUMENT_TYPES
from ..components import _check_mark, _data_table, _notice, _pill, _th
from ..data import ReportData, _outcome
from ..formatting import REQUIREMENT_ORDER, STATUS_LABELS, _date, _officer, _text, _when, _wrap_filename
from ..theme import (
    BLUE,
    BLUE_BG,
    CONTENT_WIDTH,
    INK_SOFT,
    PANEL,
    PANEL_LINE,
    RED,
    RED_BG,
    SOFT,
    STEEL,
    STYLES,
    TEAL,
    TEAL_BG,
    _hex,
)

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
