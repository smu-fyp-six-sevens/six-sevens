"""
Generates three payslip PDFs directly with ReportLab — no HTML template, no
browser. Each of the three original Payslip_N.html designs (blue corporate
layout, monospace compact statement, boxed detailed layout) is rebuilt here
as its own flowable-based builder function, so the visual character of each
survives without needing a headless browser to render CSS.

Usage:
    python generate_payslips.py

Edit the DATA dicts below to change company/employee/pay details.
Requires only reportlab and pypdf (pip install reportlab pypdf).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # backend/, for the CPF Board rate tables

from app.fraud_detection.external_validation.sg_reference.cpf import (
    band_for_age, bands_in_force, ceiling_on, contribution,
)
from case_facts import as_date, seeded_rng
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate, Frame, HRFlowable, PageTemplate, Paragraph, Spacer, Table, TableStyle,
)

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "generated"

SYNTHETIC_TAG = "SYNTHETIC-TEST-DATA"

DEPARTMENTS = ["Operations", "Finance", "Sales & Marketing", "Administration",
               "Business Development", "Human Resources"]
EMPLOYER_STREETS = ["1 Marina Boulevard", "8 Shenton Way", "80 Robinson Road",
                     "6 Battery Road", "50 Raffles Place", "3 Church Street"]


def employee_cpf(date_of_birth: date | None, pay_date: date, ordinary_wages: float,
                 additional_wages: float = 0.0) -> float:
    """The employee's CPF share for the month, by CPF Board's rules: the age band on the pay date,
    ordinary wages capped at the monthly OW ceiling, additional wages (bonus, variable pay) on top.
    Uses the same rate tables as the fraud checks (sg_reference/cpf.py). Without a date of birth the
    employee is taken to be 55 or younger."""
    bands = bands_in_force(pay_date) or bands_in_force(date(2026, 1, 1))
    band = band_for_age(bands, date_of_birth, pay_date) if date_of_birth else bands[0]
    ceiling = ceiling_on(pay_date) or 8_000
    _, employee = contribution(band, ordinary_wages, ceiling, additional_wages)
    return float(employee)


def synthesize_payslip_data(individual_id: str, *, employee_name: str,
                             employer_name: str, role: str = "", application_date: date | None = None,
                             currency: str = "SGD", company_address: str = "",
                             date_of_birth: date | str | None = None) -> dict:
    """Invent a plausible month of pay for one employee, seeded on their individual
    ID so re-runs are reproducible. The mastersheet's Payslips tab only carries
    identity fields (name, NRIC, role, employer) — no salary figures — so every
    monetary field a payslip template needs is synthesized here, including a
    realistic-looking employee ID (the mastersheet's internal individual ID, e.g.
    "C008-P2", is only used to seed the RNG, never shown on the document).

    `company_address` should be the case's actual registered company address
    (e.g. from case_facts.company_facts()) so it agrees with every other document
    generated for the case. If not supplied (e.g. standalone/demo use), a
    plausible address is invented instead.

    Returns a superset of the fields build_payslip_1/2/3 each read, so callers
    don't need to branch on which template they're about to build.
    """
    rng = seeded_rng(f"{individual_id}|payslip")

    application_date = application_date or datetime.now().date()
    period_month = application_date.month - 1 or 12
    period_year = application_date.year - (application_date.month == 1)
    period_start = date(period_year, period_month, 1)
    next_month_start = date(period_year + (period_month == 12), (period_month % 12) + 1, 1)
    period_end = next_month_start - timedelta(days=1)

    basic_salary = round(rng.uniform(3200, 9500), -1)
    tax_withholding = round(basic_salary * rng.uniform(0.05, 0.12), 2)
    other_deductions = round(rng.uniform(20, 120), 2)
    allowances = round(rng.uniform(150, 700), -1)
    overtime = round(rng.uniform(0, 300), -1)
    variable_pay = round(rng.uniform(100, 600), -1)
    benefits = round(rng.uniform(50, 300), -1)
    housing_allowance = round(rng.uniform(300, 900), -1)
    transport_allowance = round(rng.uniform(100, 300), -1)
    other_allowance = round(rng.uniform(0, 200), -1)

    # Each template's own earnings decide its CPF: salary, allowances and overtime are ordinary
    # wages; variable pay is an additional wage. Benefits (Payslip 2) are left out as not wages.
    dob = as_date(date_of_birth)
    pay_date = period_end
    cpf_employee = employee_cpf(dob, pay_date, basic_salary + allowances + overtime)
    cpf_payslip_2 = employee_cpf(dob, pay_date, basic_salary, variable_pay)
    cpf_payslip_3 = employee_cpf(dob, pay_date, basic_salary + housing_allowance + transport_allowance + other_allowance)

    if not company_address:
        street = EMPLOYER_STREETS[rng.randrange(len(EMPLOYER_STREETS))]
        postal = rng.randint(18900, 68999)
        company_address = f"{street}, Singapore {postal:06d}"

    return {
        "company_name": employer_name,
        "company_address": company_address,
        "employee_name": employee_name,
        "employee_id": f"EMP-{rng.randint(1000, 9999)}",
        "pay_period": period_start.strftime("%B %Y"),
        "payment_date": period_end.strftime("%Y-%m-%d"),
        "currency": currency,
        # Payslip 1
        "basic_salary": basic_salary,
        "allowances": allowances,
        "overtime": overtime,
        "cpf_employee": cpf_employee,
        "other_deductions": other_deductions,
        # Payslip 2
        "variable_pay": variable_pay,
        "benefits": benefits,
        "tax_withholding": tax_withholding,
        "cpf_employee_2": cpf_payslip_2,
        # Payslip 3
        "department": DEPARTMENTS[rng.randrange(len(DEPARTMENTS))],
        "job_title": role or "Employee",
        "housing_allowance": housing_allowance,
        "transport_allowance": transport_allowance,
        "other_allowance": other_allowance,
        "retirement_contribution": cpf_payslip_3,
        "tax_deduction": tax_withholding,
        "other_deduction": other_deductions,
    }


def compute_net_pay(data: dict, template: str) -> float:
    """Same formula each build_payslip_N() uses internally -- pulled out so a
    caller that needs to know an employee's net pay without actually rendering
    a PDF (e.g. reconciling a bank statement's salary-credit line against this
    payslip) computes the exact same number, not an approximation."""
    if "2" in template:
        return (data["basic_salary"] + data["variable_pay"] + data["benefits"]) \
            - (data["cpf_employee_2"] + data["other_deductions"])
    if "3" in template:
        return (data["basic_salary"] + data["housing_allowance"] + data["transport_allowance"]
                + data["other_allowance"]) \
            - (data["retirement_contribution"] + data["tax_deduction"] + data["other_deduction"])
    return (data["basic_salary"] + data["allowances"] + data["overtime"]) \
        - (data["cpf_employee"] + data["other_deductions"])


def money(value: float) -> str:
    return f"{value:,.2f}"


def _build_pdf(output_path: Path, story: list, *, producer: str = "ReportLab PDF Library") -> None:
    tmp_path = output_path.with_suffix(".tmp.pdf")
    doc = BaseDocTemplate(
        str(tmp_path), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=20 * mm, bottomMargin=20 * mm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="template", frames=[frame])])
    doc.build(story)

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_metadata({
        "/Producer": producer,
        "/Creator": producer,
        "/CreationDate": datetime.now().strftime("D:%Y%m%d%H%M%S"),
        "/Keywords": SYNTHETIC_TAG,
    })
    with open(output_path, "wb") as f:
        writer.write(f)
    tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Payslip 1 — "Corporate Payslip" (blue accent, boxed net-pay banner)
# ---------------------------------------------------------------------------

def build_payslip_1(output_path: Path, data: dict) -> float:
    total_earnings = data["basic_salary"] + data["allowances"] + data["overtime"]
    total_deductions = data["cpf_employee"] + data["other_deductions"]
    net_pay = compute_net_pay(data, "Payslip-1")

    s = {
        "company": ParagraphStyle("company", fontName="Helvetica-Bold", fontSize=17, leading=20,
                                  textColor=colors.HexColor("#1f2937")),
        "addr": ParagraphStyle("addr", fontName="Helvetica", fontSize=9, leading=12,
                               textColor=colors.HexColor("#1f2937")),
        "label": ParagraphStyle("label", fontName="Helvetica", fontSize=7.5, leading=9,
                                textColor=colors.HexColor("#64748b")),
        "value": ParagraphStyle("value", fontName="Helvetica", fontSize=10, leading=13,
                                textColor=colors.HexColor("#1f2937")),
        "th": ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=9, textColor=colors.HexColor("#1e40af")),
        "th_r": ParagraphStyle("th_r", fontName="Helvetica-Bold", fontSize=9, alignment=2,
                               textColor=colors.HexColor("#1e40af")),
        "td": ParagraphStyle("td", fontName="Helvetica", fontSize=9.5, textColor=colors.HexColor("#1f2937")),
        "td_r": ParagraphStyle("td_r", fontName="Helvetica", fontSize=9.5, alignment=2,
                               textColor=colors.HexColor("#1f2937")),
        "total": ParagraphStyle("total", fontName="Helvetica-Bold", fontSize=9.5),
        "total_r": ParagraphStyle("total_r", fontName="Helvetica-Bold", fontSize=9.5, alignment=2),
        "net": ParagraphStyle("net", fontName="Helvetica-Bold", fontSize=14, alignment=2,
                              textColor=colors.HexColor("#1e3a8a")),
        "note": ParagraphStyle("note", fontName="Helvetica", fontSize=7.5, textColor=colors.HexColor("#64748b")),
    }
    story: list = []
    story.append(Paragraph(data["company_name"], s["company"]))
    story.append(Paragraph(data["company_address"], s["addr"]))
    story.append(Spacer(1, 3))
    story.append(HRFlowable(width="100%", thickness=2.2, color=colors.HexColor("#2563eb"),
                            spaceBefore=4, spaceAfter=10))

    meta = Table([
        [Paragraph("EMPLOYEE", s["label"]), Paragraph(data["employee_name"], s["value"]),
         Paragraph("EMPLOYEE ID", s["label"]), Paragraph(data["employee_id"], s["value"])],
        [Paragraph("PAY PERIOD", s["label"]), Paragraph(data["pay_period"], s["value"]),
         Paragraph("PAYMENT DATE", s["label"]), Paragraph(data["payment_date"], s["value"])],
    ], colWidths=[32 * mm, 55 * mm, 32 * mm, 51 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(meta)
    story.append(Spacer(1, 6 * mm))

    rows = [[Paragraph("Description", s["th"]), Paragraph(f"Amount ({data['currency']})", s["th_r"])]]
    for label, amount, style in [
        ("Basic salary", data["basic_salary"], s["td"]),
        ("Allowances", data["allowances"], s["td"]),
        ("Overtime", data["overtime"], s["td"]),
    ]:
        rows.append([Paragraph(label, style), Paragraph(money(amount), s["td_r"])])
    rows.append([Paragraph("Total earnings", s["total"]), Paragraph(money(total_earnings), s["total_r"])])
    rows.append([Paragraph("Employee contribution", s["td"]), Paragraph(f"-{money(data['cpf_employee'])}", s["td_r"])])
    rows.append([Paragraph("Other deductions", s["td"]), Paragraph(f"-{money(data['other_deductions'])}", s["td_r"])])
    rows.append([Paragraph("Total deductions", s["total"]), Paragraph(money(total_deductions), s["total_r"])])

    table = Table(rows, colWidths=[110 * mm, 60 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eff6ff")),
        ("LINEBELOW", (0, 0), (-1, -1), 0.6, colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (0, 4), (-1, 4), colors.HexColor("#f8fafc")),
        ("BACKGROUND", (0, 7), (-1, 7), colors.HexColor("#f8fafc")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(table)
    story.append(Spacer(1, 7 * mm))

    net = Table([[Paragraph(f"Net pay: {data['currency']} {money(net_pay)}", s["net"])]], colWidths=[170 * mm])
    net.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#dbeafe")),
        ("TOPPADDING", (0, 0), (-1, -1), 10), ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(net)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("This is a computer-generated payslip and does not require a signature.", s["note"]))

    _build_pdf(output_path, story)
    print(f"Wrote {output_path}")
    return net_pay


# ---------------------------------------------------------------------------
# Payslip 2 — "Compact Payroll Statement" (monospace, dashed rule, boxed net pay)
# ---------------------------------------------------------------------------

def build_payslip_2(output_path: Path, data: dict) -> float:
    net_pay = compute_net_pay(data, "Payslip-2")

    s = {
        "h1": ParagraphStyle("h1", fontName="Courier-Bold", fontSize=15, alignment=1, spaceAfter=6),
        "sub": ParagraphStyle("sub", fontName="Courier", fontSize=9, alignment=1),
        "line_l": ParagraphStyle("line_l", fontName="Courier", fontSize=9.5),
        "line_r": ParagraphStyle("line_r", fontName="Courier", fontSize=9.5, alignment=2),
        "section": ParagraphStyle("section", fontName="Courier-Bold", fontSize=9.5,
                                  textColor=colors.HexColor("#1d4ed8"), spaceBefore=8, spaceAfter=2),
        "net": ParagraphStyle("net", fontName="Courier-Bold", fontSize=13, alignment=1),
    }
    story: list = []
    story.append(Paragraph(data["company_name"], s["h1"]))
    story.append(Paragraph("PAYROLL STATEMENT", s["sub"]))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#374151"),
                            dash=(3, 2), spaceBefore=6, spaceAfter=8))

    def line(label: str, value: str) -> Table:
        t = Table([[Paragraph(label, s["line_l"]), Paragraph(value, s["line_r"])]], colWidths=[85 * mm, 85 * mm])
        t.setStyle(TableStyle([
            ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.HexColor("#9ca3af")),
            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t

    story.append(line("Employee", data["employee_name"]))
    story.append(line("Employee ID", data["employee_id"]))
    story.append(line("Period", data["pay_period"]))

    story.append(Paragraph("EARNINGS", s["section"]))
    story.append(line("Base pay", f"{data['currency']} {money(data['basic_salary'])}"))
    story.append(line("Variable pay", f"{data['currency']} {money(data['variable_pay'])}"))
    story.append(line("Benefits", f"{data['currency']} {money(data['benefits'])}"))

    story.append(Paragraph("DEDUCTIONS", s["section"]))
    # Singapore employers don't withhold income tax from residents' pay; CPF is the statutory deduction.
    story.append(line("Employee CPF", f"{data['currency']} {money(data['cpf_employee_2'])}"))
    story.append(line("Other deductions", f"{data['currency']} {money(data['other_deductions'])}"))

    story.append(Spacer(1, 6 * mm))
    net = Table([[Paragraph(f"NET PAY: {data['currency']} {money(net_pay)}", s["net"])]], colWidths=[170 * mm])
    net.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.4, colors.HexColor("#111827")),
        ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(net)

    _build_pdf(output_path, story)
    print(f"Wrote {output_path}")
    return net_pay


# ---------------------------------------------------------------------------
# Payslip 3 — "Payslip" (detailed, boxed employee-info grid)
# ---------------------------------------------------------------------------

def _field_box(label: str, value: str, s: dict, width: float) -> Table:
    inner = Table([[Paragraph(label, s["field_label"])], [Paragraph(value, s["field_value"])]],
                  colWidths=[width - 12 * mm])
    inner.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    box = Table([[inner]], colWidths=[width])
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#dddddd")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f7f7f7")),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return box


def build_payslip_3(output_path: Path, data: dict) -> float:
    s = {
        "company": ParagraphStyle("company", fontName="Helvetica-Bold", fontSize=17),
        "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=11, spaceBefore=10, spaceAfter=4),
        "field_label": ParagraphStyle("field_label", fontName="Helvetica", fontSize=7,
                                      textColor=colors.HexColor("#666666")),
        "field_value": ParagraphStyle("field_value", fontName="Helvetica-Bold", fontSize=10.5),
        "th": ParagraphStyle("th", fontName="Helvetica-Bold", fontSize=8.5),
        "th_r": ParagraphStyle("th_r", fontName="Helvetica-Bold", fontSize=8.5, alignment=2),
        "td": ParagraphStyle("td", fontName="Helvetica", fontSize=9.5),
        "td_r": ParagraphStyle("td_r", fontName="Helvetica", fontSize=9.5, alignment=2),
        "total": ParagraphStyle("total", fontName="Helvetica-Bold", fontSize=9.5),
        "total_r": ParagraphStyle("total_r", fontName="Helvetica-Bold", fontSize=9.5, alignment=2),
        "net_label": ParagraphStyle("net_label", fontName="Helvetica-Bold", fontSize=13),
        "net_value": ParagraphStyle("net_value", fontName="Helvetica-Bold", fontSize=13, alignment=2),
        "footer": ParagraphStyle("footer", fontName="Helvetica", fontSize=7.5,
                                 textColor=colors.HexColor("#666666"), leading=10),
    }
    story: list = []
    story.append(Paragraph(data["company_name"], s["company"]))
    story.append(Spacer(1, 4))
    story.append(HRFlowable(width="100%", thickness=1.4, color=colors.HexColor("#333333"),
                            spaceBefore=6, spaceAfter=10))

    story.append(Paragraph("Employee Information", s["section"]))
    field_w = 83 * mm
    fields = [
        ("EMPLOYEE NAME", data["employee_name"]), ("EMPLOYEE ID", data["employee_id"]),
        ("DEPARTMENT", data["department"]), ("JOB TITLE", data["job_title"]),
        ("PAY PERIOD", data["pay_period"]), ("PAYMENT DATE", data["payment_date"]),
    ]
    grid_rows = [[_field_box(*fields[i], s, field_w), _field_box(*fields[i + 1], s, field_w)]
                 for i in range(0, len(fields), 2)]
    grid = Table(grid_rows, colWidths=[field_w, field_w])
    grid.setStyle(TableStyle([
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(grid)

    def section_table(title: str, rows: list[tuple[str, float]], total_label: str) -> float:
        story.append(Paragraph(title, s["section"]))
        body = [[Paragraph("Description", s["th"]), Paragraph("Amount", s["th_r"])]]
        for label, amount in rows:
            body.append([Paragraph(label, s["td"]), Paragraph(money(amount), s["td_r"])])
        total = sum(a for _, a in rows)
        body.append([Paragraph(total_label, s["total"]), Paragraph(money(total), s["total_r"])])
        t = Table(body, colWidths=[110 * mm, 60 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
            ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.HexColor("#dddddd")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f5f5f5")),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ]))
        story.append(t)
        return total

    total_earnings = section_table("Earnings", [
        ("Basic Salary", data["basic_salary"]), ("Housing Allowance", data["housing_allowance"]),
        ("Transport Allowance", data["transport_allowance"]), ("Other Allowance", data["other_allowance"]),
    ], "Total Earnings")
    story.append(Spacer(1, 6 * mm))
    total_deductions = section_table("Deductions", [
        ("Retirement / CPF Contribution", data["retirement_contribution"]),
        ("Tax Deduction", data["tax_deduction"]), ("Other Deduction", data["other_deduction"]),
    ], "Total Deductions")

    net_pay = compute_net_pay(data, "Payslip-3")
    story.append(Spacer(1, 7 * mm))
    net = Table([[Paragraph("NET PAY", s["net_label"]), Paragraph(money(net_pay), s["net_value"])]],
               colWidths=[85 * mm, 85 * mm])
    net.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.6, colors.HexColor("#333333")),
        ("TOPPADDING", (0, 0), (-1, -1), 9), ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(net)
    story.append(Spacer(1, 6 * mm))
    story.append(HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#cccccc"), spaceAfter=4))
    story.append(Paragraph(
        "This payslip is system-generated and does not require a signature. "
        "Please retain this document for your personal records.", s["footer"]))

    _build_pdf(output_path, story)
    print(f"Wrote {output_path}")
    return net_pay


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

PAYSLIP_1_DATA = {
    "company_name": "Acme Global Pte Ltd",
    "company_address": "1 Marina Boulevard, Singapore 018989",
    "employee_name": "John Tan",
    "employee_id": "EMP-1024",
    "pay_period": "August 2026",
    "payment_date": "2026-08-31",
    "currency": "SGD",
    "basic_salary": 5000.00,
    "allowances": 600.00,
    "overtime": 150.00,
    "cpf_employee": 500.00,
    "other_deductions": 50.00,
}

PAYSLIP_2_DATA = {
    "company_name": "Northbridge Logistics Inc.",
    "employee_name": "Maria Gomez",
    "employee_id": "EMP-2078",
    "pay_period": "Aug 1 - Aug 31, 2026",
    "currency": "USD",
    "basic_salary": 4200.00,
    "variable_pay": 300.00,
    "benefits": 150.00,
    "tax_withholding": 420.00,
    "cpf_employee_2": 900.00,
    "other_deductions": 30.00,
}

PAYSLIP_3_DATA = {
    "company_name": "Silverline Technologies Ltd.",
    "employee_name": "Aisha Khan",
    "employee_id": "EMP-3051",
    "department": "Engineering",
    "job_title": "Senior Software Engineer",
    "pay_period": "August 2026",
    "payment_date": "2026-08-31",
    "basic_salary": 6000.00,
    "housing_allowance": 800.00,
    "transport_allowance": 200.00,
    "other_allowance": 100.00,
    "retirement_contribution": 600.00,
    "tax_deduction": 750.00,
    "other_deduction": 40.00,
}


TEMPLATES = {
    "1": (build_payslip_1, PAYSLIP_1_DATA, "Payslip_1_generated.pdf"),
    "2": (build_payslip_2, PAYSLIP_2_DATA, "Payslip_2_generated.pdf"),
    "3": (build_payslip_3, PAYSLIP_3_DATA, "Payslip_3_generated.pdf"),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("template", choices=TEMPLATES,
                     help="which payslip layout to generate: 1 (Corporate), 2 (Compact), 3 (Detailed)")
    args = ap.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    builder, data, output_name = TEMPLATES[args.template]
    builder(OUTPUT_DIR / output_name, data)


if __name__ == "__main__":
    main()
