"""
Generates a utility bill / proof-of-residence PDF matching the attached
PacificLight sample layout: logo + bill meta header, customer block beside a
large amount-due panel, an invoice-address callout, a 5-column account info
strip, a boxed consumption-trend panel, and a boxed summary-of-charges panel.

Drawn directly with a canvas (rather than flowables) since the layout is a
fixed one-page invoice with panel positions that need to match the reference
pixel-for-pixel more than they need to reflow.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from case_facts import seeded_rng
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfgen import canvas as pdfcanvas

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


def _money(amount: float | str) -> str:
    if isinstance(amount, str):
        return amount
    return f"${amount:,.2f}"


def _build_pdf(output_path: str, draw_fn, *, producer: str = "DocuSuite Enterprise 4.2") -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    c = pdfcanvas.Canvas(str(tmp_path), pagesize=A4)
    draw_fn(c)
    c.save()

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    creation = datetime.now().strftime("D:%Y%m%d%H%M%S")
    writer.add_metadata({"/Producer": producer, "/Creator": producer, "/CreationDate": creation})
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


UTILITY_PROVIDERS = ["PacificLight", "Keppel Electric", "Sembcorp Power", "Geneco", "Tuas Power"]


def synthesize_utility_bill_data(individual_id: str, *, application_date: date | None = None) -> dict:
    """Invent plausible account numbers, billing period and amount for one utility
    bill, seeded on the individual's ID so re-runs are reproducible. The mastersheet's
    Proof of Address tab carries no provider/account/amount columns at all -- every
    field a utility bill template needs beyond identity/address is synthesized here.
    """
    rng = seeded_rng(f"{individual_id}|utility-bill")
    application_date = application_date or datetime.now().date()

    bill_date = application_date - timedelta(days=rng.randint(2, 10))
    period_end = date(bill_date.year, bill_date.month, 1) - timedelta(days=1)
    period_start = period_end - timedelta(days=27 + rng.randint(0, 3))
    payment_due = bill_date + timedelta(days=14)

    return dict(
        provider_name=UTILITY_PROVIDERS[rng.randrange(len(UTILITY_PROVIDERS))],
        bill_date=bill_date,
        payment_due=payment_due,
        billing_period_start=period_start,
        billing_period_end=period_end,
        ple_account_no=f"{rng.randint(10_000_000, 99_999_999)}",
        mssl_account_no=f"{rng.randint(1_000_000_000, 9_999_999_999)}",
        statement_no=f"{rng.randint(1_000_000, 9_999_999)}",
        total_amount_payable=round(rng.uniform(80, 260), 2),
    )


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
    account_type: str = "Residential",
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

        col_x = left + 330
        col_w = right - col_x
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

    _build_pdf(output_path, draw)
    return {
        "output_path": output_path,
        "provider_name": provider_name,
        "bill_date": bill_date.isoformat(),
        "total_amount_payable": total_amount_payable,
        "payment_due": payment_due.isoformat(),
    }


if __name__ == "__main__":
    out_dir = Path.cwd() / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    result = generate_utility_bill(
        str(out_dir / "proof_of_residence_utility_bill.pdf"),
        provider_name="PacificLight",
        bill_date=date(2026, 9, 1),
        ple_account_no="26919567",
        statement_no="1448462",
        customer_name="Tan Wei Ming",
        address_lines=["18 Ardmore Park #15-02", "Singapore 259960"],
        total_amount_payable=185.20,
        payment_due=date(2026, 9, 15),
        invoice_address_lines=["18 Ardmore Park #15-02", "Singapore 259960"],
        mssl_account_no="9385283645",
        billing_period_start=date(2026, 7, 25),
        billing_period_end=date(2026, 8, 21),
    )
    print("Utility bill:", result)
