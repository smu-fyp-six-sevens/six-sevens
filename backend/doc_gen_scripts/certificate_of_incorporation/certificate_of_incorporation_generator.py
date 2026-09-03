"""
Generates a Certificate of Incorporation template PDF with a fixed structure
for later value injection.
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


def generate_certificate_of_incorporation(
    output_path: str,
    *,
    company_name: str,
    company_number: str,
    incorporation_date: date,
    jurisdiction: str = "Republic of Singapore",
    registered_office_address: list[str] | None = None,
    company_type: str = "Private Company Limited by Shares",
    issued_by: str = "Accounting and Corporate Regulatory Authority",
    certificate_no: str = "COI-2026-000001",
    share_capital: str = "SGD 100,000",
    directors: list[str] | None = None,
) -> dict:
    s = _styles()
    registered_office_address = registered_office_address or ["3 Anson Road, #10-01", "Springleaf Tower, Singapore 079909"]
    directors = directors or ["Tan Wei Ming", "Lau Jia En"]

    story: list = []
    story.append(Paragraph("CERTIFICATE OF INCORPORATION", s["title"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(issued_by, s["subtitle"]))
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        f"This is to certify that the company named below was incorporated under the laws of the {jurisdiction}.",
        s["body"],
    ))
    story.append(Spacer(1, 4 * mm))

    summary = Table(
        [[Paragraph("Company Name", s["body_bold"]), Paragraph(company_name, s["body"])],
         [Paragraph("Company Registration Number", s["body_bold"]), Paragraph(company_number, s["body"])],
         [Paragraph("Date of Incorporation", s["body_bold"]), Paragraph(incorporation_date.strftime("%d %B %Y"), s["body"])],
         [Paragraph("Company Type", s["body_bold"]), Paragraph(company_type, s["body"])],
         [Paragraph("Certificate No.", s["body_bold"]), Paragraph(certificate_no, s["body"])],
         [Paragraph("Share Capital", s["body_bold"]), Paragraph(share_capital, s["body"])],],
        colWidths=[55 * mm, 95 * mm],
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C8D1DB")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]),
    )
    story.append(summary)
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("Registered Office Address", s["body_bold"]))
    for line in registered_office_address:
        story.append(Paragraph(line, s["body"]))
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("Directors", s["body_bold"]))
    story.append(Paragraph(", ".join(directors), s["body"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "This certificate is issued as a template and may be populated later with actual incorporation data.",
        s["small"],
    ))

    _build_pdf(output_path, story)
    return {
        "output_path": output_path,
        "company_name": company_name,
        "company_number": company_number,
    }


if __name__ == "__main__":
    import os

    os.makedirs("/home/claude/loan_letters/output", exist_ok=True)
    print(generate_certificate_of_incorporation(
        "/home/claude/loan_letters/output/certificate_of_incorporation_template.pdf",
        company_name="Greenlink Digital Bank Pte Ltd",
        company_number="202612345W",
        incorporation_date=date(2026, 1, 12),
        registered_office_address=["3 Anson Road, #10-01", "Springleaf Tower, Singapore 079909"],
        directors=["Tan Wei Ming", "Lau Jia En"],
    ))
