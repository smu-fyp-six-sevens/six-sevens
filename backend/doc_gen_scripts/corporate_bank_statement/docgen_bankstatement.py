"""
docgen_bankstatement.py — generate DBS-style + UOB-style corporate account statements.

Structure of this module
------------------------
  1. Layout constants          page geometry, column x-positions, brand colours (DBS)
  2. BANK_PROFILES             per-bank letterhead details (DBS, UOB)
  3. Mastersheet loading       load_case() reads Case Registry + Bank Statements tabs
  4. Transaction engine        month_transactions() invents a month of activity, shared
                                by both templates
  5. Pagination                paginate() splits rows into pages before drawing (DBS)
  6. Rendering                 render() paints the DBS printed-statement pages
  7. CLI                       build_blocks() / generate() / main()
  8. UOB template              render_uob() — a second, self-contained renderer for the
                                UOB iBanking "Account Activities" export, with its own
                                layout constants, pagination and drawing helpers

generate() picks a renderer from RENDERERS by the bank profile's `template` field, so
adding another bank means adding a BankProfile plus a matching RENDERERS entry — either
reusing an existing renderer or writing a new section like 8.

To generate (a specific case):

cd backend/doc_gen_scripts/corporate_bank_statement
python docgen_bankstatement.py ../v9_document_mastersheet.xlsx --case C001 --outdir out/


Everything is seeded from SHA-256(case_id + month) so reruns are byte-comparable.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, TypedDict

import openpyxl
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas as pdfcanvas

# --------------------------------------------------------------------------
# 1. Layout constants
# --------------------------------------------------------------------------

PAGE_W, PAGE_H = A4
M_L, M_R = 40, 40
TOP = PAGE_H - 40
BOTTOM = 60

COL_TXN_DATE = M_L
COL_VAL_DATE = M_L + 58
COL_DETAILS = M_L + 118
DETAILS_W = 210
COL_WITHDRAW_R = PAGE_W - M_R - 175
COL_DEPOSIT_R = PAGE_W - M_R - 90
COL_BALANCE_R = PAGE_W - M_R

FONT = "Helvetica"
FONT_B = "Helvetica-Bold"
SIZE_ROW = 7.2
LEAD_ROW = 8.6
BAND_GREY = colors.HexColor("#D4D4D4")
DBS_RED = colors.HexColor("#E4002B")
UOB_NAVY = colors.HexColor("#12275C")
UOB_TABLE_HEAD = colors.HexColor("#DDE3F3")
UOB_RULE = colors.HexColor("#E2E2E2")
UOB_LABEL = colors.HexColor("#6B6B6B")

SYNTHETIC_TAG = "SYNTHETIC-TEST-DATA"


# --------------------------------------------------------------------------
# 2. Bank profiles
# --------------------------------------------------------------------------

@dataclass
class BankProfile:
    name: str
    legal_name: str
    address: str
    website: str
    footer_reg: str
    product: str
    tagline: tuple[str, str]
    # Path to a transparent-background PNG of the bank wordmark. None falls back to
    # the vector approximation drawn by _draw_logo_fallback().
    logo_path: str | None = None
    # "panel": white wordmark reversed out of the black brand panel, as on the real
    # statement. "plain": logo printed in its own colours on the white page.
    logo_style: str = "panel"
    # Which page renderer to use: "dbs" (printed statement) or "uob" (iBanking export).
    template: str = "dbs"
    branch: str = ""
    # Company chop printed in the footer of the UOB export. JPEGs have no alpha, so
    # load_stamp() keys out the white background before drawing.
    stamp_path: str | None = None
    bic_code: str = ""
    bic_name: str = ""


BANK_PROFILES: dict[str, BankProfile] = {
    "DBS Bank": BankProfile(
        name="DBS",
        legal_name="DBS Bank Ltd",
        address="12 Marina Boulevard, Marina Bay Financial Centre Tower 3, Singapore 018982",
        website="www.dbs.com",
        footer_reg="Co. Reg. No. 196800306E   GST Reg No: MR-8500180-3",
        product="Details of your DBS Corporate Multi-Currency Account",
        tagline=("Live more,", "Bank less"),
        logo_path=str(Path(__file__).with_name("assets") / "dbs_logo.png"),
        logo_style="panel",
    ),
    "UOB": BankProfile(
        name="UOB",
        legal_name="United Overseas Bank Limited",
        address="80 Raffles Place, UOB Plaza, Singapore 048624",
        website="www.uob.com.sg",
        footer_reg="Co. Reg. No. 193500026Z",
        product="Account Activities",
        tagline=("", ""),
        logo_path=str(Path(__file__).with_name("assets") / "uob_logo.png"),
        logo_style="plain",
        template="uob",
        branch="UOB Orchard",
        stamp_path=str(Path(__file__).with_name("assets") / "uob_stamp.jpg"),
    ),
    # OCBC also appears on the Bank Statements tab. Add a profile and a matching
    # renderer entry in RENDERERS before generating those cases.
}


# Logo images are recoloured once per run and cached, since drawImage is called on
# every page.
_LOGO_CACHE: dict[tuple[str, bool], ImageReader] = {}


def load_logo(path: str, reverse: bool = False) -> ImageReader | None:
    """Return the logo as an ImageReader, or None if the file is missing.

    reverse=True repaints opaque dark pixels white so the wordmark reads against the
    black brand panel, leaving the coloured mark untouched.
    """
    key = (path, reverse)
    if key in _LOGO_CACHE:
        return _LOGO_CACHE[key]
    if not Path(path).exists():
        return None

    from PIL import Image

    img = Image.open(path).convert("RGBA")
    if reverse:
        pixels = img.load()
        if pixels: 
            w, h = img.size
            for y in range(h):
                for x in range(w):
                    r, g, b, a = pixels[x, y]
                    if a > 0 and max(r, g, b) < 110:      # near-black wordmark only
                        pixels[x, y] = (255, 255, 255, a)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    reader = ImageReader(buf)
    _LOGO_CACHE[key] = reader
    return reader


_STAMP_CACHE: dict[str, ImageReader] = {}


def load_stamp(path: str, white_cutoff: int = 232) -> ImageReader | None:
    """Load a stamp image, making its white background transparent.

    Scanned chops arrive as JPEGs with a solid white field. Drawn as-is the white
    would sit as an opaque square over the page, so anything brighter than
    white_cutoff on all three channels is made fully transparent.
    """
    if path in _STAMP_CACHE:
        return _STAMP_CACHE[path]
    if not Path(path).exists():
        return None

    from PIL import Image

    img = Image.open(path).convert("RGBA")
    pixels = img.load()
    w, h = img.size
    if pixels:
        for y in range(h):
            for x in range(w):
                r, g, b, _ = pixels[x, y]
                if r >= white_cutoff and g >= white_cutoff and b >= white_cutoff:
                    pixels[x, y] = (r, g, b, 0)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    reader = ImageReader(buf)
    _STAMP_CACHE[path] = reader
    return reader


# --------------------------------------------------------------------------
# 3. Mastersheet loading
# --------------------------------------------------------------------------

def seeded_rng(key: str) -> random.Random:
    return random.Random(int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16))


def _rows(ws) -> list[dict[str, Any]]:
    header = [c.value for c in ws[1]]
    return [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]


def parse_period(text: str) -> list[date]:
    """'2026-06 to 2026-08' -> [2026-06-01, 2026-07-01, 2026-08-01]"""
    found = re.findall(r"(\d{4})-(\d{2})", str(text or ""))
    if not found:
        raise ValueError(f"cannot parse statement period: {text!r}")
    start = date(int(found[0][0]), int(found[0][1]), 1)
    end = date(int(found[-1][0]), int(found[-1][1]), 1)
    months, cur = [], start
    while cur <= end:
        months.append(cur)
        cur = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
    return months


def load_case(xlsx: str | Path, case_id: str, bank_override: str | None = None) -> dict[str, Any]:
    wb = openpyxl.load_workbook(str(xlsx), data_only=True)
    case = next(r for r in _rows(wb["Case Registry"]) if str(r["Case ID"]) == case_id)
    stmt = next(r for r in _rows(wb["Bank Statements"]) if str(r["Case ID"]) == case_id)
    people = [r for r in _rows(wb["Individuals"]) if str(r["Case ID"]) == case_id]
    people.sort(key=lambda r: r.get("Person Seq") or 0)

    bank_name = bank_override or str(stmt.get("Bank Name (auto)") or case.get("Bank Name"))
    if bank_name not in BANK_PROFILES:
        raise NotImplementedError(
            f"No letterhead profile for {bank_name!r}. Add one to BANK_PROFILES."
        )

    return {
        "case_id": case_id,
        "filename": stmt.get("Filename") or f"{case_id}_BankStatement.pdf",
        "company_name": str(case["Company Name"]).upper(),
        "address": str(case["Registered Address"]).upper(),
        "account_no": str(stmt.get("Account No. (auto)") or case.get("Bank Account No.")),
        "bank": BANK_PROFILES[bank_name],
        "months": parse_period(stmt.get("Statement Month", "")),
        "director": people[0] if people else None,
        "fraud_flag": str(stmt.get("Fraud Injected? (Y/N)") or "N").strip().upper() == "Y",
        "dimension": stmt.get("Detection Dimension"),
    }


def format_account_no(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    return f"{digits[:3]}-{digits[3:9]}-{digits[9:]}" if len(digits) >= 10 else raw


# --------------------------------------------------------------------------
# 4. Transaction engine
# --------------------------------------------------------------------------

@dataclass
class Txn:
    txn_date: date
    value_date: date
    lines: list[str]
    withdrawal: float | None = None
    deposit: float | None = None
    balance: float = 0.0


CUSTOMERS = [
    "ORIENT PACIFIC SUPPLIES PTE LTD", "KIM SENG TRADING PTE. LTD.",
    "HARBOURFRONT DISTRIBUTORS PTE LTD", "GREENFIELD RETAIL GROUP PTE. LTD.",
    "SUNRISE PROVISIONS PTE LTD", "ASIA LINK COMMODITIES PTE. LTD.",
]
SUPPLIERS = [
    "TAN HENG PACKAGING PTE LTD", "MERIDIAN LOGISTICS SERVICES",
    "SEAWAY FREIGHT (S) PTE LTD", "UNITED PAPER SUPPLY PTE. LTD.",
]
CARD_MERCHANTS = [
    "SHELL SERVICE STN SI NG", "FAIRPRICE FINEST SI NG", "GRAB* TRIP SI NG",
    "COURTS MEGASTORE SI NG", "SP SERVICES LTD SI NG",
]


def _card_ref(rng: random.Random) -> str:
    return f"{rng.randint(4000,4999)}-{rng.randint(1000,9999)}-{rng.randint(1000,9999)}-{rng.randint(1000,9999)}"


def month_transactions(
    ctx: dict[str, Any], month: date, opening: float, rng: random.Random
) -> list[Txn]:
    """Invent one month of plausible SME account activity."""
    last_day = (date(month.year + (month.month == 12), (month.month % 12) + 1, 1)
                - timedelta(days=1)).day
    salary_names = [ctx["director"]["Name (auto/fill)"].upper()] if ctx["director"] else ["STAFF"]

    events: list[tuple[int, str, dict[str, Any]]] = []

    # Customer receipts — the bulk of the credits.
    for _ in range(rng.randint(2, 3)):
        events.append((rng.randint(2, last_day - 1), "receipt", {
            "payer": rng.choice(CUSTOMERS),
            "amount": round(rng.uniform(4_000, 45_000), 2),
        }))

    # Supplier payments.
    for _ in range(rng.randint(2, 3)):
        events.append((rng.randint(3, last_day - 1), "supplier", {
            "payee": rng.choice(SUPPLIERS),
            "amount": round(rng.uniform(2_000, 28_000), 2),
        }))

    # Payroll on the 25th, rent on the 1st, utilities mid-month.
    events.append((25, "payroll", {"name": rng.choice(salary_names),
                                   "amount": round(rng.uniform(3_500, 9_000), 2)}))
    events.append((1, "rent", {"amount": round(rng.uniform(3_000, 7_500), 2)}))
    events.append((rng.randint(10, 18), "utilities", {"amount": round(rng.uniform(280, 1_400), 2)}))

    # Card spend.
    for _ in range(rng.randint(1, 2)):
        events.append((rng.randint(1, last_day), "card", {
            "merchant": rng.choice(CARD_MERCHANTS),
            "amount": round(rng.uniform(18, 850), 2),
        }))

    # Monthly account fee on the last day.
    events.append((last_day, "fee", {"amount": 35.00}))

    events.sort(key=lambda e: e[0])

    balance = opening
    txns: list[Txn] = []

    def add(day: int, lines: list[str], withdrawal=None, deposit=None):
        nonlocal balance
        balance += (deposit or 0) - (withdrawal or 0)
        d = date(month.year, month.month, min(day, last_day))
        txns.append(Txn(d, d, lines, withdrawal, deposit, round(balance, 2)))

    for day, kind, p in events:
        if kind == "receipt":
            add(day, [
                "Inward PayNow PAYMENT",
                f"SG3P{rng.randint(10**11, 10**12-1)} C{rng.randint(10**11, 10**12-1)}",
                f"OTHER {p['payer']} SGD {p['amount']:,.0f}",
            ], deposit=p["amount"])
        elif kind == "supplier":
            add(day, [
                "REMITTANCE TRANSFER OF FUNDS RTF",
                f"EBACT{rng.randint(10**10, 10**11-1)} 0016RF{rng.randint(1000000,9999999)}",
                f"{p['payee']} SGD {p['amount']:,.0f}",
            ], withdrawal=p["amount"])
        elif kind == "payroll":
            add(day, [
                "FAST PAYMENT SALARY",
                f"EBGPP{rng.randint(10**13, 10**14-1)}C100000000000",
                f"M:+65{rng.randint(80000000,99999999)} SALARY PAYMENT SGD {p['amount']:,.0f}",
            ], withdrawal=p["amount"])
            add(day, [
                "SERVICE CHARGE FOR FAST PAYMENT SALARY",
                f"EBGPP{rng.randint(10**13, 10**14-1)}C100000000000",
                "M:IDEAL SALARY PAYMENT",
            ], withdrawal=0.50)
        elif kind == "rent":
            add(day, [
                "GIRO PAYMENT",
                f"PREMISES RENTAL {month.strftime('%b%y').upper()}",
            ], withdrawal=p["amount"])
        elif kind == "utilities":
            add(day, [
                "GIRO PAYMENT",
                "SP SERVICES LTD UTILITIES",
            ], withdrawal=p["amount"])
        elif kind == "card":
            add(day, [
                "BUSINESS ADVANCE CARD TRANSACTION BAT",
                f"{p['merchant']} {day:02d}{month.strftime('%b').upper()} {_card_ref(rng)}",
            ], withdrawal=p["amount"])
        elif kind == "fee":
            add(day, ["SERVICE CHARGE"], withdrawal=p["amount"])

    return txns


# --------------------------------------------------------------------------
# 5. Pagination
# --------------------------------------------------------------------------

@dataclass
class Block:
    """One month's statement section."""
    month: date
    opening: float
    txns: list[Txn]
    closing: float
    total_withdrawal: float
    total_deposit: float


@dataclass
class PageSlice:
    block: Block
    rows: list[Txn]
    carried_in: float
    is_last_slice: bool
    first_page: bool = False


def _row_height(txn: Txn) -> float:
    lines = 0
    for ln in txn.lines:
        lines += len(simpleSplit(ln, FONT, SIZE_ROW, DETAILS_W)) or 1
    return lines * LEAD_ROW + 3


def paginate(blocks: list[Block]) -> list[PageSlice]:
    """Split blocks into page-sized slices before any drawing happens."""
    pages: list[PageSlice] = []
    first_page = True
    for block in blocks:
        # Space consumed by furniture before the first data row on a page.
        avail_first = TOP - 210 - BOTTOM if first_page else TOP - 150 - BOTTOM
        remaining = list(block.txns)
        carried = block.opening
        while remaining:
            budget = avail_first
            take: list[Txn] = []
            for txn in remaining:
                h = _row_height(txn)
                if budget - h < 0:
                    break
                budget -= h
                take.append(txn)
            rest = remaining[len(take):]
            # Reserve room for the Total / Balance Carried Forward rows.
            if not rest and budget < 40:
                rest = [take.pop()]
            pages.append(
                PageSlice(block, take, carried, is_last_slice=not rest, first_page=first_page)
            )
            carried = take[-1].balance if take else carried
            remaining = rest
            first_page = False
            avail_first = TOP - 150 - BOTTOM
    return pages


# --------------------------------------------------------------------------
# 6. Rendering
# --------------------------------------------------------------------------

def draw_logo(c: pdfcanvas.Canvas, bank: BankProfile, x: float, y: float,
              w: float, h: float) -> None:
    """Paint the bank brand block. Uses bank.logo_path when present."""
    tag_w = 60
    logo = load_logo(bank.logo_path, reverse=(bank.logo_style == "panel")) if bank.logo_path else None

    if bank.logo_style == "panel":
        c.setFillColor(colors.black)
        c.rect(x, y, w, h, stroke=0, fill=1)
        c.setFillColor(DBS_RED)
        c.rect(x + w, y, tag_w, h, stroke=0, fill=1)
        c.setFillColor(colors.white)
        c.setFont(FONT, 7.5)
        c.drawString(x + w + 6, y + 26, bank.tagline[0])
        c.drawString(x + w + 6, y + 16, bank.tagline[1])

    if logo is None:
        _draw_logo_fallback(c, bank, x, y, w, h)
        return

    # Fit the image inside the panel, preserving aspect ratio.
    iw, ih = logo.getSize()
    pad = 8 if bank.logo_style == "panel" else 0
    box_w, box_h = w - 2 * pad, h - 2 * pad
    scale = min(box_w / iw, box_h / ih)
    dw, dh = iw * scale, ih * scale
    c.drawImage(
        logo,
        x + pad + (box_w - dw) / 2,
        y + pad + (box_h - dh) / 2,
        width=dw,
        height=dh,
        mask="auto",
    )


def _draw_logo_fallback(c: pdfcanvas.Canvas, bank: BankProfile, x: float, y: float,
                        w: float, h: float) -> None:
    """Vector approximation used when no logo file is available."""
    c.setFillColor(colors.white)
    c.setFont(FONT_B, 20)
    c.drawCentredString(x + w / 2 + 5, y + 16, bank.name)
    c.setFillColor(DBS_RED)
    c.rect(x + 10, y + 18, 12, 12, stroke=0, fill=1)


def draw_letterhead(c: pdfcanvas.Canvas, ctx: dict[str, Any], page_no: int, total: int) -> float:
    bank = ctx["bank"]
    y = TOP

    c.setFont(FONT, 6.5)
    c.setFillColor(colors.black)
    c.drawString(M_L, y, bank.legal_name)
    c.drawString(M_L, y - 8, bank.address)
    c.drawString(M_L, y - 16, bank.website)

    draw_logo(c, bank, x=PAGE_W - M_R - 155, y=y - 52, w=105, h=46)

    c.setFillColor(colors.black)
    if page_no == 1:
        c.setFont("Helvetica-BoldOblique", 15)
        c.drawString(M_L, y - 48, "Account Statement")

    c.setFont(FONT, 8)
    c.drawString(PAGE_W - M_R - 130, y - 88, "Page")
    c.drawString(PAGE_W - M_R - 95, y - 88, str(page_no))
    c.drawString(PAGE_W - M_R - 72, y - 88, "of")
    c.drawString(PAGE_W - M_R - 48, y - 88, str(total))

    cursor = y - 105
    if page_no == 1:
        c.setFont(FONT, 8.5)
        for line in [ctx["company_name"]] + [p.strip() for p in ctx["address"].split(",")]:
            c.drawString(M_L, cursor, line)
            cursor -= 12
        cursor -= 18
    return cursor


def draw_account_band(c: pdfcanvas.Canvas, ctx: dict[str, Any], block: Block, y: float) -> float:
    month = block.month
    last_day = (date(month.year + (month.month == 12), (month.month % 12) + 1, 1)
                - timedelta(days=1))
    c.setFillColor(BAND_GREY)
    c.rect(M_L, y - 30, PAGE_W - M_L - M_R, 30, stroke=0, fill=1)
    c.setFillColor(colors.black)
    c.setFont(FONT_B, 9.5)
    c.drawString(M_L + 6, y - 12, ctx["bank"].product)
    c.setFont(FONT, 8)
    c.drawString(M_L + 6, y - 25,
                 f"{month.strftime('%d-%b-%Y')}  to  {last_day.strftime('%d-%b-%Y')}")
    c.drawRightString(PAGE_W - M_R - 6, y - 25,
                      f"Account No: {format_account_no(ctx['account_no'])}")
    return y - 44


def draw_column_headers(c: pdfcanvas.Canvas, y: float) -> float:
    c.setFont(FONT_B, 8.5)
    c.setFillColor(colors.black)
    c.drawString(COL_TXN_DATE, y, "Transaction")
    c.drawString(COL_TXN_DATE, y - 10, "Date")
    c.drawString(COL_VAL_DATE, y, "Value")
    c.drawString(COL_VAL_DATE, y - 10, "Date")
    c.drawString(COL_DETAILS, y, "Transaction")
    c.drawString(COL_DETAILS, y - 10, "Details")
    c.drawRightString(COL_WITHDRAW_R, y, "Withdrawal")
    c.drawRightString(COL_DEPOSIT_R, y, "Deposit")
    c.drawRightString(COL_BALANCE_R, y, "Balance")
    c.setLineWidth(0.8)
    c.line(M_L, y - 16, PAGE_W - M_R, y - 16)
    return y - 28


def draw_row(c: pdfcanvas.Canvas, txn: Txn, y: float) -> float:
    c.setFont(FONT, SIZE_ROW)
    c.drawString(COL_TXN_DATE, y, txn.txn_date.strftime("%d-%b-%y"))
    c.drawString(COL_VAL_DATE, y, txn.value_date.strftime("%d-%b-%y"))
    if txn.withdrawal is not None:
        c.drawRightString(COL_WITHDRAW_R, y, f"{txn.withdrawal:,.2f}")
    if txn.deposit is not None:
        c.drawRightString(COL_DEPOSIT_R, y, f"{txn.deposit:,.2f}")
    c.drawRightString(COL_BALANCE_R, y, f"{txn.balance:,.2f}")

    cursor = y
    for line in txn.lines:
        for wrapped in simpleSplit(line, FONT, SIZE_ROW, DETAILS_W):
            c.drawString(COL_DETAILS, cursor, wrapped)
            cursor -= LEAD_ROW
    return min(cursor, y - LEAD_ROW) - 3


def draw_footer(c: pdfcanvas.Canvas, ctx: dict[str, Any]) -> None:
    c.saveState()
    c.setFont(FONT, 6)
    c.setFillColor(colors.HexColor("#555555"))
    c.translate(PAGE_W - 18, 90)
    c.rotate(90)
    c.drawString(0, 0, ctx["bank"].footer_reg)
    c.restoreState()


def draw_messages_page(c: pdfcanvas.Canvas, ctx: dict[str, Any], page_no: int, total: int) -> None:
    draw_letterhead(c, ctx, page_no, total)
    y = TOP - 130
    c.setFillColor(colors.black)
    c.setFont(FONT_B, 10)
    c.drawString(M_L, y, "Messages For You")
    c.line(M_L, y - 3, M_L + 88, y - 3)
    y -= 26

    for heading, body in [
        ("Revision in account pricing for DBS Corporate Multi-Currency Account",
         "Account fall below fee for DBS Corporate Multi-Currency Account will be revised to "
         "S$40 or equivalent. Please visit: go.dbs.com/sg-pricing"),
        ("Cessation of printed Corporate Account Statements for DBS IDEAL users",
         "We will progressively stop mailing printed statements. Please view or download your "
         "eStatements from DBS IDEAL. For more information, please visit: go.dbs.com/estatements"),
    ]:
        c.setFont(FONT_B, 8.5)
        c.drawString(M_L + 8, y, f"\u2022  {heading}")
        y -= 12
        c.setFont(FONT, 8)
        for line in simpleSplit(body, FONT, 8, PAGE_W - M_L - M_R - 30):
            c.drawString(M_L + 20, y, line)
            y -= 10
        y -= 14

    y -= 10
    c.setFont(FONT, 6.5)
    for para in [
        "DEPOSIT INSURANCE SCHEME - SINGAPORE DOLLAR DEPOSITS OF NON-BANK DEPOSITORS AND "
        "MONIES AND DEPOSITS DENOMINATED IN SINGAPORE DOLLARS UNDER THE SUPPLEMENTARY "
        "RETIREMENT SCHEME ARE INSURED BY THE SINGAPORE DEPOSIT INSURANCE CORPORATION, FOR UP "
        "TO S$75,000 IN AGGREGATE PER DEPOSITOR PER SCHEME MEMBER BY LAW.",
        "PLEASE CHECK THIS STATEMENT OF ACCOUNT CAREFULLY UNLESS WE RECEIVE NOTIFICATION OF ANY "
        "DISCREPANCY OR INACCURACY IN RESPECT OF THE ENTRIES IN THIS STATEMENT WITHIN FOURTEEN "
        "(14) DAYS FROM RECEIPT OF THIS STATEMENT, IT SHALL BE CONCLUSIVE AND BINDING ON YOU.",
        "PLEASE NOTIFY THE BANK IN WRITING IF YOU CHANGE YOUR ADDRESS OR IF YOU DO NOT RECEIVE "
        "YOUR STATEMENT FOR THE PRECEDING MONTH.",
    ]:
        for line in simpleSplit(para, FONT, 6.5, PAGE_W - M_L - M_R):
            c.drawString(M_L, y, line)
            y -= 8
        y -= 6

    c.setFont(FONT, 8)
    c.drawString(M_L, 110,
                 "For any queries please contact DBS BusinessCare at 1800 222 2200 "
                 "(+65 6222 2200 from overseas) or BusinessCareSG@dbs.com.")
    c.drawString(M_L, 98, "Thank you for banking with DBS Bank.")
    draw_footer(c, ctx)


def render(ctx: dict[str, Any], blocks: list[Block], out_pdf: Path) -> Path:
    slices = paginate(blocks)
    total_pages = len(slices) + 1  # + messages page

    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = pdfcanvas.Canvas(str(out_pdf), pagesize=A4)
    c.setTitle(f"Account Statement — {ctx['company_name']}")
    c.setAuthor(ctx["bank"].legal_name)

    for i, sl in enumerate(slices, start=1):
        y = draw_letterhead(c, ctx, i, total_pages)
        y = draw_account_band(c, ctx, sl.block, y)
        y = draw_column_headers(c, y)

        c.setFont(FONT_B, 7.5)
        c.drawRightString(COL_BALANCE_R, y, "Currency: SGD")
        y -= 12
        c.setFont(FONT, SIZE_ROW)
        c.drawString(COL_DETAILS, y, "Balance Brought Forward")
        c.drawRightString(COL_BALANCE_R, y, f"{sl.carried_in:,.2f}")
        y -= LEAD_ROW + 4

        for txn in sl.rows:
            y = draw_row(c, txn, y)

        if sl.is_last_slice:
            y -= 8
            c.setFont(FONT, SIZE_ROW)
            c.drawString(COL_DETAILS, y, "Total")
            c.drawRightString(COL_WITHDRAW_R, y, f"{sl.block.total_withdrawal:,.2f}")
            c.drawRightString(COL_DEPOSIT_R, y, f"{sl.block.total_deposit:,.2f}")
            y -= LEAD_ROW + 4
            c.drawString(COL_DETAILS, y, "Balance Carried Forward")
            c.drawRightString(COL_BALANCE_R, y, f"{sl.block.closing:,.2f}")

        draw_footer(c, ctx)
        c.showPage()

    draw_messages_page(c, ctx, total_pages, total_pages)
    c.showPage()
    c.save()
    return out_pdf


# --------------------------------------------------------------------------
# 7. CLI
# --------------------------------------------------------------------------

def build_blocks(ctx: dict[str, Any], opening: float | None = None) -> list[Block]:
    blocks: list[Block] = []
    rng_open = seeded_rng(ctx["case_id"] + "|opening")
    balance = opening if opening is not None else round(rng_open.uniform(15_000, 90_000), 2)

    for month in ctx["months"]:
        rng = seeded_rng(f"{ctx['case_id']}|{month:%Y-%m}")
        txns = month_transactions(ctx, month, balance, rng)
        blocks.append(
            Block(
                month=month,
                opening=balance,
                txns=txns,
                closing=txns[-1].balance if txns else balance,
                total_withdrawal=round(sum(t.withdrawal or 0 for t in txns), 2),
                total_deposit=round(sum(t.deposit or 0 for t in txns), 2),
            )
        )
        balance = blocks[-1].closing
    return blocks


def generate(xlsx: str | Path, case_id: str, outdir: str | Path,
             synthetic_tag: bool = True, bank: str | None = None) -> Path:
    ctx = load_case(xlsx, case_id, bank_override=bank)
    blocks = build_blocks(ctx)
    renderer = RENDERERS[ctx["bank"].template]
    out = renderer(ctx, blocks, Path(outdir) / ctx["filename"])

    if synthetic_tag:
        from pypdf import PdfReader, PdfWriter
        reader = PdfReader(str(out))
        writer = PdfWriter(clone_from=reader)
        writer.add_metadata({"/Keywords": SYNTHETIC_TAG})
        with open(out, "wb") as fh:
            writer.write(fh)

    months = ", ".join(m.strftime("%Y-%m") for m in ctx["months"])
    print(f"{case_id}  {out.name}  months={months}  "
          f"opening={blocks[0].opening:,.2f}  closing={blocks[-1].closing:,.2f}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx")
    ap.add_argument("--case", required=True, help="case ID, e.g. C001")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--bank", help="override the mastersheet bank, e.g. UOB")
    ap.add_argument("--no-synthetic-tag", action="store_true")
    args = ap.parse_args()
    generate(args.xlsx, args.case, args.outdir,
             synthetic_tag=not args.no_synthetic_tag, bank=args.bank)



# ==========================================================================
# 8. UOB template — iBanking "Account Activities" export
# ==========================================================================

U_M_L, U_M_R = 35, 35
U_TOP = PAGE_H - 38
U_BOTTOM = 95

U_COL_STMT = U_M_L + 5
U_COL_TXN = U_M_L + 95
U_COL_DESC = U_M_L + 195
U_DESC_W = 108
U_COL_DEP_R = PAGE_W - U_M_R - 165
U_COL_WDR_R = PAGE_W - U_M_R - 95
U_COL_LEDGER_R = PAGE_W - U_M_R - 8

# Row caps per page. The first page also carries the summary cards, so it holds far
# fewer rows than the continuation pages.
U_ROWS_FIRST_PAGE = 3
U_ROWS_PER_PAGE = 10


@dataclass
class UobRow:
    stmt_date: date
    txn_dt: datetime
    lines: list[str]
    deposit: float | None
    withdrawal: float | None
    balance: float


def to_uob_rows(blocks: list[Block], rng: random.Random) -> list[UobRow]:
    """Flatten the shared transaction model into the UOB export's row shape."""
    rows: list[UobRow] = []
    for block in blocks:
        for t in block.txns:
            stamp = datetime.combine(
                t.txn_date,
                datetime.min.time().replace(
                    hour=rng.randint(9, 20), minute=rng.randint(0, 59), second=rng.randint(0, 59)
                ),
            )
            rows.append(
                UobRow(
                    stmt_date=t.txn_date,
                    txn_dt=stamp,
                    lines=_uob_description(t),
                    deposit=t.deposit if t.deposit is not None else 0.00,
                    withdrawal=t.withdrawal if t.withdrawal is not None else 0.00,
                    balance=t.balance,
                )
            )
    return rows


def _uob_description(t: Txn) -> list[str]:
    """Rewrite a DBS-flavoured narrative into UOB iBanking wording."""
    head = t.lines[0]
    rest = t.lines[1:]
    if head.startswith("Inward PayNow"):
        return ["Inward Cr - FAST"] + rest[::-1][:2]
    if head.startswith("REMITTANCE"):
        return ["Funds Trf - FAST"] + rest[::-1][:2]
    if head.startswith("FAST PAYMENT SALARY"):
        return ["Funds Transfer-IB"] + rest[::-1][:2]
    if head.startswith("SERVICE CHARGE FOR"):
        return ["SVC Chg"] + rest[:1]
    if head.startswith("GIRO"):
        label = "Inward CR - GIRO" if t.deposit else "GIRO Payment"
        return [label] + rest[:1]
    if head.startswith("BUSINESS ADVANCE CARD"):
        return ["Card Transaction"] + rest[:1]
    return [head] + rest[:1]


def _uob_row_height(row: UobRow) -> float:
    lines = sum(len(simpleSplit(ln, FONT, 7.2, U_DESC_W)) or 1 for ln in row.lines)
    return max(lines * 9.2 + 14, 34)


def uob_draw_header(c: pdfcanvas.Canvas, ctx: dict[str, Any]) -> float:
    bank = ctx["bank"]
    logo = load_logo(bank.logo_path) if bank.logo_path else None
    y = U_TOP - 34
    if logo:
        iw, ih = logo.getSize()
        h = 26
        c.drawImage(logo, U_M_L, y, width=iw * (h / ih), height=h, mask="auto")
    else:
        c.setFillColor(UOB_NAVY)
        c.setFont(FONT_B, 20)
        c.drawString(U_M_L, y + 4, bank.name)

    c.setStrokeColor(UOB_RULE)
    c.setLineWidth(0.8)
    c.line(U_M_L, y - 10, PAGE_W - U_M_R, y - 10)

    c.setFillColor(UOB_NAVY)
    c.setFont(FONT_B, 12)
    c.drawString(U_M_L, y - 30, "Account Activities")
    c.line(U_M_L, y - 40, PAGE_W - U_M_R, y - 40)
    return y - 58


def uob_draw_footer(c: pdfcanvas.Canvas, ctx: dict[str, Any], page_no: int, total: int) -> None:
    export = ctx["export_stamp"]
    c.setFillColor(UOB_LABEL)
    c.setFont(FONT, 7.5)
    c.drawString(U_M_L, 78,
                 f"Date of Export:  {export:%d/%m/%Y}  |  Time of Export:  {export:%I:%M:%S %p}")
    c.setFillColor(UOB_NAVY)
    c.setFont(FONT_B, 7.5)
    c.drawString(U_M_L, 66, "Account Activities")

    c.setFillColor(colors.HexColor("#666666"))
    c.setFont(FONT, 7.5)
    c.drawCentredString(PAGE_W / 2, 46, f"{page_no} of {total}")

    # Company chop, bottom right.
    stamp = load_stamp(ctx["bank"].stamp_path) if ctx["bank"].stamp_path else None
    if stamp is not None:
        size = 46
        iw, ih = stamp.getSize()
        sw, sh = (size, size * ih / iw) if iw >= ih else (size * iw / ih, size)
        c.drawImage(stamp, PAGE_W - U_M_R - sw, 76 - sh / 2, width=sw, height=sh, mask="auto")
    else:
        c.setStrokeColor(colors.HexColor("#D32C3A"))
        c.setLineWidth(1.4)
        c.circle(PAGE_W - U_M_R - 26, 76, 20, stroke=1, fill=0)
        c.setLineWidth(0.6)
        c.circle(PAGE_W - U_M_R - 26, 76, 16, stroke=1, fill=0)


def _uob_card(c: pdfcanvas.Canvas, x: float, y: float, w: float, h: float) -> None:
    c.setStrokeColor(UOB_RULE)
    c.setFillColor(colors.white)
    c.setLineWidth(0.8)
    c.roundRect(x, y - h, w, h, 4, stroke=1, fill=1)


def _uob_field(c: pdfcanvas.Canvas, x: float, y: float, label: str, value: str,
               max_width: float | None = None) -> float:
    """Draw a label/value pair, wrapping the value when max_width is given.

    Returns the y position below the last line drawn, so callers can stack fields
    without assuming a fixed height.
    """
    c.setFillColor(UOB_LABEL)
    c.setFont(FONT, 7.5)
    c.drawString(x, y, label)
    c.setFillColor(colors.black)
    c.setFont(FONT, 8.5)
    lines = simpleSplit(value, FONT, 8.5, max_width) if max_width else [value]
    cursor = y - 12
    for line in lines or [""]:
        c.drawString(x, cursor, line)
        cursor -= 11
    return cursor + 11


class UobSummaryGeometry(TypedDict):
    half: float
    inner_w: float
    account_line: str
    card_h: float
    end_y: float


def uob_summary_geometry(ctx: dict[str, Any], y: float) -> UobSummaryGeometry:
    """Measure the summary block without drawing it, so paginate and render agree."""
    w_full = PAGE_W - U_M_L - U_M_R
    half = (w_full - 12) / 2
    inner_w = half - 28
    acct = ctx["account_no"]
    account_line = f"{acct} {ctx['company_name']} SGD {acct}"
    acct_lines = len(simpleSplit(account_line, FONT, 8.5, inner_w)) or 1
    name_lines = len(simpleSplit(ctx["company_name"], FONT, 8.5, inner_w)) or 1
    card_h = 60 + name_lines * 11 + 24 + acct_lines * 11 + 12
    # card block + details card + "Account Transactions" heading and filter lines
    end_y = y - card_h - 12 - 126 - 76
    return {
        "half": half,
        "inner_w": inner_w,
        "account_line": account_line,
        "card_h": card_h,
        "end_y": end_y,
    }


def uob_draw_summary(c: pdfcanvas.Canvas, ctx: dict[str, Any], y: float,
                     record_count: int) -> float:
    w_full = PAGE_W - U_M_L - U_M_R
    geo = uob_summary_geometry(ctx, y)
    half, inner_w = geo["half"], geo["inner_w"]
    card_h, account_line = geo["card_h"], geo["account_line"]

    # Company / Account and Account Balance cards, sized from their wrapped content.
    _uob_card(c, U_M_L, y, half, card_h)
    _uob_card(c, U_M_L + half + 12, y, half, card_h)
    c.setFillColor(UOB_NAVY)
    c.setFont(FONT_B, 11)
    c.drawString(U_M_L + 14, y - 22, "Company / Account")
    c.drawString(U_M_L + half + 26, y - 22, "Account Balance")

    after_name = _uob_field(c, U_M_L + 14, y - 48, "Company", ctx["company_name"], inner_w)
    _uob_field(c, U_M_L + 14, after_name - 24, "Account", account_line, inner_w)
    _uob_field(c, U_M_L + half + 26, y - 48, "Available Balance", f"{ctx['closing']:,.2f}")
    _uob_field(c, U_M_L + half + 26, after_name - 24, "Ledger Balance", f"{ctx['closing']:,.2f}")
    y -= card_h + 12

    # Account Details card
    _uob_card(c, U_M_L, y, w_full, 108)
    c.setFillColor(UOB_NAVY)
    c.setFont(FONT_B, 11)
    c.drawString(U_M_L + 14, y - 22, "Account Details")
    cols = [U_M_L + 14 + i * (w_full - 28) / 5 for i in range(5)]
    top_fields = [
        ("Account Type", "Current Account"),
        ("Account Branch", ctx["bank"].branch),
        ("Overdraft Facility", "0.00"),
        ("Primary Account", "No"),
        ("Account Currency", "SGD"),
    ]
    bot_fields = [
        ("Total Float", "0.00"),
        ("Account Nature", "M"),
        ("Allocated Amount", "0.00"),
        ("Earmark", "0.00"),
    ]
    for x, (lab, val) in zip(cols, top_fields):
        _uob_field(c, x, y - 48, lab, val)
    for x, (lab, val) in zip(cols, bot_fields):
        _uob_field(c, x, y - 84, lab, val)
    y -= 126

    # Account Transactions block
    c.setFillColor(UOB_NAVY)
    c.setFont(FONT_B, 12)
    c.drawString(U_M_L, y, "Account Transactions")
    y -= 24
    first, last = ctx["period"]
    _uob_field(c, U_M_L, y, "Account Type", "Withdrawal and Deposit")
    _uob_field(c, U_M_L + 160, y, "Statement Date",
               f"{first:%d/%m/%Y} - {last:%d/%m/%Y}")
    y -= 34
    c.setFillColor(colors.black)
    c.setFont(FONT, 8)
    c.drawString(U_M_L, y, f"{record_count} records (Note: The above filters are applied.)")
    return y - 18


def uob_draw_table_head(c: pdfcanvas.Canvas, y: float) -> float:
    w_full = PAGE_W - U_M_L - U_M_R
    c.setFillColor(UOB_TABLE_HEAD)
    c.rect(U_M_L, y - 26, w_full, 26, stroke=0, fill=1)
    c.setFillColor(UOB_NAVY)
    c.setFont(FONT_B, 8)
    c.drawString(U_COL_STMT, y - 17, "Statement Date")
    c.drawString(U_COL_TXN, y - 17, "Transaction Date")
    c.drawString(U_COL_DESC, y - 17, "Description")
    c.drawRightString(U_COL_DEP_R, y - 17, "Deposit(SGD)")
    c.drawRightString(U_COL_WDR_R, y - 17, "Withdrawal(SGD)")
    c.drawRightString(U_COL_LEDGER_R, y - 17, "Ledger Balance(SGD)")
    return y - 26


def uob_draw_row(c: pdfcanvas.Canvas, row: UobRow, y: float) -> float:
    h = _uob_row_height(row)
    w_full = PAGE_W - U_M_L - U_M_R
    c.setStrokeColor(UOB_RULE)
    c.setLineWidth(0.6)
    c.rect(U_M_L, y - h, w_full, h, stroke=1, fill=0)

    mid = y - h / 2
    c.setFillColor(colors.black)
    c.setFont(FONT, 7.5)
    c.drawString(U_COL_STMT, mid - 3, f"{row.stmt_date:%d/%m/%Y}")
    c.drawString(U_COL_TXN, mid + 3, f"{row.txn_dt:%d/%m/%Y %I:%M:%S}")
    c.drawString(U_COL_TXN, mid - 6, f"{row.txn_dt:%p}")

    cursor = y - 14
    c.setFont(FONT, 7.2)
    for line in row.lines:
        for wrapped in simpleSplit(line, FONT, 7.2, U_DESC_W):
            c.drawString(U_COL_DESC, cursor, wrapped)
            cursor -= 9.2

    c.setFont(FONT, 7.5)
    c.drawRightString(U_COL_DEP_R, mid - 3, f"{row.deposit:,.2f}")
    c.drawRightString(U_COL_WDR_R, mid - 3, f"{row.withdrawal:,.2f}")
    c.drawRightString(U_COL_LEDGER_R, mid - 3, f"{row.balance:,.2f}")
    return y - h


def uob_draw_closing_page(c: pdfcanvas.Canvas, ctx: dict[str, Any]) -> None:
    y = uob_draw_header(c, ctx)
    c.setFillColor(UOB_LABEL)
    c.setFont(FONT, 8.5)
    c.drawRightString(U_COL_WDR_R - 20, y - 20, "Total Deposits(SGD)")
    c.drawRightString(U_COL_LEDGER_R, y - 20, "Total Withdrawals(SGD)")
    c.setFillColor(colors.black)
    c.setFont(FONT, 8.5)
    c.drawRightString(U_COL_WDR_R - 20, y - 34, f"{ctx['total_deposit']:,.2f}")
    c.drawRightString(U_COL_LEDGER_R, y - 34, f"{ctx['total_withdrawal']:,.2f}")

    y -= 66
    c.setFillColor(colors.black)
    c.setFont(FONT_B, 8)
    c.drawString(U_M_L, y, "Note")
    c.setFont(FONT, 8)
    c.drawString(U_M_L, y - 11, "Balances and details reflected are indicative.")

    y -= 34
    c.setFont(FONT_B, 8)
    c.drawString(U_M_L, y, "Deposit Insurance Scheme")
    c.setFont(FONT, 8)
    body = ("Singapore dollar deposits of non-bank depositors and monies and deposits "
            "denominated in Singapore dollars under the Supplementary Retirement Scheme are "
            "insured by the Singapore Deposit Insurance Corporation, for up to S$100,000 in "
            "aggregate per depositor per Scheme member by law. Foreign currency deposits, dual "
            "currency investments, structured deposits and other investment products are not "
            "insured.")
    yy = y - 11
    for line in simpleSplit(body, FONT, 8, PAGE_W - U_M_L - U_M_R):
        c.drawString(U_M_L, yy, line)
        yy -= 10

    yy -= 24
    _uob_card(c, U_M_L, yy, PAGE_W - U_M_L - U_M_R, 105)
    c.setFillColor(UOB_LABEL)
    c.setFont(FONT, 8.5)
    c.drawString(U_M_L + 14, yy - 22, "For Customer's Remarks")


def render_uob(ctx: dict[str, Any], blocks: list[Block], out_pdf: Path) -> Path:
    rng = seeded_rng(ctx["case_id"] + "|uob-times")
    rows = to_uob_rows(blocks, rng)

    ctx["closing"] = blocks[-1].closing
    ctx["total_deposit"] = round(sum(b.total_deposit for b in blocks), 2)
    ctx["total_withdrawal"] = round(sum(b.total_withdrawal for b in blocks), 2)
    ctx["period"] = (blocks[0].month, rows[-1].stmt_date)
    ctx["export_stamp"] = datetime.combine(
        rows[-1].stmt_date + timedelta(days=rng.randint(1, 20)),
        datetime.min.time().replace(hour=rng.randint(0, 23), minute=rng.randint(0, 59),
                                    second=rng.randint(0, 59)),
    )

    # Pagination pass. Two limits apply: a hard row cap per page, and the real
    # vertical space left once the header, summary block and table head are placed.
    header_end = U_TOP - 34 - 58
    first_avail = uob_summary_geometry(ctx, header_end)["end_y"] - 26 - U_BOTTOM
    rest_avail = header_end - 26 - U_BOTTOM

    pages: list[list[UobRow]] = []
    current: list[UobRow] = []
    budget, cap = first_avail, U_ROWS_FIRST_PAGE
    for row in rows:
        h = _uob_row_height(row)
        if current and (len(current) >= cap or budget - h < 0):
            pages.append(current)
            current, budget, cap = [], rest_avail, U_ROWS_PER_PAGE
        budget -= h
        current.append(row)
    if current:
        pages.append(current)

    total_pages = len(pages) + 1
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    c = pdfcanvas.Canvas(str(out_pdf), pagesize=A4)
    c.setTitle(f"Account Activities — {ctx['company_name']}")
    c.setAuthor(ctx["bank"].legal_name)

    for i, page_rows in enumerate(pages, start=1):
        y = uob_draw_header(c, ctx)
        if i == 1:
            y = uob_draw_summary(c, ctx, y, len(rows))
        y = uob_draw_table_head(c, y)
        for row in page_rows:
            y = uob_draw_row(c, row, y)
        uob_draw_footer(c, ctx, i, total_pages)
        c.showPage()

    uob_draw_closing_page(c, ctx)
    uob_draw_footer(c, ctx, total_pages, total_pages)
    c.showPage()
    c.save()
    return out_pdf


RENDERERS = {"dbs": render, "uob": render_uob}


if __name__ == "__main__":
    main()
