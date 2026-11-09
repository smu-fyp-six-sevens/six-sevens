"""
The case report a loan officer downloads as a PDF (GET /cases/{id}/report.pdf): the case's risk, the case
analysis's summary and findings, the recommended additional documents, the checklist, the documents and the notes.

gather() reads everything the report shows; render() lays it out, so the layout is testable without a database.
The sections that come from the case analysis appear only once an analysis has finished; until then a banner
says why (not run yet, still running, or did not finish). Officer Notes appears only when the case has notes.

The package is split by concern:

- data.py: gather() and the rules the report applies to what it reads (routing outcome, analysis state).
- theme.py: the palette, paragraph styles and page geometry.
- formatting.py: labels and the helpers that turn values into report text.
- components.py: the drawn building blocks (headings, pills, notices, tables, cards).
- sections/: one function per part of the report (header, analysis sections, case sections).
- pdf.py: render(), which lays the sections out on pages with the header and footer.
"""

from .data import (
    AnalysisState,
    ReportData,
    analysis_state,
    filename_for,
    gather,
    manual_review_count,
    routing_outcome,
)
from .pdf import render

__all__ = [
    "AnalysisState",
    "ReportData",
    "analysis_state",
    "filename_for",
    "gather",
    "manual_review_count",
    "render",
    "routing_outcome",
]
