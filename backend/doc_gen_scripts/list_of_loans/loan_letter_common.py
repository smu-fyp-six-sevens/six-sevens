"""
Shared building blocks for loan facility offer letters (EFS, Overdraft, Term
Loan). Each facility-specific generator imports from here and supplies only
its own terms table and body content — keeps the three generator scripts
from duplicating letterhead/signature/metadata logic.
"""

import io
from datetime import date, datetime

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, HRFlowable,
)

styles = getSampleStyleSheet()
FONT = "Helvetica"


def format_currency(amount: float) -> str:
    return f"SGD {amount:,.2f}"


def build_styles():
    return {
        "bank_name": ParagraphStyle("bank_name", fontName=f"{FONT}-Bold", fontSize=15,
                                     textColor=colors.HexColor("#1F3A5F")),
        "bank_addr": ParagraphStyle("bank_addr", fontName=FONT, fontSize=8,
                                     textColor=colors.HexColor("#444444"), alignment=2),
        "small": ParagraphStyle("small", fontName=FONT, fontSize=9, leading=13),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=9.5, leading=14, spaceAfter=6),
        "heading": ParagraphStyle("heading", fontName=f"{FONT}-Bold", fontSize=11, spaceBefore=8, spaceAfter=6),
        "table_label": ParagraphStyle("table_label", fontName=f"{FONT}-Bold", fontSize=9),
        "table_value": ParagraphStyle("table_value", fontName=FONT, fontSize=9),
        "bullet": ParagraphStyle("bullet", fontName=FONT, fontSize=8.5, leading=12),
        "sig": ParagraphStyle("sig", fontName=FONT, fontSize=9.5, leading=14),
    }


def letterhead(bank_name: str, bank_address_lines: list, ref_no: str, offer_date: date):
    s = build_styles()
    header = Table(
        [[Paragraph(bank_name, s["bank_name"]), Paragraph("<br/>".join(bank_address_lines), s["bank_addr"])]],
        colWidths=[90 * mm, 80 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    confidential = Paragraph("PRIVATE &amp; CONFIDENTIAL", s["small"])
    meta = Paragraph(
        f"Our Ref: {ref_no}<br/>Date: {offer_date.strftime('%d %B %Y')}<br/>"
        "Subject to execution of final facility documentation", s["small"]
    )
    return [confidential, Spacer(1, 2 * mm), header, Spacer(1, 4 * mm), meta, Spacer(1, 4 * mm),
            HRFlowable(width="100%", thickness=0.75, color=colors.black), Spacer(1, 4 * mm)]


def addressee_block(company_name: str, address_lines: list, attn_name: str):
    s = build_styles()
    return [
        Paragraph(f"<b>{company_name}</b><br/>" + "<br/>".join(address_lines), s["body"]),
        Spacer(1, 3 * mm),
        Paragraph(f"Attention: {attn_name}", s["body"]),
        Spacer(1, 3 * mm),
    ]


def terms_table(rows: list):
    """rows: list of (label, value) tuples."""
    s = build_styles()
    data = [[Paragraph(label, s["table_label"]), Paragraph(str(value), s["table_value"])] for label, value in rows]
    t = Table(data, colWidths=[55 * mm, 105 * mm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#999999")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def terms_and_conditions(bullets: list):
    s = build_styles()
    bullet_style = ParagraphStyle("bullet_spaced", parent=s["bullet"], spaceAfter=5)
    combined = "<br/><br/>".join(f"{idx}.&nbsp;&nbsp;{b}" for idx, b in enumerate(bullets, start=1))
    return Paragraph(combined, bullet_style)


def signature_block(bank_name: str, signatory_name: str, signatory_title: str,
                     borrower_director_name: str):
    s = build_styles()
    return [
        Spacer(1, 6 * mm),
        Paragraph("Yours faithfully,", s["sig"]),
        Paragraph(f"For and on behalf of<br/><b>{bank_name}</b>", s["sig"]),
        Spacer(1, 10 * mm),
        Paragraph("_________________________", s["sig"]),
        Paragraph(f"{signatory_name}<br/>{signatory_title}", s["sig"]),
        Spacer(1, 10 * mm),
        HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#999999")),
        Spacer(1, 4 * mm),
        Paragraph("<b>Acceptance</b>", s["heading"]),
        Paragraph(
            "We, the undersigned, being duly authorised, accept the above terms and conditions "
            "on behalf of the Borrower.", s["body"],
        ),
        Spacer(1, 8 * mm),
        Paragraph("_________________________", s["sig"]),
        Paragraph(f"{borrower_director_name}<br/>Director<br/>Date: _______________", s["sig"]),
    ]


def build_pdf(output_path: str, story: list, *, producer: str = "DocuSuite Enterprise 4.2",
              creation_date: str | None = None):
    """
    Assembles the PDF and applies the mandatory metadata override (never
    leave a PDF library's default Producer/Creator signature in place —
    same convention as every other generator in this dataset).
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
    )
    doc.build(story)
    buffer.seek(0)

    reader = PdfReader(buffer)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    if creation_date is None:
        creation_date = datetime.now().strftime("D:%Y%m%d%H%M%S")
    writer.add_metadata({"/Producer": producer, "/Creator": producer, "/CreationDate": creation_date})
    with open(output_path, "wb") as f:
        writer.write(f)
