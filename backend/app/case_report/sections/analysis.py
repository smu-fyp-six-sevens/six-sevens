"""The sections that come from a finished case analysis: summary, risk, cross-document findings, documents to request."""

from __future__ import annotations

from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Flowable, Paragraph, Spacer, Table, TableStyle

from ...case_pipeline.models import CaseAnalysis
from ...labels import document_type_label
from ...models import CaseRisk, CaseSummary
from ..components import NoticeTone, RiskGauge, _data_table, _Mark, _notice, _risk_pill, _severity_pill, _th
from ..formatting import CATEGORY_LABELS, _check, _plural, _text
from ..theme import (
    BLUE,
    CONTENT_WIDTH,
    PANEL,
    PANEL_LINE,
    RISK_COLOURS,
    SEVERITY_MARKS,
    SOFT,
    STYLES,
    _hex,
    _SEVERITY_RANK,
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
