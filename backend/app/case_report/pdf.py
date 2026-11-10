"""render(): the sections laid out on A4 pages, with the header band and the "Page n of N" footer."""

from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Flowable,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Spacer,
)

from .components import SectionHeading
from .data import ReportData
from .formatting import _officer, _plural, _when
from .sections import (
    _additional_documents_section,
    _analysis_status_banner,
    _checklist_section,
    _cross_document_section,
    _documents_section,
    _notes_section,
    _risk_section,
    _stat_cards,
    _summary_section,
    _title_block,
)
from .theme import INK, INK_SOFT, LINE, MARGIN, PAGE_HEIGHT, PAGE_WIDTH

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
