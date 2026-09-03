"""
Generates a passport template PDF with fixed, field-driven layout so the
same structure can later be populated with real values.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate, Paragraph, Spacer, Table, TableStyle


styles = getSampleStyleSheet()
FONT = "Helvetica"


def _styles():
    return {
        "title": ParagraphStyle("title", fontName=f"{FONT}-Bold", fontSize=16, leading=18, alignment=1),
        "subtitle": ParagraphStyle("subtitle", fontName=FONT, fontSize=9, leading=11, alignment=1),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=9, leading=11),
        "body_bold": ParagraphStyle("body_bold", fontName=f"{FONT}-Bold", fontSize=9, leading=11),
        "small": ParagraphStyle("small", fontName=FONT, fontSize=7.5, leading=9),
    }


def _build_pdf(output_path: str, story: list, *, producer: str = "DocuSuite Enterprise 4.2") -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    doc = BaseDocTemplate(
        str(tmp_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="template", frames=[frame])])
    doc.build(story)

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({"/Producer": producer, "/Creator": producer, "/CreationDate": "D:20260315090000"})
    with open(output_path, "wb") as f:
        writer.write(f)
    tmp_path.unlink(missing_ok=True)


def _table(rows: list[list[str]], widths: list[float]) -> Table:
    s = _styles()
    return Table(
        [[Paragraph(c, s["body"])] for c in []]
    )


def _field_table(rows: list[tuple[str, str]], widths: list[float]) -> Table:
    s = _styles()
    return Table(
        [[Paragraph(label, s["body_bold"]), Paragraph(value, s["body"])] for label, value in rows],
        colWidths=widths,
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C8D1DB")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]),
    )


def generate_passport_page(
    output_path: str,
    *,
    passport_no: str,
    surname: str,
    given_names: str,
    nationality: str,
    date_of_birth: date,
    place_of_birth: str,
    sex: str,
    date_of_issue: date,
    date_of_expiry: date,
    holder_signature: str = "________________",
    mrz_line_1: str = "P<SGP<SAMPLE<<HOLDER<<<<<<<<<<<<<<<<<<<<<<",
    mrz_line_2: str = "A12345678SGP8001019M3201012<<<<<<<<<<<<<<04",
    issue_authority: str = "Immigration & Checkpoints Authority",
    personal_number: str = "",
) -> dict:
    s = _styles()
    story: list = []

    story.append(Paragraph("REPUBLIC OF SINGAPORE", s["subtitle"]))
    story.append(Paragraph("PASSPORT", s["title"]))
    story.append(Spacer(1, 4 * mm))

    story.append(_field_table([
        ("Passport No.", passport_no),
        ("Surname", surname),
        ("Given Names", given_names),
        ("Nationality", nationality),
        ("Date of Birth", date_of_birth.strftime("%d %b %Y")),
        ("Place of Birth", place_of_birth),
        ("Sex", sex),
        ("Date of Issue", date_of_issue.strftime("%d %b %Y")),
        ("Date of Expiry", date_of_expiry.strftime("%d %b %Y")),
        ("Issuing Authority", issue_authority),
        ("Personal Number", personal_number),
    ], [42 * mm, 98 * mm]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Holder Signature", s["body_bold"]))
    story.append(Paragraph(holder_signature, s["body"]))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Machine Readable Zone", s["body_bold"]))
    story.append(Paragraph(mrz_line_1, s["small"]))
    story.append(Paragraph(mrz_line_2, s["small"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Template fields are intentionally fixed for later injection.", s["small"]))

    _build_pdf(output_path, story)
    return {
        "output_path": output_path,
        "passport_no": passport_no,
    }


if __name__ == "__main__":
    import os

    os.makedirs("/home/claude/loan_letters/output", exist_ok=True)
    print(generate_passport_page(
        "/home/claude/loan_letters/output/passport_template.pdf",
        passport_no="E1234567A",
        surname="Tan",
        given_names="Wei Ming",
        nationality="Singapore",
        date_of_birth=date(1980, 1, 1),
        place_of_birth="Singapore",
        sex="M",
        date_of_issue=date(2024, 1, 15),
        date_of_expiry=date(2034, 1, 14),
    ))
