#!/usr/bin/env python3
"""generate_bills.py — Company electricity bill (PacificLight), used as Proof of
Address (Company) in the "Optional Documents" mastersheet tab.

Rewritten from an HTML/CSS + headless-Chrome pipeline to pure ReportLab canvas,
matching the already-approved layout in
../../personal_proof_of_residence/utility_bill_generator.py (same header /
customer-amount block / info strip / consumption-trend box / summary-of-charges
box, same helper-function style) rather than re-deriving the layout from
bill.html/bill.css from scratch. Only the identity is different here: this is a
*company's* commercial electricity account, driven by the mastersheet's
"Optional Documents" tab (Document Type == "Proof of Address (Company)",
filename NOT containing "telecom" -- the telecom variant lives in
../telecom/generate_telecom.py) instead of an individual's residence.

bill.html / bill.css are kept alongside this file as the original design
reference; not used at generation time anymore.

Usage:
cd backend/doc_gen_scripts/optional_documents/utility
python generate_bills.py ../../v15_document_mastersheet.xlsx --outdir out/
python generate_bills.py ../../v15_document_mastersheet.xlsx --outdir out/ --cases C042

Run with no arguments to build one example bill at output/bill_example.pdf.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import openpyxl
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as pdfcanvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from case_facts import seeded_rng  # noqa: E402

PAGE_W, PAGE_H = A4
MARGIN = 40
CONTENT_W = PAGE_W - 2 * MARGIN
FONT = "Helvetica"
FONT_B = "Helvetica-Bold"

ORANGE = colors.HexColor("#c0592c")
ORANGE_LIGHT = colors.HexColor("#eb9a5c")
ORANGE_PALE = colors.HexColor("#f2b183")
GREY_LABEL = colors.HexColor("#6b7280")
GREY_BORDER = colors.HexColor("#d8dbe0")
BLACK = colors.black

ASSETS = Path(__file__).resolve().parent
DEFAULT_OUT = ASSETS / "output" / "bill_example.pdf"

BANKS = ["DBS", "OCBC", "UOB", "HSBC", "Standard Chartered", "Maybank", "CIMB"]

_POSTAL_RE = re.compile(r"(?:Singapore\s*)?(\d{6})\s*$", re.I)


def split_address(raw: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    postal = ""
    kept: list[str] = []
    for part in parts:
        match = _POSTAL_RE.search(part)
        if match and not postal:
            postal = match.group(1)
            remainder = _POSTAL_RE.sub("", part).strip()
            remainder = re.sub(r"Singapore\s*$", "", remainder, flags=re.I).strip()
            if remainder:
                kept.append(remainder)
        else:
            kept.append(part)
    street = kept[0] if kept else (raw or "").strip()
    unit = ", ".join(kept[1:]) if len(kept) > 1 else ""
    return street, unit, postal


def _money(amount: float | str) -> str:
    if isinstance(amount, str):
        return amount
    return f"${amount:,.2f}"


def _build_pdf(output_path: str, draw_fn, *, synthetic_tag: bool = True,
               producer: str = "PacificLight ePortal") -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    c = pdfcanvas.Canvas(str(tmp_path), pagesize=A4)
    draw_fn(c)
    c.save()

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    creation = datetime.now().strftime("D:%Y%m%d%H%M%S")
    meta = {"/Producer": producer, "/Creator": producer, "/CreationDate": creation}
    if synthetic_tag:
        meta["/Subject"] = "SYNTHETIC-TEST-DATA"
        meta["/Keywords"] = "SYNTHETIC-TEST-DATA"
    writer.add_metadata(meta)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        writer.write(f)
    tmp_path.unlink(missing_ok=True)


def _text(c: pdfcanvas.Canvas, text: str, x: float, y: float, *, font=FONT, size=9,
          color=BLACK, align: str = "left") -> None:
    c.setFillColor(color)
    c.setFont(font, size)
    if align == "right":
        c.drawRightString(x, y, text)
    elif align == "center":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)


def _wrapped(c: pdfcanvas.Canvas, text: str, x: float, top: float, width: float, *,
             font=FONT, size=9, leading=None, color=BLACK, align: str = "left") -> float:
    leading = leading or size * 1.3
    c.setFillColor(color)
    c.setFont(font, size)
    y = top
    for line in simpleSplit(text, font, size, width):
        if align == "right":
            c.drawRightString(x + width, y, line)
        else:
            c.drawString(x, y, line)
        y -= leading
    return y


def _rounded_box(c: pdfcanvas.Canvas, x: float, y: float, w: float, h: float, radius: float = 8) -> None:
    c.setStrokeColor(GREY_BORDER)
    c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, radius, stroke=1, fill=0)


def _draw_logo(c: pdfcanvas.Canvas, x: float, y: float) -> None:
    """Small flower-of-circles mark, since there's no PacificLight logo asset."""
    c.setFillColor(ORANGE_PALE)
    c.circle(x, y + 6, 5.5, stroke=0, fill=1)
    c.circle(x + 9, y + 3, 5.5, stroke=0, fill=1)
    c.setFillColor(ORANGE_LIGHT)
    c.circle(x + 4, y - 4, 5.5, stroke=0, fill=1)
    c.setFillColor(ORANGE)
    c.circle(x + 3, y + 1, 4, stroke=0, fill=1)


# --------------------------------------------------------------------------
# Renderer -- same layout as personal_proof_of_residence/utility_bill_generator.py
# --------------------------------------------------------------------------

def generate_utility_bill(
    output_path: str,
    *,
    provider_name: str,
    bill_date: date,
    ple_account_no: str,
    statement_no: str,
    customer_name: str,
    address_lines: list[str],
    total_amount_payable: float,
    payment_due: date,
    invoice_address_lines: list[str],
    mssl_account_no: str,
    billing_period_start: date,
    billing_period_end: date,
    account_type: str = "Commercial",
    deposit: float = 400.00,
    contract_end_date: date | None = None,
    previous_balance: float = 199.60,
    payment_received_date: date | None = None,
    payment_received_amount: float | None = None,
    current_charges: float | None = None,
    deduction_bank_name: str = "Standard Chartered",
    deduction_bank_account_last4: str = "0158",
    usage_kwh: int = 689,
    consumption_trend: list[tuple[str, int]] | None = None,
    synthetic_tag: bool = True,
) -> dict:
    # A fixed date drifted past five years from the bill (POA.CONTRACT_TERM); a typical retail contract runs two.
    contract_end_date = contract_end_date or date(bill_date.year + 2, bill_date.month, min(bill_date.day, 28))
    payment_received_date = payment_received_date or date(billing_period_end.year, billing_period_end.month, 19)
    payment_received_amount = payment_received_amount if payment_received_amount is not None else previous_balance
    current_charges = current_charges if current_charges is not None else total_amount_payable
    consumption_trend = consumption_trend or [
        ("MAR", 638), ("APR", 702), ("MAY", 688), ("JUN", 716), ("JUL", 774), ("AUG", 689),
    ]

    def draw(c: pdfcanvas.Canvas) -> None:
        left = MARGIN
        right = PAGE_W - MARGIN

        # --- Header: logo/wordmark (left), bill meta (right) ---
        y = PAGE_H - MARGIN - 6
        _draw_logo(c, left + 7, y - 8)
        _text(c, provider_name, left + 22, y - 14, font=FONT_B, size=19, color=ORANGE)

        my = y
        _text(c, f"Bill Date: {bill_date.strftime('%d %b %Y')}", right, my, size=9.5, align="right")
        my -= 18
        _text(c, f"PLE Account No.: {ple_account_no}", right, my, size=9, color=GREY_LABEL, align="right")
        my -= 16
        _text(c, f"Statement No.: {statement_no}", right, my, size=9, color=GREY_LABEL, align="right")

        y -= 56

        # --- Customer block (left) / Amount + Due + Invoice-for (right) ---
        left_y = y
        _text(c, customer_name, left, left_y, font=FONT_B, size=10.5)
        left_y -= 14
        for line in address_lines:
            _text(c, line, left, left_y, size=9.5)
            left_y -= 12

        right_y = y
        _text(c, _money(total_amount_payable), right, right_y, font=FONT_B, size=25, align="right")
        right_y -= 15
        _text(c, "Total Amount Payable", right, right_y, size=8.5, color=GREY_LABEL, align="right")
        right_y -= 20
        _text(c, payment_due.strftime("%d %b %Y"), right, right_y, font=FONT_B, size=14, align="right")
        right_y -= 12
        _text(c, "Payment Due", right, right_y, size=8.5, color=GREY_LABEL, align="right")
        right_y -= 18
        _text(c, "THIS IS YOUR TAX INVOICE FOR:", right, right_y, font=FONT_B, size=8, align="right")
        right_y -= 12
        for line in invoice_address_lines:
            _text(c, line, right, right_y, font=FONT_B, size=9, align="right")
            right_y -= 12

        y = min(left_y, right_y) - 12

        # --- MSSL account + 5-column info strip ---
        _text(c, f"MSSL ACCOUNT NO.: {mssl_account_no}", left, y, font=FONT_B, size=9.5)
        y -= 22

        info_cols = [
            ("BILLING PERIOD",
             f"{billing_period_start.strftime('%d %b %Y')} – {billing_period_end.strftime('%d %b %Y')}"),
            ("BILL DATE", bill_date.strftime("%d %b %Y")),
            ("ACCOUNT TYPE", account_type),
            ("DEPOSIT", _money(deposit)),
            ("CONTRACT END DATE", contract_end_date.strftime("%d %b %Y")),
        ]
        col_w5 = CONTENT_W / len(info_cols)
        for i, (label, value) in enumerate(info_cols):
            cx = left + i * col_w5
            _text(c, label, cx, y, font=FONT, size=7, color=GREY_LABEL)
            _wrapped(c, value, cx, y - 12, col_w5 - 6, font=FONT_B, size=9.3, leading=11)
        y -= 40

        # --- Consumption trend box ---
        box_h = 130
        box_y = y - box_h
        _rounded_box(c, left, box_y, CONTENT_W, box_h)
        _text(c, "CONSUMPTION TREND", left + CONTENT_W / 2, y - 18, font=FONT_B, size=10, align="center")

        chart_left = left + 20
        chart_right = left + CONTENT_W - 150
        axis_y = box_y + 40
        _text(c, "ELECTRICITY", left + 14, y - 42, font=FONT_B, size=8.5)
        _text(c, "kWh", chart_left, axis_y + 4, size=7, color=GREY_LABEL)

        c.setStrokeColor(GREY_BORDER)
        c.setLineWidth(0.8)
        c.line(chart_left, axis_y, chart_right, axis_y)

        n = len(consumption_trend)
        step = (chart_right - chart_left - 32) / max(n - 1, 1)
        for i, (month, value) in enumerate(consumption_trend):
            cx = chart_left + 32 + i * step
            _text(c, str(value), cx, axis_y + 8, font=FONT_B, size=8, align="center")
            _text(c, month, cx, axis_y - 12, size=7.5, color=GREY_LABEL, align="center")
        _text(c, "*Estimated month", chart_left, box_y + 10, size=6, color=GREY_LABEL)

        usage_x = right - 14
        _text(c, "USAGE", usage_x, y - 42, size=8.5, color=GREY_LABEL, align="right")
        _text(c, f"{usage_kwh} kWh", usage_x, y - 62, font=FONT_B, size=19, align="right")
        _text(c, "(Rounded to whole number)", usage_x, y - 74, size=6.5, color=GREY_LABEL, align="right")

        y = box_y - 16

        # --- Summary of charges box ---
        final_label = (
            f"Total Amount Payable will be deducted from your {deduction_bank_name} "
            f"bank account ending with {deduction_bank_account_last4} on {payment_due.strftime('%d %b %Y')}"
        )
        final_label_w = CONTENT_W - 20 - 90
        final_lines = simpleSplit(final_label, FONT_B, 8.5, final_label_w)
        rows_h = 4 * 20
        final_h = max(len(final_lines) * 11, 22) + 16
        box2_h = 34 + rows_h + final_h
        box2_y = y - box2_h
        _rounded_box(c, left, box2_y, CONTENT_W, box2_h)

        ry = y - 20
        _text(c, "SUMMARY OF CHARGES", left + 14, ry, font=FONT_B, size=10)
        ry -= 20

        charges_rows = [
            ("Balance Brought Forward from Previous Bill", _money(previous_balance)),
            (f"Payment Received on/before {payment_received_date.strftime('%d %b %Y')} - Thank You",
             f"-{_money(payment_received_amount)}"),
            ("Outstanding Balance", _money(0.00)),
            ("Current Charges", _money(current_charges)),
        ]
        for label, value in charges_rows:
            _text(c, label, left + 14, ry, size=9.3)
            _text(c, value, right - 14, ry, size=9.3, align="right")
            ry -= 20

        c.setStrokeColor(GREY_BORDER)
        c.setLineWidth(0.6)
        c.line(left + 14, ry + 6, right - 14, ry + 6)
        ry -= 12

        amount_x = right - 14
        _wrapped(c, final_label, left + 14, ry, final_label_w, font=FONT_B, size=8.5, leading=11)
        _text(c, _money(total_amount_payable), amount_x, ry - (final_h - 22) / 2, font=FONT_B, size=17, align="right")

    _build_pdf(output_path, draw, synthetic_tag=synthetic_tag)
    return {
        "output_path": output_path,
        "provider_name": provider_name,
        "bill_date": bill_date.isoformat(),
        "total_amount_payable": total_amount_payable,
        "payment_due": payment_due.isoformat(),
    }


# --------------------------------------------------------------------------
# Field computation -- company-scale version of synthesize_utility_bill_data()
# --------------------------------------------------------------------------

UTILITY_PROVIDERS = ["PacificLight", "Keppel Electric", "Sembcorp Power", "Geneco", "Tuas Power"]
MONTH_NAMES = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _consumption_trend(rng, end: date) -> list[tuple[str, int]]:
    base = rng.randint(1800, 5200)  # commercial premises use far more than a household
    swing = base * 0.08
    values = []
    current = float(base)
    year, month = end.year, end.month
    labels = []
    for _ in range(6):
        labels.append(MONTH_NAMES[month - 1])
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    labels.reverse()
    for _ in range(6):
        current *= rng.uniform(0.92, 1.1)
        current += rng.uniform(-swing, swing)
        values.append(max(500, int(round(current))))
    return list(zip(labels, values))


def synthesize_company_bill_data(case_id: str, filename: str, *, application_date: date | None = None) -> dict:
    """Same idea as personal_proof_of_residence's synthesize_utility_bill_data(),
    scaled up for a company's commercial account -- the Optional Documents tab
    carries no provider/account/amount columns, so every field beyond company
    identity/address is synthesized here, seeded per case+filename."""
    rng = seeded_rng(f"{case_id}|{filename}|utility")
    application_date = application_date or datetime.now().date()

    bill_date = application_date - timedelta(days=rng.randint(2, 10))
    period_end = date(bill_date.year, bill_date.month, 1) - timedelta(days=1)
    period_start = period_end - timedelta(days=27 + rng.randint(0, 3))
    payment_due = bill_date + timedelta(days=14)
    trend = _consumption_trend(rng, period_end)
    usage_kwh = trend[-1][1]

    rate = rng.choice([0.2599, 0.2688, 0.2799, 0.2899, 0.2998, 0.3097])
    current_charges = round(usage_kwh * rate, 2)
    previous_balance = round(trend[-2][1] * rate * rng.uniform(0.94, 1.06), 2)
    scenario = rng.choice(["paid", "paid", "paid", "outstanding"])
    if scenario == "paid":
        payment_received_amount = previous_balance
        total_amount_payable = current_charges
    else:
        payment_received_amount = round(previous_balance * rng.uniform(0.4, 0.8), 2)
        total_amount_payable = round(previous_balance - payment_received_amount + current_charges, 2)

    return dict(
        provider_name=UTILITY_PROVIDERS[rng.randrange(len(UTILITY_PROVIDERS))],
        bill_date=bill_date,
        payment_due=payment_due,
        billing_period_start=period_start,
        billing_period_end=period_end,
        ple_account_no=f"{rng.randint(10_000_000, 99_999_999)}",
        mssl_account_no=f"{rng.randint(1_000_000_000, 9_999_999_999)}",
        statement_no=f"{rng.randint(1_000_000, 9_999_999)}",
        total_amount_payable=total_amount_payable,
        account_type="Commercial",
        deposit=rng.choice([400.00, 600.00, 800.00, 1000.00, 1500.00]),
        # Up to 4 years and 11 months from the bill: a contract ending more than 5 years out is flagged (POA.CONTRACT_TERM).
        contract_end_date=date(bill_date.year + rng.randint(1, 4), rng.randint(1, 12), rng.choice([1, 10, 15, 28])),
        previous_balance=previous_balance,
        payment_received_date=period_end - timedelta(days=rng.randint(6, 12)) if scenario == "paid" else None,
        payment_received_amount=payment_received_amount,
        current_charges=current_charges,
        deduction_bank_name=rng.choice(BANKS),
        deduction_bank_account_last4=f"{rng.randint(0, 9999):04d}",
        usage_kwh=usage_kwh,
        consumption_trend=trend,
    )


# --------------------------------------------------------------------------
# Mastersheet-driven persona
# --------------------------------------------------------------------------

POA_DOCUMENT_TYPE = "Proof of Address (Company)"


def build_persona(row: dict[str, Any]) -> dict:
    case_id = str(row.get("Case ID") or "")
    filename = str(row.get("Filename") or "")
    company_name = str(row.get("Company Name (auto)") or "").strip()
    street, unit, postal = split_address(str(row.get("Company Address (auto)") or ""))
    address_lines = [f"{street} {unit}".strip(), f"Singapore {postal or '000000'}"]

    data = synthesize_company_bill_data(case_id, filename)
    return dict(
        customer_name=company_name,
        address_lines=address_lines,
        invoice_address_lines=address_lines,
        **data,
    )


def _rows(ws) -> list[dict[str, Any]]:
    header = [c.value for c in ws[1]]
    return [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]


def load_cases(xlsx: str | Path, cases: list[str] | None = None) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(str(xlsx), data_only=True)
    ws = wb["Optional Documents"]
    out = []
    for row in _rows(ws):
        if str(row.get("Document Type") or "").strip() != POA_DOCUMENT_TYPE:
            continue
        filename = str(row.get("Filename") or "")
        if "telecom" in filename.lower():
            continue
        case_id = str(row.get("Case ID") or "")
        if cases and case_id not in cases:
            continue
        out.append(row)
    return out


def generate(xlsx: str | Path, outdir: str | Path, cases: list[str] | None = None,
             synthetic_tag: bool = True) -> list[Path]:
    written = []
    for row in load_cases(xlsx, cases):
        filename = str(row.get("Filename") or f"{row.get('Case ID')}_UtilityBill.pdf")
        path = Path(outdir) / filename
        persona = build_persona(row)
        generate_utility_bill(str(path), synthetic_tag=synthetic_tag, **persona)
        written.append(path)
        print(f"{row.get('Case ID')}  {filename}  {row.get('Company Name (auto)')}")
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate PacificLight-style company electricity bill PDFs.")
    parser.add_argument("xlsx", nargs="?", help="Mastersheet path")
    parser.add_argument("--outdir", "-o", type=Path, default=ASSETS / "output", help="Output directory")
    parser.add_argument("--cases", nargs="*", help="Case IDs to include; default is all")
    parser.add_argument("--no-synthetic-tag", action="store_true", help="omit the SYNTHETIC-TEST-DATA metadata tag")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.xlsx:
        persona = build_persona({
            "Case ID": "DEMO",
            "Filename": "bill_example.pdf",
            "Company Name (auto)": "Harbourview Trading Pte. Ltd.",
            "Company Address (auto)": "10 Anson Road, #18-12 International Plaza, Singapore 079903",
        })
        print(generate_utility_bill(str(DEFAULT_OUT), **persona))
        return
    args.outdir.mkdir(parents=True, exist_ok=True)
    written = generate(args.xlsx, args.outdir, cases=args.cases, synthetic_tag=not args.no_synthetic_tag)
    print(f"Wrote {len(written)} utility bill PDF(s) to {args.outdir}")


if __name__ == "__main__":
    main()
