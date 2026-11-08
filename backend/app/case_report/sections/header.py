"""The top of the first page: the title panel, the four stat cards and the analysis status banner."""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Flowable, Paragraph, Spacer, Table, TableStyle

from ...models import APPROVED_STATUSES
from ..components import _Chip, _notice, _StatCard
from ..data import ReportData, analysis_state, manual_review_count
from ..formatting import _date, _plural, _stage_label, _text, _when
from ..theme import AMBER, CASE_STAGE_COLOURS, CONTENT_WIDTH, INK, RED, RISK_COLOURS, STEEL, STYLES, TEAL

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
