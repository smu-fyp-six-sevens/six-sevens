"""
Generates an OCBC-style bank statement PDF template that visually matches
the reference screenshot: top branding, account block, statement header,
transaction table, summary totals, notices, and footnotes.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas


PAGE_W, PAGE_H = A4
FONT = "Helvetica"


def _save_pdf(output_path: str, draw_fn) -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    c = canvas.Canvas(str(tmp_path), pagesize=A4)
    draw_fn(c)
    c.save()

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({"/Producer": "DocuSuite Enterprise 4.2", "/Creator": "DocuSuite Enterprise 4.2", "/CreationDate": "D:20260315090000"})
    with open(output_path, "wb") as f:
        writer.write(f)
    tmp_path.unlink(missing_ok=True)


def _draw_text(c: canvas.Canvas, x: float, y: float, text: str, *, font: str = FONT, size: float = 9, color=colors.black, bold: bool = False) -> None:
    c.setFillColor(color)
    c.setFont(f"{font}-Bold" if bold else font, size)
    c.drawString(x, y, text)


def _wrap_lines(text: str, width: float, *, font: str = FONT, size: float = 9) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        trial = word if not cur else f"{cur} {word}"
        if pdfmetrics.stringWidth(trial, font, size) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def _draw_multiline(c: canvas.Canvas, x: float, y: float, text: str, *, width: float, font: str = FONT, size: float = 9, leading: float = 11, color=colors.black, bold: bool = False) -> float:
    c.setFillColor(color)
    c.setFont(f"{font}-Bold" if bold else font, size)
    for line in _wrap_lines(text, width, font=f"{font}-Bold" if bold else font, size=size):
        c.drawString(x, y, line)
        y -= leading
    return y


def generate_bank_statement(
    output_path: str,
    *,
    bank_name: str,
    account_holder: str,
    account_number: str,
    statement_number: str,
    statement_period_start: date,
    statement_period_end: date,
    opening_balance: float,
    closing_balance: float,
    available_balance: float,
    branch_code: str,
    account_type: str = "Monthly Savings Account",
    currency: str = "",
    transactions: list[tuple[date, str, float]] | None = None,
) -> dict:
    transactions = transactions or [
        (statement_period_start.replace(day=3), "ATM WITHDRAWAL", -155.00),
        (statement_period_start.replace(day=6), "SUPERMARKET CARD PAYMENT", -685.50),
        (statement_period_start.replace(day=8), "UTILITY BILL PAYMENT", -120.75),
        (statement_period_start.replace(day=18), "PAYROLL CREDIT", 2500.00),
        (statement_period_start.replace(day=22), "ONLINE TRANSFER", 200.00),
        (statement_period_end, "INTEREST CREDIT", 5.45),
    ]

    def draw(c: canvas.Canvas) -> None:
        # page background
        c.setFillColor(colors.white)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        # top branding
        c.setFillColor(colors.HexColor("#e21b23"))
        c.setFont("Helvetica-Bold", 20)
        c.drawString(34, PAGE_H - 56, "OCBC")
        c.setFont("Helvetica", 7.5)
        c.drawRightString(PAGE_W - 42, PAGE_H - 30, "OCBC Bank")
        c.drawRightString(PAGE_W - 42, PAGE_H - 40, "65 Chulia Street, OCBC Centre")
        c.drawRightString(PAGE_W - 42, PAGE_H - 50, "Singapore 049513")

        # account id and address block
        c.setFillColor(colors.black)
        c.setFont("Helvetica", 9)
        c.drawString(36, PAGE_H - 92, "XXXX-0472")
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(36, PAGE_H - 138, account_holder.upper())
        c.setFont("Helvetica", 8.5)
        for i, line in enumerate(["18 ARDMORE PARK #15-02", "SINGAPORE", "259960"]):
            c.drawString(36, PAGE_H - 154 - (i * 14), line)

        # information box
        c.setStrokeColor(colors.HexColor("#777777"))
        c.setFillColor(colors.HexColor("#fbfbfb"))
        c.rect(PAGE_W - 240, PAGE_H - 170, 200, 48, fill=1, stroke=1)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 7)
        c.drawCentredString(PAGE_W - 140, PAGE_H - 138, "Information")
        c.setFont("Helvetica", 6)
        c.drawCentredString(PAGE_W - 140, PAGE_H - 146, "As part of our efforts to be more environmentally friendly bank, all our")
        c.drawCentredString(PAGE_W - 140, PAGE_H - 154, "account statements will now be printed on both sides of this page.")

        # statement heading
        c.setFillColor(colors.black)
        c.rect(PAGE_W - 230, PAGE_H - 226, 135, 28, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 10.5)
        c.drawCentredString(PAGE_W - 162.5, PAGE_H - 208, "STATEMENT OF ACCOUNT")
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.black)
        c.drawRightString(PAGE_W - 42, PAGE_H - 214, "Page 1 of 1")
        c.drawRightString(PAGE_W - 42, PAGE_H - 230, "For enquiries, please call")
        c.drawRightString(PAGE_W - 42, PAGE_H - 240, "our Customer Service Officers at (1800) 363 333")
        c.drawRightString(PAGE_W - 42, PAGE_H - 250, "Orchard Road Branch")

        c.setFont("Helvetica-Bold", 9)
        c.drawString(36, PAGE_H - 218, account_type.upper())
        c.setFont("Helvetica", 8)
        c.drawString(36, PAGE_H - 228, f"Account No. {account_number}")
        c.setLineWidth(0.8)
        c.setStrokeColor(colors.HexColor("#777777"))
        c.line(36, PAGE_H - 240, PAGE_W - 42, PAGE_H - 240)
        c.setFont("Helvetica", 9)
        c.drawString(PAGE_W - 185, PAGE_H - 270, f"{statement_period_start.strftime('%d %b %Y').upper()} TO {statement_period_end.strftime('%d %b %Y').upper()}")

        # transactions table
        table_x = 36
        table_y_top = PAGE_H - 290
        headers = ["Transaction Date", "Value Date", "Description", "Cheque", "Withdrawal", "Deposit", "Balance"]
        widths = [55, 48, 150, 38, 50, 50, 45]
        c.setFont("Helvetica", 6.5)
        header_y = table_y_top
        for i, h in enumerate(headers):
            c.drawString(table_x + sum(widths[:i]), header_y, h)
        c.line(table_x, header_y - 4, PAGE_W - 42, header_y - 4)

        rows = [
            (statement_period_start, statement_period_start, "BALANCE B/F", "", "", "", opening_balance),
            *transactions,
            (statement_period_end, statement_period_end, "Closing Balance", "", "", "", closing_balance),
        ]
        y = header_y - 18
        for row in rows:
            if len(row) == 7 and isinstance(row[0], date):
                tx_date, val_date, desc, chq, withdrawal, deposit, bal = row
                values = [
                    tx_date.strftime("%d %b"),
                    val_date.strftime("%d %b"),
                    desc,
                    chq,
                    f"{withdrawal:,.2f}" if isinstance(withdrawal, (int, float)) and withdrawal > 0 else "",
                    f"{deposit:,.2f}" if isinstance(deposit, (int, float)) and deposit > 0 else "",
                    f"{bal:,.2f}" if isinstance(bal, (int, float)) else "",
                ]
            else:
                tx_date, val_date, desc = row[:3]
                amount = row[3] if len(row) > 3 else 0.0
                values = [
                    tx_date.strftime("%d %b"),
                    val_date.strftime("%d %b"),
                    desc,
                    "",
                    f"{abs(amount):,.2f}" if amount < 0 else "",
                    f"{amount:,.2f}" if amount > 0 else "",
                    "",
                ]
            for i, text in enumerate(values):
                x = table_x + sum(widths[:i]) + 2
                if i in (4, 5, 6):
                    c.setFont("Helvetica", 6.5)
                    c.drawRightString(table_x + sum(widths[: i + 1]) - 4, y, text)
                else:
                    c.drawString(x, y, text)
            y -= 17

        # summary block
        summary_y = y - 12
        c.setFont("Helvetica-Bold", 8)
        c.drawString(PAGE_W - 230, summary_y, "Total Withdrawals / Deposits")
        c.drawRightString(PAGE_W - 42, summary_y, f"{961.25:,.2f}      {2_705.45:,.2f}")
        c.drawString(PAGE_W - 230, summary_y - 14, "Total Interest Paid This year")
        c.drawRightString(PAGE_W - 42, summary_y - 14, "45.45")
        c.drawString(PAGE_W - 230, summary_y - 28, "Average Balance")
        c.drawRightString(PAGE_W - 42, summary_y - 28, "3,912.88")

        # notice boxes
        box1_y = summary_y - 115
        c.setStrokeColor(colors.HexColor("#808080"))
        c.rect(36, box1_y, PAGE_W - 78, 78, fill=0, stroke=1)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(46, box1_y + 62, "CHECK YOUR STATEMENT")
        c.setFont("Helvetica", 6.8)
        _draw_multiline(c, 46, box1_y + 50, "Please check this statement & advise us of any discrepancies within 14 days of receipt. If we do not hear from you, we will take this statement as correct and binding.", width=PAGE_W - 120, size=6.8, leading=8)
        c.drawString(46, box1_y + 18, "For enquiries, please call our Customer Service Officers at (1800) 555-0100.")

        box2_y = box1_y - 92
        c.rect(36, box2_y, PAGE_W - 78, 58, fill=0, stroke=1)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(46, box2_y + 40, "UPDATING YOUR PERSONAL PARTICULARS")
        c.setFont("Helvetica", 6.8)
        _draw_multiline(c, 46, box2_y + 28, "You may update your address and contact numbers via Online Banking or our ATMs. Alternatively, complete and mail us the Change of Address / Contact Details form.", width=PAGE_W - 120, size=6.8, leading=8)

        # footer / promotion
        promo_y = 52
        c.setFont("Helvetica-Bold", 8)
        c.drawString(36, promo_y + 22, "BANK PROMOTION & INFORMATION")
        c.setFont("Helvetica-Bold", 6.8)
        c.drawString(36, promo_y + 8, "REVISION OF INTEREST RATES")
        c.setFont("Helvetica", 6.8)
        _draw_multiline(c, 145, promo_y + 8, "From 1 November 2026, interest rates for Bonus+ Savings Account will be revised. To view the new rates and details, visit Bank website > Notices.", width=360, size=6.8, leading=8)
        c.setFont("Helvetica", 5.8)
        c.drawString(36, 22, "Deposit Insurance Scheme")
        _draw_multiline(c, 36, 15, "Singapore dollar deposits of non-bank depositors and monies and deposits denominated in Singapore dollars under the Supplementary Retirement Scheme are insured by the Singapore Deposit Insurance Corporation, for up to S$100,000 in aggregate per depositor per Scheme member by law. Monies and deposits in Supplementary Retirement Scheme accounts and CPF Investment Scheme accounts are excluded from deposit insurance coverage.", width=PAGE_W - 70, size=5.8, leading=7)

    _save_pdf(output_path, draw)
    return {
        "output_path": output_path,
        "bank_name": bank_name,
        "account_number": account_number,
    }


if __name__ == "__main__":
    import os

    os.makedirs("/home/claude/loan_letters/output", exist_ok=True)
    print(generate_bank_statement(
        "/home/claude/loan_letters/output/bank_statement_template.pdf",
        bank_name="OCBC",
        account_holder="TAN WEI MING",
        account_number="XXXX-0472",
        statement_number="2025-10-01",
        statement_period_start=date(2025, 10, 1),
        statement_period_end=date(2025, 10, 31),
        opening_balance=3250.00,
        closing_balance=4994.20,
        available_balance=4994.20,
        branch_code="001",
        account_type="Monthly Savings Account",
    ))
