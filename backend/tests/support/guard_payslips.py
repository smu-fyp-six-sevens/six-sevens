"""
Payslips for the content guard suites (Sprint 5, SCRUM-288): a genuine one, or one carrying the test case's text
hidden in white, set in its document info, or drawn into a picture on the page; and the document info exiftool
reports for a PDF, read with PyMuPDF so the suites don't need exiftool installed.
"""
from __future__ import annotations

import pymupdf

PAYSLIP_LINES = [
    "PAYSLIP - OCTOBER 2026",
    "Employer: Brightwave Analytics Pte Ltd",
    "Employee: Heng Wan Ting   NRIC: S8812345F",
    "Basic salary          SGD 5,200.00",
    "Employee CPF          SGD 1,040.00",
    "Net pay               SGD 4,160.00",
]


def payslip_pdf(*, hidden: str | None = None, metadata: dict[str, str] | None = None, image_text: str | None = None) -> bytes:
    """A one-page payslip, with the test case's text hidden in white, set in its document info, or drawn into a
    picture on the page."""
    doc = pymupdf.open()
    page = doc.new_page()
    for row, line in enumerate(PAYSLIP_LINES):
        page.insert_text((60, 80 + row * 20), line, fontsize=11)
    if hidden:
        page.insert_text((60, 260), hidden, fontsize=9, color=(1, 1, 1))
    if image_text:
        note = pymupdf.open()
        picture = note.new_page(width=pymupdf.get_text_length(image_text, fontsize=12) + 24, height=40)
        picture.insert_text((12, 26), image_text, fontsize=12)
        page.insert_image(pymupdf.Rect(60, 300, 560, 340), pixmap=picture.get_pixmap(dpi=150))
    if metadata:
        doc.set_metadata({key.lower(): value for key, value in metadata.items()})
    return doc.tobytes()


def exiftool_metadata(file_bytes: bytes, filename: str) -> dict:
    """The document info fields exiftool reports for a PDF (Title, Subject, Author...)."""
    info = pymupdf.open(stream=file_bytes, filetype="pdf").metadata or {}
    return {key[:1].upper() + key[1:]: value for key, value in info.items() if value}
