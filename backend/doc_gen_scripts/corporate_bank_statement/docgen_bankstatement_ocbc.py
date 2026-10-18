"""
docgen_bankstatement_ocbc.py — OCBC "Daily Statement of Account - Details" renderer.

Companion module to docgen_bankstatement.py (the DBS/UOB generator you already
have). Rather than editing that file, this drops in as a third bank profile +
renderer: import BANK_PROFILES/RENDERERS/BankProfile from your existing module,
register "OCBC" here, and reuse the same Case Registry / Bank Statements /
Individuals mastersheet flow and the same seeded transaction engine.

Usage (once wired into your existing generate()/main(), see bottom of file):

    python docgen_bankstatement_ocbc.py v9_document_mastersheet.xlsx --case C001 --outdir out/ --bank OCBC

Layout notes
------------
Unlike the DBS template (one block per calendar month) and the UOB template,
the OCBC export prints the *whole* statement period as a single continuous
listing, newest transaction first, with the running balance carried over from
the account's forward-chronological simulation (each row's balance is the
balance immediately after that transaction — same numbers as DBS/UOB would
show, just listed in reverse date order, which is how the real OCBC export
behaves).

The letterhead (logo + title + date range), the account info box (account
number, name, debit/credit totals, balances, BIC) and the footer (Printed
By / Page X of Y / Printed On / Co.Reg. / disclaimer) are redrawn identically
on every page — so "header details and footer show up on every page" is
satisfied by construction, not by a print-time trick. Because every page's
furniture is the same fixed height, one pagination pass over all rows (before
any page is drawn) gives an exact page count up front, so "Page X of Y" in
the footer is always accurate — no placeholder, no post-hoc patch.
"""

from __future__ import annotations

import random
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as pdfcanvas

# Reuse everything from your existing generator: layout base, bank profiles,
# the mastersheet loader, the seeded transaction engine, and the CLI plumbing.
#
# Prefer the dotted package import so this shares the exact same BANK_PROFILES/
# RENDERERS dict objects as whatever else already imported docgen_bankstatement.py
# that way (e.g. scripts/generate_from_mastersheet.py) -- registering "OCBC" and
# "ocbc" into a second, separately-imported copy of those module-level dicts would
# leave them invisible to callers using the first copy, silently undoing this
# module's whole purpose. Falls back to the bare import for standalone script use
# (`python docgen_bankstatement_ocbc.py ...` from this directory), where the dotted
# package path isn't importable.
try:
    from doc_gen_scripts.corporate_bank_statement.docgen_bankstatement import (
        PAGE_W, PAGE_H, FONT, FONT_B,
        BankProfile, BANK_PROFILES, RENDERERS,
        Block, Txn, seeded_rng, load_logo,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from docgen_bankstatement import (
        PAGE_W, PAGE_H, FONT, FONT_B,
        BankProfile, BANK_PROFILES, RENDERERS,
        Block, Txn, seeded_rng, load_logo,
    )

# --------------------------------------------------------------------------
# Bank profile
# --------------------------------------------------------------------------
# bic_code / bic_name are OCBC-specific fields. If your BankProfile dataclass
# doesn't have them yet, add:
#     bic_code: str = ""
#     bic_name: str = ""
# to its definition in docgen_bankstatement.py.

BANK_PROFILES["OCBC"] = BankProfile(
    name="OCBC",
    legal_name="Oversea-Chinese Banking Corporation Limited",
    address="65 Chulia Street, OCBC Centre, Singapore 049513",
    website="www.ocbc.com",
    footer_reg="Co.Reg.no.:193200032W",
    product="Daily Statement of Account - Details",
    tagline=("", ""),
    logo_path=str(Path(__file__).resolve().parent.parent / "assets" / "images" / "ocbc-logo.png"),
    logo_style="plain",
    template="ocbc",
    bic_code="OCBCSGSGXXX",
    bic_name="OVERSEA-CHINESE BANKING CORPORATION LIMITED",
)

OCBC_RED = colors.HexColor("#E4002B")

# --------------------------------------------------------------------------
# Layout constants (points; 15mm margins, matching the reference HTML)
# --------------------------------------------------------------------------

O_M_L, O_M_R = 42.52, 42.52
O_TOP = PAGE_H - O_M_L
O_BOTTOM = O_M_L
O_HEADER_H = 55                       # logo box + gap before the info box
O_TABLEHEAD_H = 40
O_FOOTER_H = 140                      # printed-by/page table + co-reg + disclaimer
O_INFO_ROW_H = 15.6

O_COL_DATE_X = O_M_L
O_COL_DATE_W = 70
O_COL_REF_X = O_COL_DATE_X + O_COL_DATE_W
O_COL_REF_W = 150
O_COL_CLIENTREF_X = O_COL_REF_X + O_COL_REF_W
O_COL_CLIENTREF_W = 90
O_COL_DEBIT_W = 65
O_COL_CREDIT_W = 65
O_COL_BAL_W = 70
O_COL_DEBIT_R = O_COL_CLIENTREF_X + O_COL_CLIENTREF_W + O_COL_DEBIT_W
O_COL_CREDIT_R = O_COL_DEBIT_R + O_COL_CREDIT_W
O_COL_BAL_R = O_COL_CREDIT_R + O_COL_BAL_W

OCBC_DISCLAIMER = (
    "The availability of balances for utilization is subject to the facility limits "
    "for omnibus and/or shared facilities, if any, that apply to you and you may not be "
    "entitled to utilize the full amount of the balances shown herein if they exceed such "
    "facility limits. For information on the availability of account balances for "
    "utilization, please contact our call centre at +65 1800 363 3333. Singapore dollar "
    "deposits of non-bank depositors and monies and deposits denominated in Singapore "
    "dollars under the Supplementary Retirement Scheme are insured by the Singapore "
    "Deposit Insurance Corporation, for up to S$75,000 in aggregate per depositor per "
    "Scheme member by law. Foreign currency deposits, dual currency investments, "
    "structured deposits and other investment products are not insured."
)


# --------------------------------------------------------------------------
# Row model
# --------------------------------------------------------------------------

@dataclass
class OcbcRow:
    post_date: date
    value_date: date
    ref_lines: list[str]
    client_ref: str
    debit: float | None
    credit: float | None
    balance: float


def format_account_no_ocbc(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return (f"{digits[:3]}-{digits[3:6]}-{digits[6:9]}-{digits[9:]}"
            if len(digits) >= 10 else raw)


def _ocbc_narrative(t: Txn, rng: random.Random) -> tuple[list[str], str]:
    """Rewrite a DBS-flavoured narrative into OCBC's Bank Ref / Desc / Details shape."""
    ref = f"{rng.randint(10**9, 10**10 - 1):016d}"
    head = t.lines[0]
    if head.startswith("Inward PayNow"):
        return [ref, "FAST PAYMENT", t.lines[-1]], "Inward Funds Transfer"
    if head.startswith("REMITTANCE"):
        return [ref, "GIRO", t.lines[-1]], "Outward Remittance"
    if head.startswith("FAST PAYMENT SALARY"):
        return [ref, "FAST PAYMENT", t.lines[-1]], "Salary Payment"
    if head.startswith("SERVICE CHARGE FOR"):
        return [ref, "CHARGES", f"Billing Statement {ref}"], "Txn Charges Billing"
    if head.startswith("GIRO"):
        label = "Inward Cr - Giro" if t.deposit else "Giro Payment"
        return [ref, "GIRO", t.lines[-1]], label
    if head.startswith("BUSINESS ADVANCE CARD"):
        return [ref, "CARD", t.lines[-1]], "Card Transaction"
    if head.startswith("SERVICE CHARGE"):
        return [ref, "CHARGES", f"Billing Statement {ref}"], "Txn Charges Billing"
    return [ref, head, ""], "Transaction"


def to_ocbc_rows(blocks: list[Block], rng: random.Random) -> list[OcbcRow]:
    """Flatten the shared transaction model, newest-first, into OCBC row shape."""
    rows: list[OcbcRow] = []
    for block in blocks:
        for t in block.txns:
            ref_lines, client_ref = _ocbc_narrative(t, rng)
            rows.append(OcbcRow(
                post_date=t.txn_date,
                value_date=t.value_date,
                ref_lines=ref_lines,
                client_ref=client_ref,
                debit=t.withdrawal,
                credit=t.deposit,
                balance=t.balance,
            ))
    rows.reverse()
    return rows


def _ocbc_row_lines(row: OcbcRow) -> list[str]:
    wrapped: list[str] = []
    for line in row.ref_lines:
        wrapped.extend(simpleSplit(line, FONT, 9, O_COL_REF_W - 8) or [""])
    return wrapped


def _ocbc_row_height(row: OcbcRow) -> float:
    n_lines = max(2, len(_ocbc_row_lines(row)))
    return n_lines * 13.5 + 6


def ocbc_info_box_height() -> float:
    # +10 reserves room for "BIC Name" (the bank's full legal name) wrapping onto
    # a second line in the last row -- it's too long to fit on one line.
    return 5 * O_INFO_ROW_H + 11 + 10


# --------------------------------------------------------------------------
# Per-page drawing — header, info box, table head/rows, footer.
# Each of these is called once per page inside render_ocbc()'s loop, which is
# what makes the header and footer repeat on every page.
# --------------------------------------------------------------------------

def ocbc_draw_header(c: pdfcanvas.Canvas, ctx: dict[str, Any]) -> float:
    bank = ctx["bank"]
    logo_w, logo_h = 119, 45          # ~42mm x 16mm, matching the reference HTML
    x_logo, y_logo = O_M_L, O_TOP - logo_h
    logo = load_logo(bank.logo_path) if bank.logo_path else None

    if logo is not None:
        iw, ih = logo.getSize()
        scale = min(logo_w / iw, logo_h / ih)
        dw, dh = iw * scale, ih * scale
        c.drawImage(logo, x_logo + (logo_w - dw) / 2, y_logo + (logo_h - dh) / 2,
                    width=dw, height=dh, mask="auto")
    else:
        c.setFillColor(OCBC_RED)
        c.setFont(FONT_B, 22)
        c.drawString(x_logo, y_logo + logo_h / 2 - 8, bank.name)

    c.setFillColor(colors.black)
    c.setFont(FONT_B, 15)
    c.drawCentredString(PAGE_W / 2, O_TOP - 16, bank.product)
    first, last = ctx["period"]
    c.setFont(FONT_B, 12)
    c.drawCentredString(PAGE_W / 2, O_TOP - 33,
                        f"From {first:%d %b %Y} To {last:%d %b %Y}")
    return O_TOP - O_HEADER_H


def ocbc_draw_info_box(c: pdfcanvas.Canvas, ctx: dict[str, Any], y: float) -> float:
    h = ocbc_info_box_height()
    w = PAGE_W - O_M_L - O_M_R
    col_w = w / 2
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.8)
    c.rect(O_M_L, y - h, w, h, stroke=1, fill=0)

    left = [
        ("Account Number", f"{ctx['account_no_fmt']} - SGD", None),
        ("Account Name", ctx["company_name"], None),
        ("Debits", (str(ctx["debit_count"]), f"S${ctx['total_debit']:,.2f}"), None),
        ("Credits", (str(ctx["credit_count"]), f"S${ctx['total_credit']:,.2f}"), None),
        ("BIC Code", ctx["bank"].bic_code, None),
    ]
    right = [
        ("Opening Balance", f"{ctx['opening']:,.2f}", ctx["period_start"]),
        ("Ledger Balance", f"{ctx['closing']:,.2f}", ctx["period_end"]),
        ("Available Balance", f"{ctx['closing']:,.2f}", ctx["period_end"]),
        ("Total Hold Amount", "0.00", ctx["period_end"]),
        ("BIC Name", ctx["bank"].bic_name, None),
    ]

    def draw_col(items: list[tuple[str, Any, date | None]], x0: float, numeric: bool) -> None:
        yy = y - 17
        for label, value, dt in items:
            c.setFillColor(colors.black)
            c.setFont(FONT_B, 8.5)
            c.drawString(x0 + 8, yy, label)
            c.setFont(FONT, 8.5)
            if numeric and dt is not None:
                c.drawRightString(x0 + col_w - 70, yy, value)
                c.drawRightString(x0 + col_w - 8, yy, f"{dt:%d-%b-%Y}")
            elif isinstance(value, tuple):
                count_str, amount_str = value
                cx = x0 + 118
                c.drawString(cx, yy, count_str)
                cx += stringWidth(count_str, FONT, 8.5) + 22
                c.setFont(FONT_B, 8.5)
                c.drawString(cx, yy, "Amount")
                cx += stringWidth("Amount", FONT_B, 8.5) + 6
                c.setFont(FONT, 8.5)
                c.drawString(cx, yy, amount_str)
            else:
                max_lines = 2 if label == "BIC Name" else 1
                lines = (simpleSplit(value, FONT, 8.5, col_w - 126) or [""])[:max_lines]
                for j, wrapped in enumerate(lines):
                    c.drawString(x0 + 118, yy - j * 10, wrapped)
            yy -= O_INFO_ROW_H

    draw_col(left, O_M_L, numeric=False)
    draw_col(right, O_M_L + col_w, numeric=True)
    return y - h


def ocbc_draw_table_head(c: pdfcanvas.Canvas, y: float) -> float:
    top = y
    w = PAGE_W - O_M_L - O_M_R
    c.setFillColor(colors.black)
    c.setFont(FONT_B, 9)
    c.drawString(O_COL_DATE_X + 4, y - 10, "Post Date")
    c.drawString(O_COL_REF_X + 4, y - 10, "Bank Ref.")
    c.drawString(O_COL_CLIENTREF_X + 4, y - 10, "Client Ref.")
    c.drawRightString(O_COL_DEBIT_R - 4, y - 10, "Debit Amount")
    c.drawRightString(O_COL_CREDIT_R - 4, y - 10, "Credit Amount")
    c.drawRightString(O_COL_BAL_R - 4, y - 10, "Balance")

    c.setFont(FONT, 9)
    c.drawString(O_COL_DATE_X + 4, y - 22, "Value Date")
    c.drawString(O_COL_REF_X + 4, y - 22, "Transaction Desc.")
    c.drawString(O_COL_REF_X + 4, y - 32, "Details")

    bottom = y - O_TABLEHEAD_H
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.8)
    c.line(O_M_L, bottom, O_M_L, top)
    c.line(O_M_L + w, bottom, O_M_L + w, top)
    c.line(O_M_L, bottom, O_M_L + w, bottom)
    return bottom


def ocbc_draw_row(c: pdfcanvas.Canvas, row: OcbcRow, y: float) -> float:
    wrapped = _ocbc_row_lines(row)
    h = _ocbc_row_height(row)
    top = y
    w = PAGE_W - O_M_L - O_M_R

    c.setFont(FONT, 9)
    c.setFillColor(colors.black)
    c.drawString(O_COL_DATE_X + 4, top - 11, row.post_date.strftime("%d %b %Y"))
    c.drawString(O_COL_DATE_X + 4, top - 24.5, row.value_date.strftime("%d %b %Y"))

    yy = top - 11
    for line in wrapped:
        c.drawString(O_COL_REF_X + 4, yy, line)
        yy -= 13.5

    c.drawString(O_COL_CLIENTREF_X + 4, top - 11, row.client_ref)
    if row.debit is not None:
        c.drawRightString(O_COL_DEBIT_R - 4, top - 11, f"{row.debit:,.2f}")
    if row.credit is not None:
        c.drawRightString(O_COL_CREDIT_R - 4, top - 11, f"{row.credit:,.2f}")
    c.drawRightString(O_COL_BAL_R - 4, top - 11, f"{row.balance:,.2f}")

    bottom = top - h
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.8)
    c.line(O_M_L, bottom, O_M_L, top)
    c.line(O_M_L + w, bottom, O_M_L + w, top)
    c.line(O_M_L, bottom, O_M_L + w, bottom)
    return bottom


def ocbc_draw_footer(c: pdfcanvas.Canvas, ctx: dict[str, Any], page_no: int, total: int) -> None:
    w = PAGE_W - O_M_L - O_M_R
    y = O_BOTTOM + O_FOOTER_H

    c.setStrokeColor(colors.black)
    c.setLineWidth(0.8)
    c.rect(O_M_L, y - 30, w, 30, stroke=1, fill=0)
    c.line(O_M_L, y - 15, O_M_L + w, y - 15)

    c.setFillColor(colors.black)
    c.setFont(FONT_B, 9)
    c.drawString(O_M_L + 6, y - 11, "Printed By")
    c.setFont("Helvetica-BoldOblique", 9)
    printed_by = ctx["director"]["Name (auto/fill)"].upper() if ctx["director"] else "SYSTEM"
    c.drawString(O_M_L + 60, y - 11, printed_by)
    c.setFont(FONT_B, 9)
    c.drawRightString(O_M_L + w - 6, y - 11, f"Page {page_no} of {total}")

    c.drawString(O_M_L + 6, y - 26, "Printed On")
    c.setFont("Helvetica-BoldOblique", 9)
    c.drawString(O_M_L + 60, y - 26, f"{ctx['export_stamp']:%d %b %Y %H:%M:%S}")

    c.setFillColor(colors.black)
    c.setFont(FONT, 7.5)
    c.drawString(O_M_L, y - 42, ctx["bank"].footer_reg)

    c.setFillColor(colors.HexColor("#333333"))
    c.setFont(FONT, 7.5)
    yy = y - 56
    for line in simpleSplit(OCBC_DISCLAIMER, FONT, 7.5, w):
        c.drawString(O_M_L, yy, line)
        yy -= 11.25


# --------------------------------------------------------------------------
# Renderer entry point
# --------------------------------------------------------------------------

def render_ocbc(ctx: dict[str, Any], blocks: list[Block], out_pdf: Path) -> Path:
    rng = seeded_rng(ctx["case_id"] + "|ocbc-refs")
    rows = to_ocbc_rows(blocks, rng)
    all_txns = [t for b in blocks for t in b.txns]

    ctx["account_no_fmt"] = format_account_no_ocbc(ctx["account_no"])
    ctx["opening"] = blocks[0].opening
    ctx["closing"] = blocks[-1].closing
    ctx["debit_count"] = sum(1 for t in all_txns if t.withdrawal is not None)
    ctx["total_debit"] = round(sum(t.withdrawal or 0 for t in all_txns), 2)
    ctx["credit_count"] = sum(1 for t in all_txns if t.deposit is not None)
    ctx["total_credit"] = round(sum(t.deposit or 0 for t in all_txns), 2)

    period_start = ctx["months"][0]
    last_month = ctx["months"][-1]
    period_end = (date(last_month.year + (last_month.month == 12),
                       (last_month.month % 12) + 1, 1) - timedelta(days=1))
    ctx["period"] = (period_start, period_end)
    ctx["period_start"] = period_start
    ctx["period_end"] = period_end

    rng_stamp = seeded_rng(ctx["case_id"] + "|ocbc-export")
    ctx["export_stamp"] = datetime.combine(
        period_end + timedelta(days=rng_stamp.randint(1, 5)),
        datetime.min.time().replace(hour=rng_stamp.randint(8, 21),
                                    minute=rng_stamp.randint(0, 59),
                                    second=rng_stamp.randint(0, 59)),
    )

    # Header + info box + table head are redrawn identically on every page, so the
    # per-page row budget is constant — no first-page/continuation-page split needed.
    content_top = O_TOP - O_HEADER_H - ocbc_info_box_height() - O_TABLEHEAD_H
    content_bottom = O_BOTTOM + O_FOOTER_H
    budget_per_page = content_top - content_bottom

    pages: list[list[OcbcRow]] = []
    current: list[OcbcRow] = []
    budget = budget_per_page
    for row in rows:
        h = _ocbc_row_height(row)
        if current and budget - h < 0:
            pages.append(current)
            current, budget = [], budget_per_page
        budget -= h
        current.append(row)
    pages.append(current)  # always at least one page, even with zero transactions

    total_pages = len(pages)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = pdfcanvas.Canvas(str(out_pdf), pagesize=A4)
    c.setTitle(f"Daily Statement of Account - Details — {ctx['company_name']}")
    c.setAuthor(ctx["bank"].legal_name)

    for i, page_rows in enumerate(pages, start=1):
        y = ocbc_draw_header(c, ctx)
        y = ocbc_draw_info_box(c, ctx, y)
        y = ocbc_draw_table_head(c, y)
        for row in page_rows:
            y = ocbc_draw_row(c, row, y)
        ocbc_draw_footer(c, ctx, i, total_pages)
        c.showPage()

    c.save()
    return out_pdf


RENDERERS["ocbc"] = render_ocbc


if __name__ == "__main__":
    # Delegates to your existing CLI (docgen_bankstatement.main), now with the
    # OCBC profile and renderer registered above.
    from docgen_bankstatement import main
    main()
