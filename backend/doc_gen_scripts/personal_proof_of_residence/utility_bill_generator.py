"""
Generates a utility bill / proof-of-residence PDF template based on the
attached PacificLight sample. The script keeps the document structure
fixed so the same fields can later be replaced with live sample values.
"""

from __future__ import annotations

from datetime import date, datetime
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
        "title": ParagraphStyle("title", fontName=f"{FONT}-Bold", fontSize=18, leading=20),
        "heading": ParagraphStyle("heading", fontName=f"{FONT}-Bold", fontSize=10, leading=12),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=9, leading=11),
        "body_bold": ParagraphStyle("body_bold", fontName=f"{FONT}-Bold", fontSize=9, leading=11),
        "small": ParagraphStyle("small", fontName=FONT, fontSize=8, leading=10),
        "tiny": ParagraphStyle("tiny", fontName=FONT, fontSize=7.3, leading=9),
    }


def _money(amount: float | str) -> str:
    if isinstance(amount, str):
        return amount
    return f"${amount:,.2f}"


def _build_pdf(output_path: str, story: list, *, producer: str = "DocuSuite Enterprise 4.2") -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    doc = BaseDocTemplate(
        str(tmp_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="template", frames=[frame])])
    doc.build(story)

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    creation = datetime.now().strftime("D:%Y%m%d%H%M%S")
    writer.add_metadata({"/Producer": producer, "/Creator": producer, "/CreationDate": creation})
    with open(output_path, "wb") as f:
        writer.write(f)
    tmp_path.unlink(missing_ok=True)


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
    s = _styles()
    story: list = []
    contract_end_date = contract_end_date or date(2032, 3, 10)
    payment_received_amount = payment_received_amount if payment_received_amount is not None else previous_balance
    current_charges = current_charges if current_charges is not None else total_amount_payable
    consumption_trend = consumption_trend or [("MAR", 638), ("APR", 702), ("MAY", 688), ("JUN", 716), ("JUL", 774), ("AUG", 689)]

    top = Table(
        [[
            Paragraph(provider_name, s["title"]),
            Paragraph(
                f"Bill Date: {bill_date.strftime('%d %b %Y').upper()}<br/>"
                f"PLE Account No.: {ple_account_no}<br/>"
                f"Statement No.: {statement_no}",
                s["body"],
            ),
        ]],
        colWidths=[100 * mm, 70 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]),
    )
    story.append(top)
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(customer_name, s["body_bold"]))
    for line in address_lines:
        story.append(Paragraph(line, s["body"]))
    story.append(Spacer(1, 4 * mm))

    summary_table = Table(
        [[
            Paragraph(_money(total_amount_payable), s["title"]),
            Paragraph("Total Amount Payable", s["heading"]),
            Paragraph(payment_due.strftime('%d %b %Y').upper(), s["title"]),
            Paragraph("Payment Due", s["heading"]),
        ]],
        colWidths=[35 * mm, 42 * mm, 35 * mm, 36 * mm],
        style=TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]),
    )
    story.append(summary_table)
    story.append(Spacer(1, 6 * mm))

    story.append(Paragraph("THIS IS YOUR TAX INVOICE FOR:", s["heading"]))
    for line in invoice_address_lines:
        story.append(Paragraph(line, s["body_bold"]))
    story.append(Spacer(1, 2 * mm))

    info_rows = [
        ["MSSL ACCOUNT NO.", mssl_account_no],
        ["BILLING PERIOD", f"{billing_period_start.strftime('%d %b %Y')} – {billing_period_end.strftime('%d %b %Y')}"] ,
        ["BILL DATE", bill_date.strftime('%d %b %Y')],
        ["ACCOUNT TYPE", account_type],
        ["DEPOSIT", _money(deposit)],
        ["CONTRACT END DATE", contract_end_date.strftime('%d %b %Y')],
    ]
    info_table = Table(
        [[Paragraph(k, s["body_bold"]), Paragraph(v, s["body"])] for k, v in info_rows],
        colWidths=[48 * mm, 98 * mm],
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]),
    )
    story.append(info_table)
    story.append(Spacer(1, 4 * mm))

    story.append(Paragraph("CONSUMPTION TREND", s["heading"]))
    trend_table = Table(
        [[Paragraph("ELECTRICITY", s["body_bold"])] + [Paragraph(month, s["tiny"]) for month, _ in consumption_trend],
         [Paragraph("kWh", s["body"])] + [Paragraph(str(value), s["body_bold"]) for _, value in consumption_trend]],
        colWidths=[28 * mm] + [20 * mm] * len(consumption_trend),
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
            ("ALIGN", (1, 0), (-1, -1), "CENTER"),
        ]),
    )
    story.append(trend_table)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(f"USAGE {usage_kwh} kWh (Rounded to whole number)", s["body_bold"]))
    story.append(Spacer(1, 2 * mm))

    story.append(Paragraph("SUMMARY OF CHARGES", s["heading"]))
    charges_rows = [
        ["Balance Brought Forward from Previous Bill", _money(previous_balance)],
        [f"Payment Received on/before {payment_received_date.strftime('%d %b %Y') if payment_received_date else '19 Aug 2026'} - Thank You", f"-{_money(payment_received_amount)}"],
        ["Outstanding Balance", _money(0.00)],
        ["Current Charges", _money(current_charges)],
    ]
    charges_table = Table(
        [[Paragraph(k, s["body"]), Paragraph(v, s["body_bold"])] for k, v in charges_rows],
        colWidths=[110 * mm, 36 * mm],
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#CCCCCC")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]),
    )
    story.append(charges_table)
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph(
        f"Total Amount Payable will be deducted from your {deduction_bank_name} bank account ending with {deduction_bank_account_last4} on {payment_due.strftime('%d %b %Y')}",
        s["small"],
    ))
    story.append(Paragraph(_money(total_amount_payable), s["title"]))

    _build_pdf(output_path, story)
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
