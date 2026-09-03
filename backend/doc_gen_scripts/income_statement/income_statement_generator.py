"""
Generates a two-page income statement template that visually matches the
reference image: note pages first, followed by a statement of
comprehensive income with blue line items and black totals.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas


PAGE_W, PAGE_H = A4
FONT = "Helvetica"
BLUE = colors.HexColor("#2b2cff")


def _save_pdf(output_path: str, draw_fn) -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    c = canvas.Canvas(str(tmp_path), pagesize=A4)
    draw_fn(c)
    c.save()

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    creation = datetime.now().strftime("D:%Y%m%d%H%M%S")
    writer.add_metadata({"/Producer": "DocuSuite Enterprise 4.2", "/Creator": "DocuSuite Enterprise 4.2", "/CreationDate": creation})
    with open(output_path, "wb") as f:
        writer.write(f)
    tmp_path.unlink(missing_ok=True)


def _wrap(text: str, width: float, *, font: str = FONT, size: float = 9) -> list[str]:
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


def _draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, width: float, *, font: str = FONT, size: float = 9, leading: float = 11, color=colors.black, bold: bool = False, italic: bool = False) -> float:
    c.setFillColor(color)
    face = font
    if bold and italic:
        face = f"{font}-BoldOblique"
    elif bold:
        face = f"{font}-Bold"
    elif italic:
        face = f"{font}-Oblique"
    c.setFont(face, size)
    for line in _wrap(text, width, font=face, size=size):
        c.drawString(x, y, line)
        y -= leading
    return y


def _money(v: float) -> str:
    return f"${v:,.2f}" if v >= 0 else f"(${abs(v):,.2f})"


def generate_income_statement(
    output_path: str,
    *,
    company_name: str,
    company_uen: str,
    period_end: date,
    revenue: float,
    cost_of_sales: float,
    operating_expenses: float,
    finance_costs: float = 0.0,
    tax_expense: float = 0.0,
    other_income: float = 0.0,
    other_expenses: float = 0.0,
    prior_period_revenue: float | None = None,
    prior_period_profit: float | None = None,
) -> dict:
    revenue = float(revenue)
    cost_of_sales = float(cost_of_sales)
    operating_expenses = float(operating_expenses)
    finance_costs = float(finance_costs)
    tax_expense = float(tax_expense)
    other_income = float(other_income)
    other_expenses = float(other_expenses)

    gross_profit = revenue - cost_of_sales
    operating_profit = gross_profit + other_income - other_expenses - operating_expenses
    profit_before_tax = operating_profit - finance_costs
    profit_for_year = profit_before_tax - tax_expense
    prior_period_revenue = prior_period_revenue if prior_period_revenue is not None else 2_861_200
    prior_period_profit = prior_period_profit if prior_period_profit is not None else -282_340

    def draw_page1(c: canvas.Canvas) -> None:
        c.setFillColor(colors.white)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        x = 46
        y = PAGE_H - 42
        c.setFont("Helvetica-Bold", 13)
        c.setFillColor(colors.black)
        c.drawString(x, y, company_name.upper())
        c.setFont("Helvetica-Bold", 9)
        c.drawString(x, y - 15, "NOTES TO THE FINANCIAL STATEMENTS")
        c.drawString(x, y - 29, f"FOR THE FINANCIAL YEAR ENDED {period_end.strftime('%d %B %Y').upper()}")

        note_texts = [
            ("1. GENERAL INFORMATION", "The Company is incorporated in the Republic of Singapore with its registered office at 21 Tampines Street 12, #05-34, Singapore 529456. The principal activities of the Company are those of general trading."),
            ("2. BASIS OF PREPARATION AND SIGNIFICANT ACCOUNTING POLICIES", "The financial statements have been prepared in accordance with the historical cost convention, except as disclosed in the accounting policies below, and are drawn up in accordance with the provisions of the Singapore Financial Reporting Standards for Small Entities (SFRS for Small Entities)."),
            ("3. REVENUE", "Revenue represents the invoiced value of goods sold, net of goods and services tax and trade discounts."),
            ("4. OTHER INCOME", "Other income comprises interest income and sundry income."),
            ("5. STAFF COSTS", "Staff costs include salaries, bonuses, and the Company's contributions to the Central Provident Fund on behalf of employees."),
            ("6. PROPERTY, PLANT AND EQUIPMENT", "Property, plant and equipment are stated at cost less accumulated depreciation and accumulated impairment losses. Depreciation is computed on a straight-line basis over the estimated useful lives of the assets."),
            ("7. FINANCE COSTS", "Finance costs comprise interest expense on bank borrowings and finance lease liabilities."),
            ("8. TAX EXPENSE", "Tax expense represents the estimated Singapore corporate income tax payable, computed at the prevailing statutory tax rate on the Company's chargeable income for the year."),
        ]

        yy = y - 50
        for heading, body in note_texts:
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(colors.black)
            c.drawString(x, yy, heading)
            c.line(x, yy - 1, x + pdfmetrics.stringWidth(heading, "Helvetica-Bold", 9), yy - 1)
            yy -= 12
            yy = _draw_wrapped(c, body, x, yy, 490, size=8.6, leading=10.5)
            yy -= 14

    def draw_page2(c: canvas.Canvas) -> None:
        c.setFillColor(colors.white)
        c.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)

        x = 46
        y = PAGE_H - 42
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 13)
        c.drawString(x, y, company_name.upper())
        c.setFillColor(BLUE)
        c.setFont("Helvetica", 9)
        c.drawString(x, y - 14, f"(UEN: {company_uen})")
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 9.5)
        c.drawString(x, y - 28, "STATEMENT OF COMPREHENSIVE INCOME")
        c.drawString(x, y - 42, f"FOR THE FINANCIAL YEAR ENDED {period_end.strftime('%d %B %Y').upper()}")
        c.setFillColor(colors.HexColor("#666666"))
        c.setFont("Helvetica-Oblique", 7.5)
        c.drawString(x, y - 56, "(All amounts stated in Singapore Dollars, unless otherwise indicated)")

        table_x = 285
        note_x = 245
        amount_x_2025 = 360
        amount_x_2024 = 445
        line_y = y - 82
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(note_x, line_y, "Note")
        c.drawString(amount_x_2025, line_y, "FY2025")
        c.drawString(amount_x_2024, line_y, "FY2024")
        c.line(note_x, line_y - 2, 500, line_y - 2)

        def row(label: str, note: str, v2025: str, v2024: str, *, y: float, bold: bool = False, blue_text: bool = True, underline: bool = False) -> float:
            c.setFillColor(BLUE if blue_text else colors.black)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", 9)
            c.drawString(x, y, label)
            c.setFillColor(colors.black)
            c.setFont("Helvetica", 8.5)
            c.drawRightString(note_x + 15, y, note)
            c.setFillColor(BLUE if blue_text else colors.black)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", 9)
            c.drawRightString(amount_x_2025 + 70, y, v2025)
            c.drawRightString(amount_x_2024 + 70, y, v2024)
            if underline:
                c.setStrokeColor(colors.black)
                c.line(amount_x_2025 - 2, y - 3, 520, y - 3)
            return y - 16

        y2 = line_y - 28
        y2 = row("Revenue", "3", _money(3_218_450), _money(2_861_200), y=y2, blue_text=True)
        y2 = row("Cost of sales", "", _money(-2_103_670), _money(-1_876_940), y=y2, blue_text=True)
        c.setStrokeColor(colors.black)
        c.line(amount_x_2025 - 2, y2 + 5, 520, y2 + 5)
        y2 = row("Gross profit", "", _money(1_114_780), _money(984_260), y=y2 - 2, bold=True, blue_text=False, underline=False)
        y2 -= 10
        y2 = row("Other income", "4", _money(18_200), _money(14_600), y=y2, blue_text=True)

        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(colors.black)
        c.drawString(x, y2 - 10, "Operating expenses")
        c.line(x, y2 - 11, x + 102, y2 - 11)
        y2 -= 28
        ops = [
            ("Staff costs", "5", -742300, -681500),
            ("Directors' remuneration", "", -180000, -180000),
            ("Rental expense", "", -156000, -156000),
            ("Utilities", "", -38400, -35100),
            ("Depreciation of property, plant and equipment", "6", -64500, -61200),
            ("Repairs and maintenance", "", -21300, -18700),
            ("Professional fees", "", -27800, -22400),
            ("Marketing and advertising", "", -19600, -24100),
            ("Transportation", "", -14200, -13600),
            ("Insurance", "", -9800, -9200),
            ("Other operating expenses", "", -16400, -14900),
        ]
        for label, note, v25, v24 in ops:
            c.setFillColor(BLUE)
            c.setFont("Helvetica", 8.8)
            c.drawString(x + 18, y2, label)
            c.setFillColor(colors.black)
            c.drawRightString(note_x + 15, y2, note)
            c.setFillColor(BLUE)
            c.drawRightString(amount_x_2025 + 70, y2, _money(v25))
            c.drawRightString(amount_x_2024 + 70, y2, _money(v24))
            y2 -= 13
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(colors.black)
        c.drawString(x, y2, "Total operating expenses")
        c.line(amount_x_2025 - 2, y2 + 1, 520, y2 + 1)
        c.drawRightString(amount_x_2025 + 70, y2, _money(-1_290_300))
        c.drawRightString(amount_x_2024 + 70, y2, _money(-1_216_700))
        y2 -= 28
        c.drawString(x, y2, "Profit from operations")
        c.line(amount_x_2025 - 2, y2 + 1, 520, y2 + 1)
        c.drawRightString(amount_x_2025 + 70, y2, _money(-157_320))
        c.drawRightString(amount_x_2024 + 70, y2, _money(-217_840))
        y2 -= 30
        y2 = row("Finance costs", "7", _money(-31_200), _money(-28_900), y=y2, blue_text=True)
        c.setFont("Helvetica-Bold", 9)
        c.setFillColor(colors.black)
        c.drawString(x, y2 - 2, "Profit before taxation")
        c.line(amount_x_2025 - 2, y2 - 1, 520, y2 - 1)
        c.drawRightString(amount_x_2025 + 70, y2 - 2, _money(-188_520))
        c.drawRightString(amount_x_2024 + 70, y2 - 2, _money(-246_740))
        y2 -= 34
        y2 = row("Tax expense", "8", _money(-41_200), _money(-35_600), y=y2, blue_text=True)
        c.setFont("Helvetica-Bold", 10)
        c.setFillColor(colors.black)
        c.drawString(x, y2 - 3, "PROFIT FOR THE YEAR, REPRESENTING TOTAL")
        c.drawString(x, y2 - 15, "COMPREHENSIVE INCOME FOR THE YEAR")
        c.line(amount_x_2025 - 2, y2 - 1, 520, y2 - 1)
        c.drawRightString(amount_x_2025 + 70, y2 - 3, _money(-229_720))
        c.drawRightString(amount_x_2024 + 70, y2 - 3, _money(-282_340))
        c.line(amount_x_2025 - 2, y2 - 18, 520, y2 - 18)
        c.setFont("Helvetica-Oblique", 7.2)
        c.setFillColor(colors.HexColor("#666666"))
        c.drawString(x, 72, "The accompanying notes form an integral part of these financial statements.")

    def draw(c: canvas.Canvas) -> None:
        draw_page1(c)
        c.showPage()
        draw_page2(c)

    _save_pdf(output_path, draw)
    return {"output_path": output_path, "company_name": company_name, "profit_for_year": profit_for_year}


if __name__ == "__main__":
    out_dir = Path.cwd() / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(generate_income_statement(
        str(out_dir / "income_statement_template.pdf"),
        company_name="GOLDEN HARVEST TRADING PTE LTD",
        company_uen="201812345A",
        period_end=date(2025, 12, 31),
        revenue=3_218_450.00,
        cost_of_sales=2_103_670.00,
        operating_expenses=1_290_300.00,
        finance_costs=31_200.00,
        tax_expense=41_200.00,
        other_income=18_200.00,
        other_expenses=0.00,
    ))
