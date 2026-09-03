"""
Generates a payslip PDF template with fixed payroll sections and placeholder
fields. The body includes the canonical cues used by the classifier so the
result remains recognisable while still being fully parameterised.
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
        "title": ParagraphStyle("title", fontName=f"{FONT}-Bold", fontSize=15, leading=18, alignment=1),
        "subtitle": ParagraphStyle("subtitle", fontName=FONT, fontSize=9, leading=11, alignment=1),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=8.8, leading=10.8),
        "body_bold": ParagraphStyle("body_bold", fontName=f"{FONT}-Bold", fontSize=8.8, leading=10.8),
        "small": ParagraphStyle("small", fontName=FONT, fontSize=7.5, leading=9),
    }


def _build_pdf(output_path: str, story: list, *, producer: str = "DocuSuite Enterprise 4.2") -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    doc = BaseDocTemplate(
        str(tmp_path),
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
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


def generate_payslip(
    output_path: str,
    *,
    employer_name: str,
    employee_name: str,
    employee_id: str,
    pay_period: str,
    payment_date: date,
    basic_salary: float,
    allowances: list[tuple[str, float]] | None = None,
    deductions: list[tuple[str, float]] | None = None,
    overtime_pay: float = 0.0,
    bonus: float = 0.0,
    cpf_employee: float = 0.0,
    cpf_employer: float = 0.0,
    bank_account_last4: str = "0158",
    currency: str = "SGD",
) -> dict:
    s = _styles()
    story: list = []
    allowances = allowances or [("Transport Allowance", 120.00), ("Meal Allowance", 80.00)]
    deductions = deductions or [("Employee CPF", cpf_employee), ("Other Deduction", 25.00)]

    gross_pay = basic_salary + overtime_pay + bonus + sum(v for _, v in allowances)
    total_deductions = sum(v for _, v in deductions)
    net_pay = gross_pay - total_deductions

    story.append(Paragraph("PAYSLIP", s["title"]))
    story.append(Paragraph(employer_name, s["subtitle"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Table(
        [[Paragraph("Employee", s["body_bold"]), Paragraph(employee_name, s["body"]), Paragraph("Employee ID", s["body_bold"]), Paragraph(employee_id, s["body"])],
         [Paragraph("Pay Period", s["body_bold"]), Paragraph(pay_period, s["body"]), Paragraph("Payment Date", s["body_bold"]), Paragraph(payment_date.strftime("%d %b %Y"), s["body"])]],
        colWidths=[32 * mm, 55 * mm, 30 * mm, 45 * mm],
        style=TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C8D1DB")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")), ("VALIGN", (0, 0), (-1, -1), "TOP")]),
    ))
    story.append(Spacer(1, 4 * mm))

    earnings_rows = [["Earnings", "Amount"]]
    earnings_rows.append(["Basic Salary", f"{currency} {basic_salary:,.2f}"])
    for label, amount in allowances:
        earnings_rows.append([label, f"{currency} {amount:,.2f}"])
    earnings_rows.append(["Overtime Pay", f"{currency} {overtime_pay:,.2f}"])
    earnings_rows.append(["Bonus", f"{currency} {bonus:,.2f}"])
    earnings_rows.append(["Gross Pay", f"{currency} {gross_pay:,.2f}"])

    deductions_rows = [["Deductions", "Amount"]]
    for label, amount in deductions:
        deductions_rows.append([label, f"{currency} {amount:,.2f}"])
    deductions_rows.append(["Total Deductions", f"{currency} {total_deductions:,.2f}"])
    deductions_rows.append(["Net Pay", f"{currency} {net_pay:,.2f}"])

    def _section_table(rows: list[list[str]]) -> Table:
        return Table(
            [[Paragraph(c, s["body_bold"] if idx == 0 else s["body"]) for idx, c in enumerate(row)] for row in rows],
            colWidths=[95 * mm, 50 * mm],
            style=TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C8D1DB")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]),
        )

    story.append(_section_table(earnings_rows))
    story.append(Spacer(1, 4 * mm))
    story.append(_section_table(deductions_rows))
    story.append(Spacer(1, 4 * mm))

    summary = Table(
        [[Paragraph("Bank Account", s["body_bold"]), Paragraph(f"Salary credited to account ending {bank_account_last4}", s["body"])],
         [Paragraph("Employee CPF", s["body_bold"]), Paragraph(f"Employer CPF contribution: {currency} {cpf_employer:,.2f}", s["body"])],
         [Paragraph("Net Pay", s["body_bold"]), Paragraph(f"{currency} {net_pay:,.2f}", s["body_bold"])],],
        colWidths=[38 * mm, 107 * mm],
        style=TableStyle([("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#C8D1DB")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7"))]),
    )
    story.append(summary)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("PAYSLIP", s["small"]))
    story.append(Paragraph("Basic Salary / Gross Pay / Net Pay are fixed fields and can be replaced later.", s["small"]))

    _build_pdf(output_path, story)
    return {
        "output_path": output_path,
        "employee_name": employee_name,
        "net_pay": net_pay,
    }


if __name__ == "__main__":
    import os

    os.makedirs("/home/claude/loan_letters/output", exist_ok=True)
    print(generate_payslip(
        "/home/claude/loan_letters/output/payslip_template.pdf",
        employer_name="Greenlink Digital Bank Pte Ltd",
        employee_name="Tan Wei Ming",
        employee_id="S1234567D",
        pay_period="December 2025",
        payment_date=date(2025, 12, 31),
        basic_salary=3000.00,
        allowances=[("Transport Allowance", 120.00), ("Meal Allowance", 80.00)],
        deductions=[("Employee CPF", 620.00), ("Other Deduction", 25.00)],
        overtime_pay=150.00,
        bonus=200.00,
        cpf_employer=900.00,
    ))
