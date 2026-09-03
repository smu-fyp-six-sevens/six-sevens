"""
Generates a source-of-wealth balance sheet PDF template based on the
attached sample. The layout is intentionally template-like: the same
statement structure can later be populated with different values without
changing the document flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


styles = getSampleStyleSheet()
FONT = "Helvetica"


@dataclass(frozen=True)
class BalanceSheetLine:
    label: str
    note: str = ""
    fy2026: str | float | int = ""
    fy2025: str | float | int = ""
    section: bool = False


def _styles():
    return {
        "title": ParagraphStyle(
            "title", fontName=f"{FONT}-Bold", fontSize=15, leading=18, alignment=1
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName=FONT, fontSize=9, leading=11, alignment=1
        ),
        "body": ParagraphStyle(
            "body", fontName=FONT, fontSize=9, leading=12
        ),
        "body_center": ParagraphStyle(
            "body_center", fontName=FONT, fontSize=9, leading=12, alignment=1
        ),
        "section": ParagraphStyle(
            "section", fontName=f"{FONT}-Bold", fontSize=9.5, leading=12, spaceBefore=3
        ),
        "small": ParagraphStyle(
            "small", fontName=FONT, fontSize=8, leading=10
        ),
        "note": ParagraphStyle(
            "note", fontName=FONT, fontSize=8.2, leading=10
        ),
    }


def _money(value: str | float | int) -> str:
    if value == "":
        return ""
    if isinstance(value, str):
        return value
    return f"${value:,.2f}"


def _build_pdf(output_path: str, story: list, *, producer: str = "DocuSuite Enterprise 4.2") -> None:
    buffer_path = Path(output_path).with_suffix(".tmp.pdf")
    doc = BaseDocTemplate(
        str(buffer_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="template", frames=[frame])])
    doc.build(story)

    reader = PdfReader(str(buffer_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({"/Producer": producer, "/Creator": producer, "/CreationDate": "D:20260315090000"})
    with open(output_path, "wb") as f:
        writer.write(f)
    buffer_path.unlink(missing_ok=True)


def _statement_rows(lines: list[BalanceSheetLine]) -> list[list[str]]:
    rows: list[list[str]] = [["Note", "fy2026", "fy2025"]]
    for line in lines:
        if line.section:
            rows.append([line.label, "", ""])
        else:
            rows.append([line.note, _money(line.fy2026), _money(line.fy2025)])
    return rows


def generate_balance_sheet(
    output_path: str,
    *,
    company_name: str,
    company_uen: str,
    statement_date: date,
    director_name: str,
    fy2026_profit: float,
    fy2025_profit: float,
    asset_lines: list[BalanceSheetLine] | None = None,
    liability_lines: list[BalanceSheetLine] | None = None,
    note_texts: dict[str, str] | None = None,
) -> dict:
    s = _styles()
    story: list = []

    asset_lines = asset_lines or [
        BalanceSheetLine("ASSETS", section=True),
        BalanceSheetLine("Non-current assets", section=True),
        BalanceSheetLine("Property, plant and equipment", "6", 486200, 512300),
        BalanceSheetLine("Intangible assets", "9", 24000, 30000),
        BalanceSheetLine("Inventories", "10", 312400, 289600),
        BalanceSheetLine("Trade receivables", "11", 458700, 402100),
        BalanceSheetLine("Other receivables and prepayments", "", 36800, 31200),
        BalanceSheetLine("Cash and bank balances", "12", 184930, 187450),
        BalanceSheetLine("Total non-current assets", "", 510200, 542300),
        BalanceSheetLine("Current assets", section=True),
        BalanceSheetLine("Total current assets", "", 992830, 910350),
        BalanceSheetLine("TOTAL ASSETS", "", 1503030, 1452650),
    ]
    liability_lines = liability_lines or [
        BalanceSheetLine("LIABILITIES", section=True),
        BalanceSheetLine("Non-current liabilities", section=True),
        BalanceSheetLine("Total non-current liabilities", "", 228400, 269700),
        BalanceSheetLine("Current liabilities", section=True),
        BalanceSheetLine("Total current liabilities", "", 455000, 408400),
        BalanceSheetLine("TOTAL LIABILITIES", "", 683400, 678100),
        BalanceSheetLine("EQUITY", section=True),
        BalanceSheetLine("Share capital", "14", 100000, 100000),
        BalanceSheetLine("Retained earnings, beginning of year", "", 674550, ""),
        BalanceSheetLine("Add: Profit for the year (per Income Statement)", "", fy2026_profit, fy2025_profit),
        BalanceSheetLine("Retained earnings, end of year", "", 719630, 674550),
        BalanceSheetLine("Total equity", "", 819630, 774550),
        BalanceSheetLine("TOTAL EQUITY AND LIABILITIES", "", 1503030, 1452650),
    ]
    note_texts = note_texts or {
        "6": "Property, plant and equipment comprise office equipment, furniture and fittings, renovation, and motor vehicles used in the Company's trading operations, stated at cost less accumulated depreciation.",
        "9": "Intangible assets comprise computer software licences, amortised on a straight-line basis over their estimated useful lives.",
        "10": "Inventories are stated at the lower of cost and net realisable value. Cost is determined on a first-in-first-out basis.",
        "11": "Trade receivables are amounts due from customers in the ordinary course of business, stated net of allowance for expected credit losses.",
        "12": "Cash and bank balances comprise cash on hand and balances with banks, including the Company's operating accounts used for day-to-day transactions.",
        "13": "Bank borrowings comprise a term loan facility, secured against the Company's property, plant and equipment, repayable in monthly instalments over the facility tenure.",
        "14": "Share capital comprises fully paid ordinary shares, with no par value, issued by the Company.",
    }

    story.append(Paragraph(company_name, s["title"]))
    story.append(Paragraph(f"(UEN: {company_uen})", s["body_center"]))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("STATEMENT OF FINANCIAL POSITION", s["title"]))
    story.append(Paragraph(f"AS AT {statement_date.strftime('%d %B %Y').upper()}", s["subtitle"]))
    story.append(Paragraph("(All amounts stated in Singapore Dollars, unless otherwise indicated)", s["subtitle"]))
    story.append(Spacer(1, 5 * mm))

    table_rows = []
    for line in asset_lines:
        if line.section:
            table_rows.append([Paragraph(line.label, s["section"]), "", ""])
        else:
            table_rows.append([
                Paragraph(line.label, s["body"]),
                Paragraph(line.note, s["body"]),
                Paragraph(_money(line.fy2026), s["body"]),
                Paragraph(_money(line.fy2025), s["body"]),
            ])
    # Add liability rows after the first table to better mirror the original flow.
    story.append(Table(
        [[Paragraph("Description", s["section"]), Paragraph("Note", s["section"]), Paragraph("fy2026", s["section"]), Paragraph("fy2025", s["section"])]],
        colWidths=[83 * mm, 14 * mm, 30 * mm, 30 * mm],
        style=TableStyle([
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 4),
        ]),
    ))
    story.append(Table(
        table_rows,
        colWidths=[83 * mm, 14 * mm, 30 * mm, 30 * mm],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 7), (-1, 7), 0.5, colors.HexColor("#999999")),
            ("LINEBELOW", (0, 9), (-1, 9), 0.5, colors.HexColor("#999999")),
            ("LINEBELOW", (0, 10), (-1, 10), 0.75, colors.black),
        ]),
    ))
    story.append(Spacer(1, 5 * mm))

    liabilities_table_rows = []
    for line in liability_lines:
        if line.section:
            liabilities_table_rows.append([Paragraph(line.label, s["section"]), "", "", ""])
        else:
            liabilities_table_rows.append([
                Paragraph(line.label, s["body"]),
                Paragraph(line.note, s["body"]),
                Paragraph(_money(line.fy2026), s["body"]),
                Paragraph(_money(line.fy2025), s["body"]),
            ])
    story.append(Table(
        liabilities_table_rows,
        colWidths=[83 * mm, 14 * mm, 30 * mm, 30 * mm],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LINEBELOW", (0, 2), (-1, 2), 0.5, colors.HexColor("#999999")),
            ("LINEBELOW", (0, 4), (-1, 4), 0.5, colors.HexColor("#999999")),
            ("LINEBELOW", (0, 5), (-1, 5), 0.75, colors.black),
        ]),
    ))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph("The accompanying notes form an integral part of these financial statements.", s["small"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Director", s["body"]))
    story.append(Paragraph("ON BEHALF OF THE BOARD OF DIRECTORS", s["section"]))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph("_________________________", s["body_center"]))
    story.append(PageBreak())

    story.append(Paragraph(company_name, s["title"]))
    story.append(Paragraph("NOTES TO THE FINANCIAL STATEMENTS", s["title"]))
    story.append(Paragraph(f"AS AT {statement_date.strftime('%d %B %Y').upper()}", s["subtitle"]))
    story.append(Spacer(1, 5 * mm))
    for note_no, heading in [
        ("6", "PROPERTY, PLANT AND EQUIPMENT"),
        ("9", "INTANGIBLE ASSETS"),
        ("10", "INVENTORIES"),
        ("11", "TRADE RECEIVABLES"),
        ("12", "CASH AND BANK BALANCES"),
        ("13", "BANK BORROWINGS"),
        ("14", "SHARE CAPITAL"),
    ]:
        story.append(Paragraph(f"{note_no}. {heading}", s["section"]))
        story.append(Paragraph(note_texts[note_no], s["body"]))
        story.append(Spacer(1, 2 * mm))

    build_path = output_path
    _build_pdf(build_path, story)
    return {
        "output_path": build_path,
        "company_name": company_name,
        "statement_date": statement_date.isoformat(),
    }


if __name__ == "__main__":
    import os

    os.makedirs("/home/claude/loan_letters/output", exist_ok=True)

    result = generate_balance_sheet(
        "/home/claude/loan_letters/output/source_of_wealth_balance_sheet.pdf",
        company_name="GOLDEN HARVEST TRADING PTE LTD",
        company_uen="201812345A",
        statement_date=date(2025, 12, 31),
        director_name="Tan Wei Ming",
        fy2026_profit=45080,
        fy2025_profit=41360,
    )
    print("Balance sheet:", result)
