#!/usr/bin/env python3
"""generate_telecom.py — Company telecom bill (StarHub), used as Proof of
Address (Company) in the "Optional Documents" mastersheet tab (the rows whose
Filename contains "telecom" -- the electricity variant lives in
../utility/generate_bills.py).

Rewritten from an HTML/CSS + headless-Chrome pipeline (bill.html/bill.css, kept
alongside this file as the design reference) to pure ReportLab canvas, in the
same house style as ../utility/generate_bills.py: same helper functions, same
MARGIN=40 fixed one-page-invoice approach. Layout/wording/colours are copied
from bill.html/bill.css section by section; only the figures with no
mastersheet column (account numbers, charges, dates) are synthesized, seeded
per case+filename so reruns are reproducible -- same computation this document
type has always used (see build_telecom_html() in the old
generate_optional_docs.py), only the renderer changed.

Usage:
cd backend/doc_gen_scripts/optional_documents/telecom
python generate_telecom.py ../../v15_document_mastersheet.xlsx --outdir out/
python generate_telecom.py ../../v15_document_mastersheet.xlsx --outdir out/ --cases C042

Run with no arguments to build one example bill at output/telecom_example.pdf.
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
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas as pdfcanvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import truth  # noqa: E402
from case_facts import seeded_rng  # noqa: E402

PAGE_W, PAGE_H = A4
MARGIN = 40
CONTENT_W = PAGE_W - 2 * MARGIN
FONT = "Helvetica"
FONT_B = "Helvetica-Bold"

GREEN = colors.HexColor("#99ca3c")
GREY_LABEL = colors.HexColor("#888888")
GREY_BORDER = colors.HexColor("#e3e3e3")
GREY_FILL = colors.HexColor("#f4f4f4")
GREY_FILL_SOFT = colors.HexColor("#fafafa")
TOTAL_FILL = colors.HexColor("#f7f7f7")
OVERDUE_RED = colors.HexColor("#c0392b")
MUTED = colors.HexColor("#999999")
INK = colors.HexColor("#222222")
BLACK = colors.black

ASSETS = Path(__file__).resolve().parent
SHARED_ASSETS = ASSETS.parent.parent / "assets" / "images"
LOGO_PATH = SHARED_ASSETS / "starhub_logo.png"
DEFAULT_OUT = ASSETS / "output" / "telecom_example.pdf"

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


def sg_short_date(value: date) -> str:
    return f"{value.day:02d}/{value.month:02d}/{value.year % 100:02d}"


def money(value: float) -> str:
    return f"{value:,.2f}"


def _build_pdf(output_path: str, draw_fn, *, synthetic_tag: bool = True,
               producer: str = "StarHub eBill") -> None:
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


def _text(c: pdfcanvas.Canvas, text: str, x: float, y: float, *, font=FONT, size=8.5,
          color=INK, align: str = "left") -> None:
    c.setFillColor(color)
    c.setFont(font, size)
    if align == "right":
        c.drawRightString(x, y, text)
    elif align == "center":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)


def _wrapped(c: pdfcanvas.Canvas, text: str, x: float, top: float, width: float, *,
             font=FONT, size=8.5, leading=None, color=INK) -> float:
    leading = leading or size * 1.4
    c.setFillColor(color)
    c.setFont(font, size)
    y = top
    for line in simpleSplit(text, font, size, width):
        c.drawString(x, y, line)
        y -= leading
    return y


def _box(c: pdfcanvas.Canvas, x: float, y: float, w: float, h: float,
         *, border=GREY_BORDER, fill=None) -> None:
    if fill is not None:
        c.setFillColor(fill)
        c.rect(x, y, w, h, fill=1, stroke=0)
    c.setStrokeColor(border)
    c.setLineWidth(0.7)
    c.rect(x, y, w, h, fill=0, stroke=1)


def _logo() -> ImageReader | None:
    return ImageReader(str(LOGO_PATH)) if LOGO_PATH.exists() else None


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------

def generate_telecom_bill(
    output_path: str,
    *,
    brand_name: str,
    bill_period_label: str,
    customer_name: str,
    account_number: str,
    address_line_1: str,
    address_line_2: str,
    postal_code: str,
    bill_number: str,
    date_of_bill: date,
    due_date: date,
    payment_code: str,
    gst_reg_no: str,
    previous_balance: float,
    overdue_amount: float,
    current_charges: float,
    total_outstanding: float,
    current_charges_due_date: date,
    gst_amount: float,
    non_taxable_charges: float,
    total_savings: float,
    payment_cutoff_date: date,
    company_letterhead: str,
    enquiry_phone: str,
    enquiry_email: str,
    registration_no: str,
    synthetic_tag: bool = True,
) -> dict:
    def draw(c: pdfcanvas.Canvas) -> None:
        left, right = MARGIN, PAGE_W - MARGIN
        y = PAGE_H - MARGIN

        # --- Topbar: logo left, doc meta right ---
        logo = _logo()
        if logo is not None:
            logo_w = 70
            logo_h = logo_w * 380 / 1000
            c.drawImage(logo, left, y - logo_h, width=logo_w, height=logo_h, mask="auto")
        _text(c, f"{brand_name} Monthly Bill", right, y - 8, size=7.5, color=GREY_LABEL, align="right")
        _text(c, bill_period_label, right, y - 8 - 7.5 * 1.45, size=7.5, color=GREY_LABEL, align="right")
        y -= 34

        _text(c, "Your Account at a Glance", left, y, font=FONT_B, size=12.5)
        y -= 16

        # --- Info table: 4 rows x (label, value, label, value) ---
        lbl_w = CONTENT_W * 0.16
        val_w = CONTENT_W * 0.34
        col_x = [left, left + lbl_w, left + lbl_w + val_w, left + lbl_w + val_w + lbl_w]
        row_h = 17
        info_rows = [
            ("Client Name", customer_name, "Account No.", account_number),
            ("Billing Address", f"{address_line_1} {address_line_2}, Singapore {postal_code}", "Bill No.", bill_number),
            ("Date of Bill", sg_short_date(date_of_bill), "Due Date", sg_short_date(due_date)),
            ("Payment Code", payment_code, "GST Reg No.", gst_reg_no),
        ]
        table_top = y
        for r, (l1, v1, l2, v2) in enumerate(info_rows):
            ry = table_top - r * row_h
            _box(c, col_x[0], ry - row_h, lbl_w, row_h, fill=GREY_FILL_SOFT)
            _box(c, col_x[1], ry - row_h, val_w, row_h)
            _box(c, col_x[2], ry - row_h, lbl_w, row_h, fill=GREY_FILL_SOFT)
            _box(c, col_x[3], ry - row_h, val_w, row_h)
            _text(c, l1, col_x[0] + 5, ry - row_h + 6, size=7.5, color=GREY_LABEL)
            _text(c, v1, col_x[1] + 5, ry - row_h + 6, font=FONT_B, size=7.5)
            _text(c, l2, col_x[2] + 5, ry - row_h + 6, size=7.5, color=GREY_LABEL)
            _text(c, v2, col_x[3] + 5, ry - row_h + 6, font=FONT_B, size=7.5)
        y = table_top - len(info_rows) * row_h - 1

        # --- Metrics strip: 4 equal cells ---
        metric_h = 30
        cell_w = CONTENT_W / 4
        metrics = [
            ("Previous Balance", money(previous_balance), False),
            ("Overdue Amount", money(overdue_amount), True),
            ("Current Charges", money(current_charges), False),
            ("Total Outstanding", money(total_outstanding), False),
        ]
        for i, (label, value, is_overdue) in enumerate(metrics):
            mx = left + i * cell_w
            _box(c, mx, y - metric_h, cell_w, metric_h)
            _text(c, label, mx + 8, y - 11, size=7.5, color=colors.HexColor("#555555"))
            _text(c, f"${value}", mx + 8, y - 24, font=FONT_B, size=11, color=OVERDUE_RED if is_overdue else INK)
        y -= metric_h + 14

        # --- Section head ---
        c.setFillColor(GREEN)
        c.rect(left, y - 9, 2.2, 11, fill=1, stroke=0)
        _text(c, "Charges this period", left + 8, y - 6, font=FONT_B, size=10.5)
        y -= 20

        # --- Data table ---
        col_amt_x = right
        header_h = 16
        _box(c, left, y - header_h, CONTENT_W, header_h, fill=GREY_FILL)
        _text(c, "Description", left + 6, y - header_h + 5, font=FONT_B, size=8, color=colors.HexColor("#555555"))
        _text(c, "Amount (SGD)", col_amt_x - 6, y - header_h + 5, font=FONT_B, size=8, color=colors.HexColor("#555555"), align="right")
        y -= header_h

        data_rows: list[tuple[str, str, str]] = [
            ("Previous Balance", f"${money(previous_balance)}", ""),
            ("Overdue Amount", f"${money(overdue_amount)}", "To be paid immediately"),
            (f"Current Charges Due on {sg_short_date(current_charges_due_date)}", f"${money(current_charges)}",
             f"Total GST ${money(gst_amount)}   Non-taxable charges ${money(non_taxable_charges)}"),
            ("Total Savings this Month", f"-${money(total_savings)}", ""),
        ]
        for label, amount, note in data_rows:
            rh = 26 if note else 17
            c.setStrokeColor(GREY_BORDER)
            c.setLineWidth(0.6)
            c.line(left, y - rh, right, y - rh)
            _text(c, label, left + 6, y - 12, size=8)
            _text(c, amount, col_amt_x - 6, y - 12, size=8, align="right")
            if note:
                _text(c, note, left + 6, y - 22, size=6.8, color=MUTED)
            y -= rh

        total_h = 18
        _box(c, left, y - total_h, CONTENT_W, total_h, fill=TOTAL_FILL)
        c.setStrokeColor(colors.HexColor("#cccccc"))
        c.line(left, y, right, y)
        c.line(left, y - total_h, right, y - total_h)
        _text(c, "Total Outstanding Balance", left + 6, y - total_h + 6, font=FONT_B, size=8.5)
        _text(c, f"${money(total_outstanding)}", col_amt_x - 6, y - total_h + 6, font=FONT_B, size=8.5, align="right")
        y -= total_h + 14

        # --- Legal note ---
        c.setStrokeColor(GREEN)
        c.setLineWidth(1)
        c.line(left, y, left + CONTENT_W * 0.38, y)
        y -= 10
        note_text = (f"Payment received on or after {sg_short_date(payment_cutoff_date)} "
                     "will not be shown in this statement.")
        y = _wrapped(c, note_text, left, y, CONTENT_W, size=8, leading=11.5, color=colors.HexColor("#333333"))
        y -= 8

        # --- Promo box ---
        promo_head_h = 15
        promo_body_h = 40
        promo_h = promo_head_h + promo_body_h
        _box(c, left, y - promo_h, CONTENT_W, promo_h)
        _box(c, left, y - promo_head_h, CONTENT_W, promo_head_h, fill=GREY_FILL)
        _text(c, "Promotion and Information", left + 8, y - promo_head_h + 5, font=FONT_B, size=8)
        py = y - promo_head_h - 10
        py = _wrapped(c, ("Roam on Conexus Mobile Alliance Networks and enjoy special rates in Hong Kong, "
                          "Korea, Taiwan, Indonesia, Philippines or Japan. Details at www.starhub.com/roaming. "
                          "Terms and Conditions apply."), left + 8, py, CONTENT_W - 16, size=7.8, leading=10.5,
                     color=colors.HexColor("#333333"))
        py -= 4
        _wrapped(c, ("StarHub TV on Mobile lets you watch sports and news on the go. "
                     "SMS <LiveTV> to 6757 to subscribe or visit www.starhub.com/tv."),
                 left + 8, py, CONTENT_W - 16, size=7.8, leading=10.5, color=colors.HexColor("#333333"))
        y -= promo_h + 14

        # --- Slip: remittance box + card/GIRO box ---
        slip_gap = 8
        box1_w = (CONTENT_W - slip_gap) * (1.15 / 2.0)
        box2_w = CONTENT_W - slip_gap - box1_w
        box1_x = left
        box2_x = left + box1_w + slip_gap
        slip_h = 150
        _box(c, box1_x, y - slip_h, box1_w, slip_h)
        _box(c, box2_x, y - slip_h, box2_w, slip_h)
        _box(c, box1_x, y - 14, box1_w, 14, fill=GREY_FILL)
        _box(c, box2_x, y - 14, box2_w, 14, fill=GREY_FILL)
        _text(c, "Remittance slip", box1_x + 6, y - 10, font=FONT_B, size=7.8)
        _text(c, "Card / GIRO payment", box2_x + 6, y - 10, font=FONT_B, size=7.8)

        r1y = y - 14 - 12
        remit_rows = [
            ("Payee", f"{brand_name} Ltd"),
            ("Client", customer_name),
            ("Account No.", account_number),
            ("Payment Code", payment_code),
            ("Current charges due", sg_short_date(current_charges_due_date)),
            ("Total outstanding", f"${money(total_outstanding)}"),
        ]
        for label, value in remit_rows:
            _text(c, label, box1_x + 6, r1y, size=7.2)
            _text(c, value, box1_x + box1_w - 6, r1y, font=FONT_B, size=7.2, align="right")
            r1y -= 11
        r1y -= 3
        for label in ("Bank", "Cheque No."):
            _text(c, label, box1_x + 6, r1y, size=7.2)
            c.setStrokeColor(colors.HexColor("#cccccc"))
            c.line(box1_x + 60, r1y - 1, box1_x + box1_w - 6, r1y - 1)
            r1y -= 13
        r1y -= 2
        _wrapped(c, f'Cheque payable to "{brand_name} Ltd". Robinson Road Post Office, P. O. Box No. 61, Singapore 900131',
                 box1_x + 6, r1y, box1_w - 12, size=6.8, leading=9, color=MUTED)

        r2y = y - 14 - 12
        c.setStrokeColor(colors.HexColor("#999999"))
        c.setLineWidth(0.7)
        c.rect(box2_x + 6, r2y - 6, 6.5, 6.5, fill=0, stroke=1)
        _text(c, "Pay this bill", box2_x + 17, r2y - 1, font=FONT_B, size=7.5)
        r2y -= 18
        c.rect(box2_x + 6, r2y - 6, 6.5, 6.5, fill=0, stroke=1)
        _text(c, "Pay this and future bills", box2_x + 17, r2y - 1, font=FONT_B, size=7.5)
        _text(c, "Monthly charges will be billed to your card.", box2_x + 17, r2y - 10, size=6.8, color=MUTED)
        r2y -= 24
        for label in ("Card No.", "Expiry", "Signature"):
            _text(c, label, box2_x + 6, r2y, size=7.2)
            c.setStrokeColor(colors.HexColor("#cccccc"))
            c.line(box2_x + 55, r2y - 1, box2_x + box2_w - 6, r2y - 1)
            r2y -= 13
        r2y -= 4
        giro_h = 18
        _box(c, box2_x + 6, r2y - giro_h, box2_w - 12, giro_h, fill=TOTAL_FILL)
        _text(c, "Pay by GIRO and get 100 free SMS", box2_x + 12, r2y - giro_h + 6, font=FONT_B, size=7.5)

        y -= slip_h + 16

        # --- Barcodes (decorative) ---
        bc_rng = seeded_rng(f"{account_number}|telecom-barcode")
        bc_h = 14
        bc_w = min(120, (CONTENT_W - 24) / 2)
        for bx0 in (left, left + bc_w + 24):
            bx = bx0
            c.setFillColor(BLACK)
            while bx < bx0 + bc_w:
                w = bc_rng.choice([0.6, 0.8, 1.0, 1.4, 1.8])
                c.rect(bx, y - bc_h, w, bc_h, fill=1, stroke=0)
                bx += w + 0.8
        y -= bc_h + 14

        # --- Footer ---
        foot_y = MARGIN + 4
        _text(c, company_letterhead, left, foot_y + 9, size=6.8, color=colors.HexColor("#9a9a9a"))
        _text(c, f"Enquiries: {enquiry_phone}    {enquiry_email}    Reg. No. {registration_no}",
              left, foot_y, size=6.8, color=colors.HexColor("#9a9a9a"))
        _text(c, "Page 1 of 1", right, foot_y, size=6.8, color=colors.HexColor("#9a9a9a"), align="right")

    _build_pdf(output_path, draw, synthetic_tag=synthetic_tag)
    return {"output_path": output_path, "customer_name": customer_name, "account_number": account_number}


# --------------------------------------------------------------------------
# Field computation -- same formulas as build_telecom_html() in the old
# generate_optional_docs.py, only bill_day is now anchored the same way
# ../utility/generate_bills.py anchors its bill_date (relative to "today"
# rather than a fixed DOC_YEAR/DOC_MONTH), since Optional Documents rows
# carry no date column of their own for POA-type rows.
# --------------------------------------------------------------------------

def synthesize_telecom_bill_data(case_id: str, filename: str, *, application_date: date | None = None) -> dict:
    rng = seeded_rng(f"{case_id}|{filename}|telecom")
    application_date = application_date or datetime.now().date()

    bill_day = application_date - timedelta(days=rng.randint(2, 10))
    due = bill_day + timedelta(days=14)
    cutoff = bill_day - timedelta(days=3)
    previous = round(rng.uniform(80.0, 260.0), 2)
    overdue = round(rng.choice([0.0, 0.0, 0.0, rng.uniform(12.0, 48.0)]), 2)
    current = round(rng.uniform(95.0, 280.0), 2)
    gst = round(current * 9 / 109, 2)
    nontax = round(rng.uniform(0.0, 8.5), 2)
    savings = round(rng.choice([0.0, 5.0, 10.0, 15.0, 20.0]), 2)
    outstanding = round(previous + overdue + current - savings, 2)

    return dict(
        brand_name="StarHub",
        bill_period_label=f"{bill_day.day:02d} {bill_day.strftime('%b')} {bill_day.year}",
        account_number=f"1.{rng.randint(11000000, 19999999)}K",
        bill_number=f"{rng.randint(1000000000000000, 1999999999999999)}",
        date_of_bill=bill_day,
        due_date=due,
        payment_code=str(rng.randint(1000000, 9999999)),
        gst_reg_no="M2-0092055-C",
        previous_balance=previous,
        overdue_amount=overdue,
        current_charges=current,
        total_outstanding=outstanding,
        current_charges_due_date=due,
        gst_amount=gst,
        non_taxable_charges=nontax,
        total_savings=savings,
        payment_cutoff_date=cutoff,
        company_letterhead="StarHub Ltd 51 Cuppage Road #07-00 StarHub Centre Singapore 229469",
        enquiry_phone="(65) 6820 1637",
        enquiry_email="customerservice@starhub.com",
        registration_no="199802208E",
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

    data = synthesize_telecom_bill_data(case_id, filename)
    return dict(
        customer_name=company_name.upper(),
        address_line_1=street,
        address_line_2=unit,
        postal_code=postal or "000000",
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
        if "telecom" not in filename.lower():
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
        filename = str(row.get("Filename") or f"{row.get('Case ID')}_TelecomBill.pdf")
        path = Path(outdir) / filename
        persona = build_persona(row)
        generate_telecom_bill(str(path), synthetic_tag=synthetic_tag, **persona)
        truth.record(path, "proof_of_address", truth.telecom_bill(persona), derived={"entity_type"})
        written.append(path)
        print(f"{row.get('Case ID')}  {filename}  {row.get('Company Name (auto)')}")
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate StarHub-style company telecom bill PDFs.")
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
            "Filename": "telecom_example.pdf",
            "Company Name (auto)": "Harbourview Trading Pte. Ltd.",
            "Company Address (auto)": "10 Anson Road, #18-12 International Plaza, Singapore 079903",
        })
        print(generate_telecom_bill(str(DEFAULT_OUT), **persona))
        return
    args.outdir.mkdir(parents=True, exist_ok=True)
    written = generate(args.xlsx, args.outdir, cases=args.cases, synthetic_tag=not args.no_synthetic_tag)
    print(f"Wrote {len(written)} telecom bill PDF(s) to {args.outdir}")


if __name__ == "__main__":
    main()
