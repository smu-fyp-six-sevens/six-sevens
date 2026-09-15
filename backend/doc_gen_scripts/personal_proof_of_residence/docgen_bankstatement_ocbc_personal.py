"""
docgen_bankstatement_ocbc_personal.py — OCBC personal "Statement of Account" renderer
(monthly savings account, bilingual EN/ZH column headers).

Companion module to docgen_bankstatement.py, in the same spirit as
docgen_bankstatement_ocbc.py (the corporate OCBC template): it imports the shared
mastersheet loader, the seeded RNG helper, and the Block/Txn model from your
existing generator, registers a new bank profile ("OCBC Personal"), and adds its
own transaction engine + renderer, since a personal savings account's activity
(ATM withdrawals, card payments, payroll credit, interest) looks nothing like the
corporate SME activity docgen_bankstatement.py already generates.

    python docgen_bankstatement_ocbc_personal.py v9_document_mastersheet.xlsx --case C001 --outdir out --bank "OCBC Personal"

Page layout
-----------
Every page redraws: the bank logo/address, the customer block, the black
"STATEMENT OF ACCOUNT" banner with "Page X of Y", the account/period line, and
the table column headers — plus, in the corner, a tiny rotated "Co. Reg." line
that plays the same role as the rotated footer in your DBS renderer (drawn once
per page, every page). The one-time "printed on both sides" notice box appears
on page 1 only, and the bulkier closing content (advisory boxes, promo section,
deposit-insurance disclaimer) is appended only after the very last transaction
row — on the last page if it fits there, otherwise on a page of its own — the
same way your DBS template keeps that kind of boilerplate off every page and
only pays for it once.

Because the exact number of transaction pages plus whether the closing content
needs its own extra page both depend on how much activity got generated, this
runs a full two-pass layout before drawing anything: pass one paginates the
transaction rows, pass two checks whether the closing block fits on the last
row-page or needs one more page appended. Only after that is total_pages known,
so every page's "Page X of Y" is exact — never a placeholder.
"""

from __future__ import annotations

import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas as pdfcanvas

# Prefer the dotted package import so this shares the exact same BANK_PROFILES/
# RENDERERS dict objects as whatever else already imported docgen_bankstatement.py
# that way (e.g. scripts/generate_from_mastersheet.py, and
# corporate_bank_statement/docgen_bankstatement_ocbc.py) -- registering "OCBC
# Personal" into a second, separately-imported copy of those module-level dicts
# would leave it invisible to callers using the first copy (this used to be exactly
# that bug: BANK_PROFILES["OCBC Personal"] was landing in a disconnected copy of the
# dict, silently invisible to load_bank_case() as called from generate_from_mastersheet.py).
# Falls back to the bare import for standalone script use (`python
# docgen_bankstatement_ocbc_personal.py ...` from this directory).
try:
    from doc_gen_scripts.corporate_bank_statement.docgen_bankstatement import (
        PAGE_W, PAGE_H, FONT, FONT_B,
        BankProfile, BANK_PROFILES, RENDERERS,
        Block, Txn, seeded_rng, load_logo,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from corporate_bank_statement.docgen_bankstatement import (
        PAGE_W, PAGE_H, FONT, FONT_B,
        BankProfile, BANK_PROFILES, RENDERERS,
        Block, Txn, seeded_rng, load_logo,
    )

# Adobe's standard CJK font metrics ship with reportlab — no font file needed.
# Rendering the actual glyphs relies on the PDF viewer having a CJK font
# available, which is true of Acrobat, Chrome, Preview, etc. out of the box.
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
CJK_FONT = "STSong-Light"

# --------------------------------------------------------------------------
# Bank profile
# --------------------------------------------------------------------------
# Registered separately from "OCBC" (the corporate template) since the two
# documents share a bank but not a layout, product name, or transaction style.

BANK_PROFILES["OCBC Personal"] = BankProfile(
    name="OCBC",
    legal_name="Oversea-Chinese Banking Corporation Limited",
    address="65 Chulia Street, OCBC Centre, Singapore 049513",
    website="www.ocbc.com",
    footer_reg="Co. Reg. No. 193200032W",
    product="MONTHLY SAVINGS ACCOUNT",
    tagline=("", ""),
    # Reuses the real OCBC logo asset from the shared doc_gen_scripts/assets/images
    # folder (all doc-gen logos/stamps live there, not scattered per-module).
    logo_path=str(Path(__file__).resolve().parent.parent / "assets" / "images" / "ocbc-logo.png"),
    logo_style="plain",
    template="ocbc_personal",
    branch="Orchard Road Branch",
)

OCBC_RED = colors.HexColor("#E4002B")

# --------------------------------------------------------------------------
# Layout constants (points; 15mm top/bottom, 18mm left/right, per the reference HTML)
# --------------------------------------------------------------------------

P_M_L, P_M_R = 51.02, 51.02
P_M_TOP, P_M_BOTTOM = 42.52, 42.52
P_TOP = PAGE_H - P_M_TOP
P_BOTTOM = P_M_BOTTOM
TABLE_W = PAGE_W - P_M_L - P_M_R

P_HEADER_H = 58        # logo + bank address block
P_REFROW_H = 42         # ref number + "printed on both sides" notice — page 1 only
P_CUSTROW_H = 64        # customer block + STATEMENT OF ACCOUNT banner + meta
P_ACCTHDR_H = 28        # product/account line + period, with its bottom rule
P_TABLEHEAD_H = 30
P_FOOTER_RESERVE = 24   # room for the rotated Co. Reg. sidebar text
P_TOTALS_H = 62         # the three bold summary rows
P_CLOSING_H = 262       # advisory boxes + promo section + disclaimer

P_COL_TXNDATE_W = 58
P_COL_VALDATE_W = 50
P_COL_DESC_W = 125
P_COL_CHEQUE_W = 36
P_COL_WITHDRAW_W = 70
P_COL_DEPOSIT_W = 70
P_COL_BALANCE_W = TABLE_W - (P_COL_TXNDATE_W + P_COL_VALDATE_W + P_COL_DESC_W
                             + P_COL_CHEQUE_W + P_COL_WITHDRAW_W + P_COL_DEPOSIT_W)

X_TXNDATE = P_M_L
X_VALDATE = X_TXNDATE + P_COL_TXNDATE_W
X_DESC = X_VALDATE + P_COL_VALDATE_W
X_CHEQUE = X_DESC + P_COL_DESC_W
X_WITHDRAW = X_CHEQUE + P_COL_CHEQUE_W
R_WITHDRAW = X_WITHDRAW + P_COL_WITHDRAW_W
R_DEPOSIT = R_WITHDRAW + P_COL_DEPOSIT_W
R_BALANCE = R_DEPOSIT + P_COL_BALANCE_W

CARD_MERCHANT_DESCS = [
    "SUPERMARKET CARD PAYMENT", "RETAIL CARD PAYMENT", "RESTAURANT CARD PAYMENT",
    "PHARMACY CARD PAYMENT", "PETROL CARD PAYMENT",
]


# --------------------------------------------------------------------------
# Transaction engine (personal banking, not the corporate SME model)
# --------------------------------------------------------------------------

def personal_month_transactions(month: date, opening: float, rng: random.Random,
                                 known_salary: float | None = None) -> list[Txn]:
    last_day = (date(month.year + (month.month == 12), (month.month % 12) + 1, 1)
                - timedelta(days=1)).day

    events: list[tuple[int, str, dict[str, Any]]] = []
    for _ in range(rng.randint(1, 3)):
        events.append((rng.randint(1, last_day), "atm",
                       {"amount": round(rng.uniform(50, 300) / 10) * 10}))
    for _ in range(rng.randint(1, 3)):
        events.append((rng.randint(1, last_day), "card",
                       {"desc": rng.choice(CARD_MERCHANT_DESCS),
                        "amount": round(rng.uniform(15, 700), 2)}))
    events.append((rng.randint(5, 12), "utility", {"amount": round(rng.uniform(60, 300), 2)}))
    # Always draw the random fallback even when known_salary is given, so the rest of
    # this month's rng sequence doesn't shift depending on whether this individual
    # also happens to be this case's Payslip subject (see build_personal_blocks()).
    payroll_amount = round(rng.uniform(2500, 9000), 2)
    if known_salary is not None:
        payroll_amount = known_salary
    events.append((rng.randint(15, 25), "payroll", {"amount": payroll_amount}))
    if rng.random() < 0.7:
        events.append((rng.randint(1, last_day), "transfer",
                       {"amount": round(rng.uniform(50, 1000), 2), "out": rng.random() < 0.5}))
    events.append((last_day, "interest", {"amount": round(opening * rng.uniform(0.0008, 0.002), 2)}))
    events.sort(key=lambda e: e[0])

    balance = opening
    txns: list[Txn] = []

    def add(day: int, desc: str, withdrawal=None, deposit=None):
        nonlocal balance
        balance += (deposit or 0) - (withdrawal or 0)
        d = date(month.year, month.month, min(day, last_day))
        txns.append(Txn(d, d, [desc], withdrawal, deposit, round(balance, 2)))

    for day, kind, p in events:
        if kind == "atm":
            add(day, "ATM WITHDRAWAL", withdrawal=p["amount"])
        elif kind == "card":
            add(day, p["desc"], withdrawal=p["amount"])
        elif kind == "utility":
            add(day, "UTILITY BILL PAYMENT", withdrawal=p["amount"])
        elif kind == "payroll":
            add(day, "PAYROLL CREDIT", deposit=p["amount"])
        elif kind == "transfer":
            if p["out"]:
                add(day, "ONLINE TRANSFER", withdrawal=p["amount"])
            else:
                add(day, "ONLINE TRANSFER", deposit=p["amount"])
        elif kind == "interest":
            add(day, "INTEREST CREDIT", deposit=p["amount"])

    return txns


def _holder_seed_key(ctx: dict[str, Any]) -> str:
    """Per-account-holder seed base. case_id alone isn't enough once a case has more
    than one individual with their own "Bank Statement" Proof of Address row -- two
    different people's statements would otherwise come out byte-identical (same
    balances, same transaction days/amounts) apart from the name printed on them."""
    director = ctx.get("director") or {}
    holder_id = director.get("Individual ID") or ctx["case_id"]
    return f"{ctx['case_id']}|{holder_id}"


def build_personal_blocks(ctx: dict[str, Any], opening: float | None = None,
                           known_salary: float | None = None) -> list[Block]:
    """known_salary: this account holder's exact net pay, when they're also this
    case's Payslip subject -- so the PAYROLL CREDIT line agrees with what their
    payslip shows, instead of an independent random amount. See
    generate_from_mastersheet.py's Proof of Address / Bank Statement section,
    which looks this up and passes it in."""
    blocks: list[Block] = []
    seed_base = _holder_seed_key(ctx)
    rng_open = seeded_rng(seed_base + "|personal-opening")
    balance = opening if opening is not None else round(rng_open.uniform(1_500, 8_000), 2)

    for month in ctx["months"]:
        rng = seeded_rng(f"{seed_base}|personal|{month:%Y-%m}")
        txns = personal_month_transactions(month, balance, rng, known_salary=known_salary)
        blocks.append(Block(
            month=month, opening=balance, txns=txns,
            closing=txns[-1].balance if txns else balance,
            total_withdrawal=round(sum(t.withdrawal or 0 for t in txns), 2),
            total_deposit=round(sum(t.deposit or 0 for t in txns), 2),
        ))
        balance = blocks[-1].closing
    return blocks


def _average_daily_balance(blocks: list[Block]) -> float:
    """Day-weighted mean balance across every generated month."""
    total_bal_days, total_days = 0.0, 0
    for block in blocks:
        month = block.month
        last_day = (date(month.year + (month.month == 12), (month.month % 12) + 1, 1)
                    - timedelta(days=1)).day
        balance, day = block.opening, 1
        for t in block.txns:
            span = t.txn_date.day - day
            total_bal_days += balance * span
            total_days += span
            balance, day = t.balance, t.txn_date.day
        span = last_day - day + 1
        total_bal_days += balance * span
        total_days += span
    return round(total_bal_days / total_days, 2) if total_days else 0.0


# --------------------------------------------------------------------------
# Text wrapping helper for CJK (no spaces to break on, so simpleSplit can't wrap it)
# --------------------------------------------------------------------------

def _wrap_cjk(text: str, font: str, size: float, max_w: float) -> list[str]:
    lines, cur = [], ""
    for ch in text:
        trial = cur + ch
        if cur and pdfmetrics.stringWidth(trial, font, size) > max_w:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


# --------------------------------------------------------------------------
# Per-page drawing
# --------------------------------------------------------------------------

def personal_draw_letterhead(c: pdfcanvas.Canvas, ctx: dict[str, Any]) -> float:
    bank = ctx["bank"]
    logo_w, logo_h = 40, 40      # ~14mm
    y = P_TOP
    logo = load_logo(bank.logo_path) if bank.logo_path else None
    if logo is not None:
        iw, ih = logo.getSize()
        scale = min(logo_w / iw, logo_h / ih)
        dw, dh = iw * scale, ih * scale
        c.drawImage(logo, P_M_L + (logo_w - dw) / 2, y - logo_h + (logo_h - dh) / 2,
                    width=dw, height=dh, mask="auto")
    else:
        c.setFillColor(OCBC_RED)
        c.setFont(FONT_B, 15)
        c.drawString(P_M_L, y - 24, bank.name)

    addr_lines = [bank.legal_name]
    if ", Singapore " in bank.address:
        head, tail = bank.address.rsplit(", Singapore ", 1)
        addr_lines += [head, f"Singapore {tail}"]
    else:
        addr_lines.append(bank.address)

    c.setFillColor(OCBC_RED)
    yy = y - 8
    for i, line in enumerate(addr_lines):
        c.setFont(FONT_B if i == 0 else FONT, 7.5)
        c.drawRightString(P_M_L + TABLE_W, yy, line)
        yy -= 10

    return y - P_HEADER_H


def personal_draw_notice(c: pdfcanvas.Canvas, ctx: dict[str, Any], y: float) -> float:
    c.setFillColor(colors.black)
    c.setFont(FONT, 8.5)
    c.drawString(P_M_L, y - 12, ctx["ref_number"])

    box_w = 213  # ~75mm
    box_x = P_M_L + TABLE_W - box_w
    body = ("As part of our efforts to be a more environmentally friendly bank, all our "
            "account statements will now be printed on both sides of this page.")
    body_lines = simpleSplit(body, FONT, 6, box_w - 17)
    box_h = 10 + 8 + len(body_lines) * 8.5
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.75)
    c.rect(box_x, y - box_h, box_w, box_h, stroke=1, fill=0)
    c.setFont(FONT_B, 7)
    c.drawCentredString(box_x + box_w / 2, y - 12, "Information")
    c.line(box_x + box_w / 2 - 22, y - 14, box_x + box_w / 2 + 22, y - 14)
    c.setFont(FONT, 6)
    yy = y - 22
    for line in body_lines:
        c.drawCentredString(box_x + box_w / 2, yy, line)
        yy -= 8.5

    return y - P_REFROW_H


def personal_draw_customer_banner(c: pdfcanvas.Canvas, ctx: dict[str, Any],
                                   y: float, page_no: int, total: int) -> float:
    c.setFillColor(colors.black)
    c.setFont(FONT_B, 9.5)
    c.drawString(P_M_L, y - 11, ctx["account_holder"])
    c.setFont(FONT, 9)
    yy = y - 24
    for line in ctx["holder_address"]:
        c.drawString(P_M_L, yy, line)
        yy -= 13

    right = P_M_L + TABLE_W
    c.setFillColor(colors.black)
    c.rect(right - 158, y - 16, 158, 16, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont(FONT_B, 11)
    c.drawCentredString(right - 79, y - 12, "STATEMENT OF ACCOUNT")

    c.setFillColor(colors.black)
    c.setFont(FONT, 7.5)
    yy = y - 26
    for line in [f"Page {page_no} of {total}",
                 "For enquiries, please call",
                 "our Customer Service Officers at (1800) 363 3333"]:
        c.drawRightString(right, yy, line)
        yy -= 9.5
    c.setFont(FONT_B, 7.5)
    c.drawRightString(right, yy, ctx["bank"].branch)

    return y - P_CUSTROW_H


def personal_draw_account_header(c: pdfcanvas.Canvas, ctx: dict[str, Any], y: float) -> float:
    c.setFillColor(colors.black)
    c.setFont(FONT_B, 9)
    c.drawString(P_M_L, y - 10, ctx["bank"].product)
    c.setFont(FONT, 9)
    c.drawString(P_M_L, y - 22, f"Account No. {ctx['ref_number']}")
    first, last = ctx["period"]
    c.setFont(FONT, 9)
    c.drawRightString(P_M_L + TABLE_W, y - 10, f"{first:%d %b %Y} TO {last:%d %b %Y}".upper())

    c.setStrokeColor(colors.black)
    c.setLineWidth(1)
    c.line(P_M_L, y - P_ACCTHDR_H, P_M_L + TABLE_W, y - P_ACCTHDR_H)
    return y - P_ACCTHDR_H


def personal_draw_table_head(c: pdfcanvas.Canvas, y: float) -> float:
    c.setFillColor(colors.black)

    def head(x: float, en: str, zh: str, align: str = "left") -> None:
        c.setFont(FONT_B, 7)
        if align == "left":
            c.drawString(x, y - 8, en)
        elif align == "right":
            c.drawRightString(x, y - 8, en)
        else:
            c.drawCentredString(x, y - 8, en)
        c.setFont(CJK_FONT, 6)
        if align == "left":
            c.drawString(x, y - 16, zh)
        elif align == "right":
            c.drawRightString(x, y - 16, zh)
        else:
            c.drawCentredString(x, y - 16, zh)

    head(X_TXNDATE, "Transaction Date", "交易日")
    head(X_VALDATE, "Value Date", "过账日")
    head(X_DESC, "Description", "说明")
    head(X_CHEQUE + P_COL_CHEQUE_W / 2, "Cheque", "支票", "center")
    head(R_WITHDRAW, "Withdrawal", "支出", "right")
    head(R_DEPOSIT, "Deposit", "存入", "right")
    head(R_BALANCE, "Balance", "结存/次", "right")

    bottom = y - P_TABLEHEAD_H
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.75)
    c.line(P_M_L, bottom, P_M_L + TABLE_W, bottom)
    return bottom


def _personal_row_lines(desc: str) -> list[str]:
    return simpleSplit(desc, FONT, 8, P_COL_DESC_W - 4) or [""]


def _personal_row_height(txn: Txn) -> float:
    return max(1, len(_personal_row_lines(txn.lines[0]))) * 11 + 4


def personal_draw_bf_row(c: pdfcanvas.Canvas, opening: float, y: float) -> float:
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(X_DESC, y - 10, "BALANCE B/F")
    c.drawRightString(R_BALANCE, y - 10, f"{opening:,.2f}")
    return y - 15


def personal_draw_row(c: pdfcanvas.Canvas, txn: Txn, y: float) -> float:
    lines = _personal_row_lines(txn.lines[0])
    h = _personal_row_height(txn)
    c.setFillColor(colors.black)
    c.setFont(FONT, 8)
    c.drawString(X_TXNDATE, y - 10, txn.txn_date.strftime("%d %b").upper())
    c.drawString(X_VALDATE, y - 10, txn.value_date.strftime("%d %b").upper())
    yy = y - 10
    for line in lines:
        c.drawString(X_DESC, yy, line)
        yy -= 11
    if txn.withdrawal is not None:
        c.drawRightString(R_WITHDRAW, y - 10, f"{txn.withdrawal:,.2f}")
    if txn.deposit is not None:
        c.drawRightString(R_DEPOSIT, y - 10, f"{txn.deposit:,.2f}")
    c.drawRightString(R_BALANCE, y - 10, f"{txn.balance:,.2f}")
    return y - h


def personal_draw_totals(c: pdfcanvas.Canvas, ctx: dict[str, Any], y: float) -> float:
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.75)
    c.line(P_M_L, y, P_M_L + TABLE_W, y)
    y -= 12

    c.setFillColor(colors.black)
    c.setFont(FONT_B, 8)
    c.drawString(X_DESC, y, "Total Withdrawals / Deposits")
    c.drawRightString(R_WITHDRAW, y, f"{ctx['total_withdrawal']:,.2f}")
    c.drawRightString(R_DEPOSIT, y, f"{ctx['total_deposit']:,.2f}")
    y -= 16

    c.drawString(X_DESC, y, "Total Interest Paid This year")
    c.drawRightString(R_DEPOSIT, y, f"{ctx['interest_ytd']:,.2f}")
    y -= 16

    c.drawString(X_DESC, y, "Average Balance")
    c.drawRightString(R_DEPOSIT, y, f"{ctx['average_balance']:,.2f}")
    y -= 16
    return y


def personal_draw_closing(c: pdfcanvas.Canvas, y: float) -> None:
    def advisory(y: float, title: str, en: str, zh: str | None = None) -> float:
        lines = simpleSplit(en, FONT, 7.5, TABLE_W - 8)
        zh_lines = _wrap_cjk(zh, CJK_FONT, 7.5, TABLE_W - 8) if zh else []
        h = 10 + 12 + len(lines) * 10.5 + len(zh_lines) * 10.5 + 6
        c.setStrokeColor(colors.black)
        c.setLineWidth(0.75)
        c.rect(P_M_L, y - h, TABLE_W, h, stroke=1, fill=0)
        c.setFillColor(colors.black)
        c.setFont(FONT_B, 8.5)
        c.drawString(P_M_L + 6, y - 12, title)
        c.setFont(FONT, 7.5)
        yy = y - 24
        for line in lines:
            c.drawString(P_M_L + 6, yy, line)
            yy -= 10.5
        c.setFillColor(colors.HexColor("#333333"))
        c.setFont(CJK_FONT, 7.5)
        for line in zh_lines:
            c.drawString(P_M_L + 6, yy, line)
            yy -= 10.5
        return y - h - 6

    y = advisory(
        y, "CHECK YOUR STATEMENT",
        "Please check this statement & advise us of any discrepancies within 14 days of "
        "receipt. If we do not hear from you, we will take this statement as correct and "
        "binding. For enquiries, please call our Customer Service Officers at (800) 555-0100.",
        "请查核这结单，若有不符之处，请在收到之后十四天内通知我们。如果您没有通知我们，"
        "则我们将视此结单为正确且具有约束力。",
    )
    y = advisory(
        y, "UPDATING YOUR PERSONAL PARTICULARS",
        "You may update your address and contact numbers via Online Banking or our ATMs. "
        "Alternatively, complete and mail us the Change of Address / Contact Details form: "
        "Bank website > Personal Banking > Help and Support > Banking Forms.",
    )

    c.setStrokeColor(colors.black)
    c.setLineWidth(0.75)
    c.line(P_M_L, y, P_M_L + TABLE_W, y)
    y -= 14
    c.setFillColor(colors.black)
    c.setFont(FONT_B, 8.5)
    c.drawString(P_M_L, y, "BANK PROMOTION & INFORMATION")
    y -= 14
    c.setFont(FONT_B, 7.5)
    c.drawString(P_M_L, y, "REVISION OF INTEREST RATES")
    c.setFont(FONT, 7.5)
    for line in simpleSplit(
        "From 1 November 2025, interest rates for Bonus+ Savings Account will be revised. "
        "To view the new rates and details, visit Bank website > Notices.",
        FONT, 7.5, TABLE_W - 160,
    ):
        c.drawString(P_M_L + 160, y, line)
        y -= 10.5
    y -= 12

    c.setFillColor(colors.black)
    c.setFont(FONT_B, 6)
    c.drawString(P_M_L, y, "Deposit Insurance Scheme")
    y -= 9
    c.setFillColor(colors.HexColor("#333333"))
    c.setFont(FONT, 6)
    for line in simpleSplit(
        "Singapore dollar deposits of non-bank depositors and monies and deposits denominated "
        "in Singapore dollars under the Supplementary Retirement Scheme are insured by the "
        "Singapore Deposit Insurance Corporation, for up to S$100,000 in aggregate per "
        "depositor per Scheme member by law. Monies and deposits in Supplementary Retirement "
        "Scheme accounts and CPF Investment Scheme accounts are excluded from deposit "
        "insurance coverage.",
        FONT, 6, TABLE_W,
    ):
        c.drawString(P_M_L, y, line)
        y -= 9


def personal_draw_footer(c: pdfcanvas.Canvas, ctx: dict[str, Any]) -> None:
    """The rotated Co. Reg. sidebar — drawn once per page, every page."""
    c.saveState()
    c.setFont(FONT, 5.5)
    c.setFillColor(colors.HexColor("#555555"))
    c.translate(PAGE_W - 12, 42)
    c.rotate(90)
    c.drawString(0, 0, f"{ctx['bank'].legal_name}  {ctx['bank'].footer_reg}")
    c.restoreState()


# --------------------------------------------------------------------------
# Renderer entry point
# --------------------------------------------------------------------------

def _generate_personal_account_no(seed: str) -> str:
    """A personal savings account number is its own identity, not the company's
    account from the Bank Statements tab -- generate one in the bank's XXX-XXXXXX-XXX
    format, seeded on the account holder (case + individual) so it's stable across
    re-runs but distinct between two individuals in the same case."""
    rng = seeded_rng(f"{seed}|personal-account-no")
    return f"{rng.randint(0, 999):03d}-{rng.randint(0, 999999):06d}-{rng.randint(0, 999):03d}"


def render_ocbc_personal(ctx: dict[str, Any], blocks: list[Block], out_pdf: Path) -> Path:
    all_txns = [t for b in blocks for t in b.txns]
    seed_base = _holder_seed_key(ctx)
    ctx["account_no"] = _generate_personal_account_no(seed_base)
    ctx["ref_number"] = ctx["account_no"]

    director = ctx.get("director")
    ctx["account_holder"] = director["Name (auto/fill)"].upper() if director else "ACCOUNT HOLDER"
    holder_address = (director or {}).get("Residential Address") if director else None
    ctx["holder_address"] = (
        [p.strip().upper() for p in str(holder_address).split(",")] if holder_address
        else [p.strip() for p in ctx["address"].split(",")]
    )

    first_month = ctx["months"][0]
    last_month = ctx["months"][-1]
    period_end = (date(last_month.year + (last_month.month == 12),
                       (last_month.month % 12) + 1, 1) - timedelta(days=1))
    ctx["period"] = (first_month, period_end)

    ctx["total_withdrawal"] = round(sum(b.total_withdrawal for b in blocks), 2)
    ctx["total_deposit"] = round(sum(b.total_deposit for b in blocks), 2)
    ctx["average_balance"] = _average_daily_balance(blocks)

    generated_interest = sum(
        t.deposit or 0 for b in blocks if b.month.year == last_month.year
        for t in b.txns if t.lines[0] == "INTEREST CREDIT"
    )
    rng_ytd = seeded_rng(seed_base + "|personal-interest-ytd")
    prior_months = max(0, first_month.month - 1) if first_month.year == last_month.year else 0
    prior_interest = round(rng_ytd.uniform(5, 15) * prior_months, 2)
    ctx["interest_ytd"] = round(prior_interest + generated_interest, 2)

    # Pass 1: paginate transaction rows only.
    first_top = P_TOP - P_HEADER_H - P_REFROW_H - P_CUSTROW_H - P_ACCTHDR_H - P_TABLEHEAD_H
    cont_top = P_TOP - P_HEADER_H - P_CUSTROW_H - P_ACCTHDR_H - P_TABLEHEAD_H
    content_bottom = P_BOTTOM + P_FOOTER_RESERVE

    pages: list[list[Txn]] = []
    current: list[Txn] = []
    budget = first_top - content_bottom - 15  # BALANCE B/F row on page 1
    for txn in all_txns:
        h = _personal_row_height(txn)
        if current and budget - h < 0:
            pages.append(current)
            current, budget = [], cont_top - content_bottom
        budget -= h
        current.append(txn)
    pages.append(current)

    # Pass 2: does the totals + closing block fit after the last page's rows?
    last_page_top = cont_top if len(pages) > 1 else first_top
    consumed = sum(_personal_row_height(t) for t in pages[-1])
    if len(pages) == 1:
        consumed += 15  # BALANCE B/F
    leftover = (last_page_top - content_bottom) - consumed
    closing_on_own_page = leftover < (P_TOTALS_H + P_CLOSING_H)
    if closing_on_own_page:
        pages.append([])  # a page with no transaction rows, just totals + closing

    total_pages = len(pages)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = pdfcanvas.Canvas(str(out_pdf), pagesize=A4)
    c.setTitle(f"Statement of Account — {ctx['account_holder']}")
    c.setAuthor(ctx["bank"].legal_name)

    for i, page_rows in enumerate(pages, start=1):
        first = (i == 1)
        y = personal_draw_letterhead(c, ctx)
        if first:
            y = personal_draw_notice(c, ctx, y)
        y = personal_draw_customer_banner(c, ctx, y, i, total_pages)
        y = personal_draw_account_header(c, ctx, y)
        y = personal_draw_table_head(c, y)
        if first:
            y = personal_draw_bf_row(c, blocks[0].opening, y)
        for txn in page_rows:
            y = personal_draw_row(c, txn, y)
        if i == total_pages:
            y -= 6
            y = personal_draw_totals(c, ctx, y)
            personal_draw_closing(c, y)
        personal_draw_footer(c, ctx)
        c.showPage()

    c.save()
    return out_pdf


RENDERERS["ocbc_personal"] = render_ocbc_personal


if __name__ == "__main__":
    from corporate_bank_statement.docgen_bankstatement import main
    main()
