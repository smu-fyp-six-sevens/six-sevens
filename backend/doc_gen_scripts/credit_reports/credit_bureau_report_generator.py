"""
Generates a Credit Bureau (Singapore) consumer credit report template based
on the attached sample. The script is structured so the same report blocks
can later be populated with different values while keeping the page flow
stable.

Every field is a keyword argument to generate_credit_bureau_report(), so it can be
called standalone (see __main__ below) with no mastersheet at all. The CLI instead
drives it from the mastersheet's Credit Reports tab, one report per row, joined back
to Case Registry (for the applicant's identity: name, NRIC, DOB, application date) and
Individuals (for nationality/residential address of that same Director & UBO row) --
the same "Applicant = Director & UBO" convention the other generators use. Output
filenames come from the Credit Reports tab's own Filename column (e.g.
C001_CreditReport.pdf), so a run drops straight into the folder the mastersheet
already expects.

The credit-history content itself (accounts, previous enquiries, defaults,
bankruptcy rows, bureau score, narratives) has no mastersheet source -- none of
those columns exist -- so the CLI path keeps generate_credit_bureau_report()'s own
sample rows (SAMPLE_ACCOUNTS et al.) as a shared template: same banks, products and
narrative text every case, but build_persona() runs each case's copy through
_jitter_amount()/_jitter_date() with that case's own seeded RNG, nudging the balances
and dates by a small, bounded amount (a related pair -- an account's open/close
dates, or one bankruptcy's stages -- shifts together so the gap between them holds).
Score/risk grade/probability of default and the credit limits are seeded picks from a
small pool (SCORE_BANDS et al.) the same way. So no two cases are byte-identical, but
none of it swings wildly either. Only the identity/enquiry-linkage fields are real.

Usage:

cd backend/doc_gen_scripts/credit_reports
python credit_bureau_report_generator.py ../v9_document_mastersheet.xlsx --outdir out/ (for all cases in the mastersheet)
python credit_bureau_report_generator.py ../v9_document_mastersheet.xlsx --outdir out/ --cases C001 C002

Run with no arguments instead to build one example report for a sample consumer
("Peter Tan") at output/credit_bureau_report.pdf -- useful for eyeballing the page
layout without touching the mastersheet.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import openpyxl
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from case_facts import as_date, seeded_rng
from nric_passport.nric_generator import derive_gender

styles = getSampleStyleSheet()
FONT = "Helvetica"

# Logo supplied to more closely match the real CBS template.
LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "images" / "CBS_Logo.png"
LOGO_ASPECT = 785 / 1893  # height / width of CBS_Logo.png, for scaling by width
BRAND_NAVY = colors.HexColor("#0E1A64")  # sampled from the logo's "CBS" wordmark

# Score/risk-grade/probability-of-default triples and credit-limit pools have no
# mastersheet column -- seeded per case (see build_persona) purely so cases don't
# all print the identical numbers, same spirit as case_facts.py's SHARE_CAPITALS.
SCORE_BANDS = [
    ("1823", "AA", "1.02%"),
    ("1701", "BB", "1.86%"),
    ("1569", "HH", "3.56%"),
    ("1420", "GG", "5.14%"),
    ("1288", "FF", "7.90%"),
    ("1105", "EE", "12.40%"),
]
SECURED_LIMITS = [0, 250_000, 500_000, 750_000, 1_000_000, 1_500_000]
UNSECURED_LIMITS = [5_000, 10_000, 15_000, 20_000, 30_000, 50_000]
EXEMPTED_LIMITS = [0, 0, 0, 500, 1_000]


@dataclass(frozen=True)
class EnquiryDetail:
    label: str
    value: str


@dataclass(frozen=True)
class AccountRow:
    product_type: str
    grantor: str
    account_type: str
    date_opened: str
    date_closed: str
    overdue_balance: str
    cycles: str
    cash_advance: str = ""
    # Outstanding principal / minimum monthly payment while the account is still
    # open -- blank for closed accounts. Feeds the "Aggregated Outstanding
    # Balances"/"Aggregated Monthly Instalment" tables so those actually reconcile
    # with whichever accounts this case ended up with, instead of being hardcoded.
    current_balance: str = ""
    monthly_instalment: str = ""


# Template rows for the credit-history blocks (Account Status History, Previous
# Enquiries, Default Records, Bankruptcy Proceedings, Narratives). No mastersheet
# column backs any of these, so build_persona() doesn't invent new ones -- it takes
# this same template and runs it through _jitter_* (below) so each case's numbers and
# dates move a little without the banks/products/narrative text changing case to case.
# Real-looking grantor banks to substitute for the "Bank A"/"Bank B"/"Bank C"
# placeholders in SAMPLE_ACCOUNTS et al. -- build_persona() draws 3 distinct names
# per case (seeded), so the credit history doesn't read as obviously templated.
GRANTOR_BANK_POOL = [
    "DBS Bank", "OCBC Bank", "UOB", "Citibank", "Maybank", "HSBC", "CIMB Bank", "Standard Chartered",
]


def _pick_grantor_banks(rng) -> dict[str, str]:
    a, b, c = rng.sample(GRANTOR_BANK_POOL, 3)
    return {"Bank A": a, "Bank B": b, "Bank C": c}


# The pool a case's account history is drawn from -- not every case gets all 7 (see
# _pick_accounts()), so not every case has the same set of loans/cards.
SAMPLE_ACCOUNTS = [
    AccountRow("Unsecured Credit Card", "Bank A", "Single", "22/03/2004", "05/03/2024", "6,500.00", "WHHHDDDDCBA*", "NNNNNNNNNNN-"),
    AccountRow("HDB Loan", "Bank A", "Joint", "10/10/2014", "", "", "AAAAAABAAAAA", current_balance="157,000.00", monthly_instalment="2,800.00"),
    AccountRow("Executive Condominium Purchase", "Bank B", "Single", "01/01/2021", "", "", "ABBBBBBBAAAA", current_balance="980,000.00", monthly_instalment="4,450.00"),
    AccountRow("Unsecured Personal Loan", "Bank B", "Single", "01/09/2010", "", "", "AAAAAAAAAAAB", current_balance="7,000.00", monthly_instalment="300.00"),
    AccountRow("Unsecured Credit Card", "Bank B", "Single", "22/03/2004", "", "", "AAAAAAAAAAA*", "NNNNNNNNNNN-", current_balance="2,500.00", monthly_instalment="100.00"),
    AccountRow("Private Residential Purchase", "Bank C", "Single", "30/11/2004", "20/07/2024", "90,000.00", "RHDDDCBAACBA"),
    AccountRow("Mortgage Restructured Loan", "Bank C", "Single", "20/07/2024", "", "", "AAAAAAAAAAAA", current_balance="75,000.00", monthly_instalment="1,000.00"),
]
SAMPLE_PREVIOUS_ENQUIRIES = [
    ("01/01/2025", "Bank B", "New Application", "Unsecured Personal Loan", "Single"),
    ("20/12/2024", "Bank B", "Review", "Unsecured Credit Card", "Single"),
    ("25/11/2024", "Bank A", "Review", "HDB Loan", "Joint"),
]
SAMPLE_DEFAULT_RECORDS = [
    ("Unsecured Credit Card", "Bank A", "05/03/2024", "6,500.00", "1,300.00", "Negotiated Settlement"),
]
SAMPLE_BANKRUPTCY_ROWS = [
    ("11111", "01/03/2022", "", "", ""),
    ("11111", "30/10/2023", "", "", "Bankruptcy Order"),
]
SAMPLE_NARRATIVES = [
    (date(2025, 12, 25), "Debt Management Programme", "As at 21/12/2025, consumer is on the Debt Management Program with Credit Counselling Singapore."),
    (date(2025, 9, 30), "ID Theft", "Bank A advised on 30/09/2025 that ID theft activities were identified and consumer’s identity has been compromised. A police report G/20250921/210 was lodged by the consumer."),
    (date(2025, 12, 1), "General", "As at 01/12/2025, consumer is on Financial Capability Building Support (Debt Relief) Programme 2024 with Credit Counselling Singapore."),
    (date(2026, 2, 1), "General", "As at 01/02/2026, consumer is on MSF ComLink+ Progress Package for Debt Clearance."),
]


def _jitter_amount(value: str, rng, pct: float = 0.12) -> str:
    """Perturb a formatted amount like '6,500.00' by a small random +/-pct, keeping
    the same comma/decimal formatting. Blank (no balance) stays blank."""
    if not value:
        return value
    try:
        base = float(value.replace(",", ""))
    except ValueError:
        return value
    if base == 0:
        return value
    return f"{base * (1 + rng.uniform(-pct, pct)):,.2f}"


def _jitter_date(value: str, rng, max_days: int = 30, offset_days: int | None = None) -> str:
    """Shift a dd/mm/yyyy date string by a small random number of days -- or by a
    given offset, so a related pair/group of dates can move together and keep their
    original gap (e.g. an account's open/close dates, or one bankruptcy's stages).
    Blank stays blank."""
    if not value:
        return value
    try:
        dt = datetime.strptime(value, "%d/%m/%Y")
    except ValueError:
        return value
    days = rng.randint(-max_days, max_days) if offset_days is None else offset_days
    return (dt + timedelta(days=days)).strftime("%d/%m/%Y")


# The template history's own span: its oldest account opened, and its newest account or
# default event. _history_retimer maps that span onto each applicant's adult life.
_TEMPLATE_HISTORY_START = date(2004, 3, 22)
_TEMPLATE_HISTORY_END = date(2024, 7, 20)
# The template's most recent previous enquiry.
_TEMPLATE_LAST_ENQUIRY = date(2025, 1, 1)


def _years_after(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 Feb
        return day.replace(year=day.year + years, day=28)


def _history_retimer(dob: date, enquiry_date: date):
    """Maps a template account/default date onto this applicant's credit history: the oldest
    account at 21 at the earliest (and no more than 22 years back), the newest event two months
    before this enquiry. The template's spacing is kept, compressed for a younger applicant."""
    end = enquiry_date - timedelta(days=60)
    # 31 days past the 21st birthday: _jitter_accounts then moves each account up to 30 days either way, and a
    # card opened before 21 is flagged (CR.AGE_AT_OPENING).
    start = max(_years_after(dob, 21) + timedelta(days=31), enquiry_date - timedelta(days=round(22 * 365.25)))
    start = min(start, end - timedelta(days=365))
    span = (_TEMPLATE_HISTORY_END - _TEMPLATE_HISTORY_START).days
    scale = min(1.0, (end - start).days / span)
    if scale == 1.0:
        start = end - timedelta(days=span)

    def retime(value: str) -> str:
        if not value:
            return value
        template = datetime.strptime(value, "%d/%m/%Y").date()
        offset = round((template - _TEMPLATE_HISTORY_START).days * scale)
        return (start + timedelta(days=offset)).strftime("%d/%m/%Y")
    return retime


def _jitter_accounts(accounts: list[AccountRow], rng, bank_map: dict[str, str], retime=lambda v: v) -> list[AccountRow]:
    jittered = []
    for row in accounts:
        shift = rng.randint(-30, 30)  # keeps each row's open/close gap intact
        jittered.append(AccountRow(
            product_type=row.product_type,
            grantor=bank_map.get(row.grantor, row.grantor),
            account_type=row.account_type,
            date_opened=_jitter_date(retime(row.date_opened), rng, offset_days=shift),
            date_closed=_jitter_date(retime(row.date_closed), rng, offset_days=shift),
            overdue_balance=_jitter_amount(row.overdue_balance, rng),
            cycles=row.cycles,
            cash_advance=row.cash_advance,
            current_balance=_jitter_amount(row.current_balance, rng),
            monthly_instalment=_jitter_amount(row.monthly_instalment, rng, pct=0.05),
        ))
    return jittered


def _pick_accounts(rng, pool: list[AccountRow] = SAMPLE_ACCOUNTS) -> list[AccountRow]:
    """Not every applicant has the same set of loans/cards -- pick a random-sized
    subset of the pool (at least 1, up to all of it), keeping the pool's original
    relative order."""
    k = rng.randint(1, len(pool))
    indices = sorted(rng.sample(range(len(pool)), k))
    return [pool[i] for i in indices]


def _jitter_enquiries(rows: list[tuple], rng, bank_map: dict[str, str], shift_days: int = 0) -> list[tuple]:
    return [(_jitter_date(_jitter_date(a, rng, offset_days=shift_days), rng), bank_map.get(b, b), c, d, e)
            for a, b, c, d, e in rows]


def _jitter_defaults(rows: list[tuple], rng, bank_map: dict[str, str], retime=lambda v: v) -> list[tuple]:
    return [
        (a, bank_map.get(b, b), _jitter_date(retime(c), rng), _jitter_amount(d, rng), _jitter_amount(e, rng), f)
        for a, b, c, d, e, f in rows
    ]


def _jitter_bankruptcy(rows: list[tuple], rng) -> list[tuple]:
    shift = rng.randint(-30, 30)  # one shared shift keeps the stages in sequence
    return [
        (a, _jitter_date(b, rng, offset_days=shift), _jitter_date(c, rng, offset_days=shift),
         _jitter_date(d, rng, offset_days=shift), e)
        for a, b, c, d, e in rows
    ]


def _jitter_narratives(rows: list[tuple], rng, bank_map: dict[str, str], max_days: int = 30) -> list[tuple]:
    def sub(text: str) -> str:
        for placeholder, real in bank_map.items():
            text = text.replace(placeholder, real)
        return text
    return [(dt + timedelta(days=rng.randint(-max_days, max_days)), t, sub(text)) for dt, t, text in rows]


# Classifies an open account's product type into the balance/instalment buckets the
# "Aggregated Outstanding Balances"/"Aggregated Monthly Instalment" tables report --
# feeds _bucket_balances/_bucket_instalments below, which build those tables from
# whichever accounts a case actually has instead of a hardcoded, unrelated set of
# numbers.
PROPERTY_PRODUCTS = {
    "HDB Loan", "Executive Condominium Purchase", "Private Residential Purchase", "Mortgage Restructured Loan",
}
INTEREST_UNSECURED_PRODUCTS = {"Unsecured Credit Card"}
# Anything else still open (e.g. "Unsecured Personal Loan") is plain unsecured.

# Annual interest rate per product, for walking each open account's balance back
# month by month in "Aggregated Outstanding Balances for Preceding 5 Months". Each
# month's fall is that month's instalment less its interest, so it never exceeds the
# instalment, and (interest shrinking with the balance) never repeats to the cent.
ANNUAL_RATES = {
    "HDB Loan": 0.026,
    "Executive Condominium Purchase": 0.031,
    "Private Residential Purchase": 0.031,
    "Mortgage Restructured Loan": 0.035,
    "Unsecured Personal Loan": 0.07,
    "Unsecured Credit Card": 0.269,
}
DEFAULT_ANNUAL_RATE = 0.05


def _amt(value: str) -> float:
    if not value:
        return 0.0
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return 0.0


def _money0(value: float) -> str:
    return f"{value:,.2f}"


def _month_label(base: date, months_back: int = 0) -> str:
    y, m = base.year, base.month
    for _ in range(months_back):
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return date(y, m, 1).strftime("%B %Y")


def _bucket_balances(accounts: list[AccountRow]) -> tuple[list[list[str]], dict[str, float]]:
    """(per-account rows, column totals) for still-open accounts with a current
    balance -- only these contribute to outstanding balances, same as a real
    aggregated-balances table excludes accounts that have been closed/paid off."""
    rows: list[list[str]] = []
    totals = {"secured": 0.0, "unsecured": 0.0, "interest": 0.0, "exempted": 0.0}
    for a in accounts:
        if a.date_closed or not a.current_balance:
            continue
        amounts = {"secured": 0.0, "unsecured": 0.0, "interest": 0.0, "exempted": 0.0}
        amounts[_balance_bucket(a.product_type)] = _amt(a.current_balance)
        for key, value in amounts.items():
            totals[key] += value
        rows.append([a.product_type, a.grantor, *(_money0(amounts[k]) for k in ("secured", "unsecured", "interest", "exempted"))])
    return rows, totals


def _balance_bucket(product_type: str) -> str:
    if product_type in PROPERTY_PRODUCTS:
        return "secured"
    if product_type in INTEREST_UNSECURED_PRODUCTS:
        return "interest"
    return "unsecured"


def _bucket_balance_history(accounts: list[AccountRow], months: int) -> list[dict[str, float]]:
    """Column totals for each of the `months` months before the current one, most
    recent first, from each open account's balance walked back through its own
    instalments and interest."""
    history = [{"secured": 0.0, "unsecured": 0.0, "interest": 0.0, "exempted": 0.0} for _ in range(months)]
    for a in accounts:
        if a.date_closed or not a.current_balance:
            continue
        bucket = _balance_bucket(a.product_type)
        balance = _amt(a.current_balance)
        instalment = _amt(a.monthly_instalment)
        rate = ANNUAL_RATES.get(a.product_type, DEFAULT_ANNUAL_RATE) / 12
        for month in history:
            # This month's balance is last month's plus interest, less the instalment.
            balance = (balance + instalment) / (1 + rate)
            month[bucket] += balance
    return history


def _limit_covering(picked: float, outstanding: float, pool: list[int], step: int) -> float:
    """The seeded limit pick, raised when the accounts' balances already exceed it: a
    facility's outstanding balance can't be more than its limit."""
    if picked >= outstanding:
        return picked
    return next((limit for limit in sorted(pool) if limit >= outstanding), -(-outstanding // step) * step)


def _bucket_instalments(accounts: list[AccountRow]) -> tuple[list[list[str]], dict[str, float]]:
    rows: list[list[str]] = []
    totals = {"property": 0.0, "non_property": 0.0, "unsecured": 0.0, "exempted": 0.0}
    for a in accounts:
        if a.date_closed or not a.monthly_instalment:
            continue
        inst = _amt(a.monthly_instalment)
        property_amt = non_property = unsecured_amt = exempted_amt = 0.0
        if a.product_type in PROPERTY_PRODUCTS:
            property_amt = inst
        else:
            unsecured_amt = inst
        totals["property"] += property_amt
        totals["non_property"] += non_property
        totals["unsecured"] += unsecured_amt
        totals["exempted"] += exempted_amt
        rows.append([a.product_type, a.grantor, a.account_type, _money0(property_amt), _money0(non_property), _money0(unsecured_amt), _money0(exempted_amt)])
    return rows, totals


def _styles():
    return {
        "header_title": ParagraphStyle("header_title", fontName=f"{FONT}-Bold", fontSize=9, leading=11),
        "header_small": ParagraphStyle("header_small", fontName=FONT, fontSize=7.5, leading=9),
        "title": ParagraphStyle("title", fontName=f"{FONT}-Bold", fontSize=13, leading=15, alignment=TA_CENTER),
        "subtitle": ParagraphStyle("subtitle", fontName=FONT, fontSize=9.2, leading=11),
        "section": ParagraphStyle("section", fontName=f"{FONT}-Bold", fontSize=9.5, leading=11, spaceBefore=3),
        "section_bar": ParagraphStyle("section_bar", fontName=f"{FONT}-Bold", fontSize=9.5, leading=12, textColor=colors.white),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=8.2, leading=10.2),
        "body_small": ParagraphStyle("body_small", fontName=FONT, fontSize=7.6, leading=9.2),
        "body_bold": ParagraphStyle("body_bold", fontName=f"{FONT}-Bold", fontSize=8.2, leading=10.2),
        "tiny": ParagraphStyle("tiny", fontName=FONT, fontSize=6.9, leading=8.5),
    }


# Every full-width table in the report (section bars and the grids/pair-tables under
# them alike) is pinned to this width, so a table's own column widths never have to
# add up exactly right by hand -- see _scale_widths below. It's the whole frame
# between the 15 mm margins, so tables run out to the header rule's edges like the
# sample CBS report rather than stopping short of the right margin.
FULL_WIDTH = letter[0] - 30 * mm


def _scale_widths(widths: list[float], target: float = FULL_WIDTH) -> list[float]:
    """Scale a list of column widths (mm) to sum exactly to `target`, preserving
    their relative proportions. Several of the sample template's tables were hand-
    tuned to widths that summed a few mm short of, or over, FULL_WIDTH, so they
    visually bulged in or out from the navy section bar above them; routing every
    table's colWidths through this keeps them pinned to the bar's width regardless
    of how the individual column widths get tweaked later."""
    total = sum(widths)
    return [w * target / total for w in widths]


def _section_bar(text: str | list[str], col_widths: list[float] | None = None) -> Table:
    """Full-width navy header bar with white bold text -- the CBS template's section
    heading style (Enquiry Details, Personal Details, Account Status History, ...).
    Pass a list of 2+ strings for a bar split into that many labelled columns (e.g.
    the "Data Provided" / "Summary" two-up header)."""
    s = _styles()
    cells = [text] if isinstance(text, str) else text
    widths = col_widths or [FULL_WIDTH / len(cells)] * len(cells)
    return Table(
        [[Paragraph(c, s["section_bar"]) for c in cells]],
        colWidths=widths,
        style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), BRAND_NAVY),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]),
    )


CBS_ADDRESS_LINE = "2 Shenton Way #20-02 SGX Centre 1 Singapore 068804 Tel: (65) 6565 6363 Fax: (65) 6538 4326 www.creditbureau.com.sg"


def _build_pdf(output_path: str, story: list, *, producer: str = "DocuSuite Enterprise 4.2") -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    doc = BaseDocTemplate(
        str(tmp_path),
        pagesize=letter,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=30 * mm,
        bottomMargin=16 * mm,
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates([PageTemplate(id="template", frames=[frame], onPage=_draw_page_chrome)])
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


def _draw_page_chrome(canvas, doc):
    """Header (logo + issuer block + rule) and footer (page number + address line)
    redrawn on every page, matching the sample CBS template's layout."""
    width, height = letter
    canvas.saveState()

    if LOGO_PATH.exists():
        logo_w = 26 * mm
        logo_h = logo_w * LOGO_ASPECT
        canvas.drawImage(
            str(LOGO_PATH), 15 * mm, height - 13 * mm - logo_h,
            width=logo_w, height=logo_h, mask="auto", preserveAspectRatio=True,
        )

    canvas.setFont("Helvetica", 8)
    canvas.drawRightString(width - 15 * mm, height - 11 * mm, "Credit Bureau (Singapore) Pte Ltd")
    canvas.setFont("Helvetica", 7.0)
    canvas.drawRightString(width - 15 * mm, height - 14.5 * mm, "Co. Registration no.: 200100843C")

    # Below both the logo and the company-name block, with a clear gap before the
    # content frame (topMargin) starts -- the logo is tall enough relative to the
    # text block that the rule has to clear its bottom edge, not just the text's.
    canvas.setLineWidth(0.5)
    canvas.line(15 * mm, height - 26 * mm, width - 15 * mm, height - 26 * mm)

    canvas.setFont("Helvetica", 7.5)
    canvas.drawCentredString(width / 2, 10 * mm, f"Page {canvas.getPageNumber()}")
    canvas.setFont("Helvetica", 6.5)
    canvas.drawCentredString(width / 2, 6.5 * mm, CBS_ADDRESS_LINE)

    canvas.restoreState()


def _pair_table(pairs: list[tuple[str, str]], col_widths=(55 * mm, 80 * mm)) -> Table:
    s = _styles()
    t = Table(
        [[Paragraph(k, s["body_bold"]), Paragraph(v, s["body"])] for k, v in pairs],
        colWidths=_scale_widths(list(col_widths)),
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]),
    )
    # Platypus centers a Table that's narrower than the frame by default, which pulls
    # every _pair_table section (Enquiry/Personal Details, Additional *) out of
    # alignment with the full-width section bar above it. Left-align to match.
    t.hAlign = "LEFT"
    return t


# Worst-to-best risk-grade scale for the Bureau Score page's colour bar, matching the
# CBS sample template. Not all of these appear in SCORE_BANDS (which only samples a
# few bands per case) -- the bar always shows the full reference scale with whichever
# grade this case landed on picked out in a larger, bordered cell.
GRADE_ORDER = ["HH", "GG", "FF", "EE", "DD", "CC", "BB", "AA"]
GRADE_COLORS = {
    "HH": colors.HexColor("#f4901e"),
    "GG": colors.HexColor("#f7a934"),
    "FF": colors.HexColor("#fbe27a"),
    "EE": colors.HexColor("#eef0b0"),
    "DD": colors.HexColor("#cbe6b0"),
    "CC": colors.HexColor("#8fcf7c"),
    "BB": colors.HexColor("#a9c8e8"),
    "AA": colors.HexColor("#5b8bc4"),
}


def _grade_bar(current_grade: str, width: float = 163 * mm, height: float = 11 * mm) -> Table:
    cell_w = width / len(GRADE_ORDER)
    cells = []
    for g in GRADE_ORDER:
        size = 15 if g == current_grade else 10.5
        style = ParagraphStyle(
            f"grade_{g}", fontName=f"{FONT}-Bold", fontSize=size, alignment=TA_CENTER,
        )
        cells.append(Paragraph(g, style))
    style_cmds = [
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.white),
    ]
    for i, g in enumerate(GRADE_ORDER):
        style_cmds.append(("BACKGROUND", (i, 0), (i, 0), GRADE_COLORS[g]))
        if g == current_grade:
            style_cmds.append(("BOX", (i, 0), (i, 0), 1.2, colors.black))
    t = Table([cells], colWidths=[cell_w] * len(GRADE_ORDER), rowHeights=[height])
    t.setStyle(TableStyle(style_cmds))
    t.hAlign = "LEFT"
    return t


def _hx_box(width: float = 40 * mm, height: float = 9 * mm) -> Table:
    style = ParagraphStyle("hx", fontName=f"{FONT}-Bold", fontSize=12, alignment=TA_CENTER)
    t = Table([[Paragraph("HX", style)]], colWidths=[width], rowHeights=[height])
    t.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    t.hAlign = "LEFT"
    return t


def generate_credit_bureau_report(
    output_path: str,
    *,
    enquiry_number: str,
    enquiry_date: date,
    reference: str,
    consumer_name: str,
    id_type: str,
    id_number: str,
    date_of_birth: date,
    postal_code: str,
    enquiry_type: str,
    product_type: str,
    applicant_type: str,
    earliest_credit_account_date: date,
    secured_credit_limit: float,
    unsecured_credit_limit: float,
    exempted_credit_limit: float,
    debt_management_programme: str,
    id_theft: str,
    surname: str,
    first_name: str,
    nationality: str,
    marital_status: str,
    address: str,
    gender: str = "Male",
    unformatted_name: str = "",
    additional_id: tuple[str, str, date] | None = None,
    additional_names: list[tuple[date, str]] | None = None,
    additional_addresses: list[tuple[date, str]] | None = None,
    accounts: list[AccountRow] | None = None,
    previous_enquiries: list[tuple[str, str, str, str, str]] | None = None,
    default_records: list[tuple[str, str, str, str, str, str]] | None = None,
    bankruptcy_rows: list[tuple[str, str, str, str, str]] | None = None,
    score: str = "1569",
    risk_grade: str = "HH",
    probability_of_default: str = "3.56%",
    narratives: list[tuple[date, str, str]] | None = None,
) -> dict:
    s = _styles()
    story: list = []

    # `is None` rather than falsy-`or`: a caller (build_persona, for a real mastersheet
    # identity) needs to be able to pass [] to suppress these sample aliases -- showing
    # "Peter Tan A.K." as a name history entry under a real person's real NRIC would be
    # actively wrong, not just decorative, so an empty list has to mean "none", not
    # "unset -- substitute the demo data".
    if additional_id is None:
        additional_id = ("Passport", "A524575", date(2020, 1, 31))
    if additional_names is None:
        additional_names = [
            (date(2021, 3, 10), "Peter Tan"),
            (date(2020, 7, 25), "Peter Tan A.K."),
            (date(2019, 1, 1), "Tan A.K"),
            (date(2018, 11, 30), "Tan Peter"),
            (date(2015, 7, 28), "A.K. Tan Peter"),
        ]
    if additional_addresses is None:
        additional_addresses = [
            (date(2021, 10, 1), "55A, Jalan Ampang, 11-1234, Singapore, 554126"),
            (date(2021, 8, 25), "35B, Gardens Drive, Singapore, 556002"),
            (date(2021, 7, 30), "99, Tuas Link, 15-01, PFS Tower, Singapore, 855132"),
        ]
    accounts = accounts or SAMPLE_ACCOUNTS
    previous_enquiries = previous_enquiries or SAMPLE_PREVIOUS_ENQUIRIES
    # `is None` rather than falsy-`or`, same reasoning as additional_id/names/
    # addresses above: build_persona() needs to be able to pass [] to suppress the
    # sample default/bankruptcy rows -- most applicants have neither on record.
    default_records = SAMPLE_DEFAULT_RECORDS if default_records is None else default_records
    bankruptcy_rows = SAMPLE_BANKRUPTCY_ROWS if bankruptcy_rows is None else bankruptcy_rows
    # `is None` rather than falsy-`or`, same reasoning as additional_id/names/
    # addresses/default_records/bankruptcy_rows above.
    narratives = SAMPLE_NARRATIVES if narratives is None else narratives

    # Page 1
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("V3.0 Enhanced Consumer Credit Report (CONSUMER’S VERSION)", s["title"]))
    story.append(Spacer(1, 4 * mm))
    story.append(_section_bar("Enquiry Details"))
    story.append(_pair_table([
        ("Enquiry Number", enquiry_number),
        ("Enquiry Date", enquiry_date.strftime("%d/%m/%Y")),
        ("Reference", reference),
    ]))
    story.append(Spacer(1, 2 * mm))
    story.append(_section_bar(["Data Provided", "Summary"], col_widths=_scale_widths([80 * mm, 83 * mm])))
    story.append(Table(
        [
            [Paragraph("Name", s["body_bold"]), Paragraph(consumer_name, s["body"]), Paragraph("Date of Earliest known Credit Account", s["body_bold"]), Paragraph(earliest_credit_account_date.strftime("%d/%m/%Y"), s["body"])],
            [Paragraph("ID Type", s["body_bold"]), Paragraph(id_type, s["body"]), Paragraph("Previous Enquiries", s["body_bold"]), Paragraph(str(len(previous_enquiries)), s["body"])],
            [Paragraph("ID Number", s["body_bold"]), Paragraph(id_number, s["body"]), Paragraph("Accounts", s["body_bold"]), Paragraph(str(len(accounts)), s["body"])],
            [Paragraph("Date of Birth", s["body_bold"]), Paragraph(date_of_birth.strftime("%d/%m/%Y"), s["body"]), Paragraph("Defaults", s["body_bold"]), Paragraph(str(len(default_records)), s["body"])],
            [Paragraph("Postal Code", s["body_bold"]), Paragraph(postal_code, s["body"]), Paragraph("Bankruptcy Proceedings", s["body_bold"]), Paragraph(str(len(bankruptcy_rows)), s["body"])],
            [Paragraph("Enquiry Type", s["body_bold"]), Paragraph(enquiry_type, s["body"]), Paragraph("Secured Credit Limit", s["body_bold"]), Paragraph(f"{secured_credit_limit:,.0f}", s["body"])],
            [Paragraph("Product Type", s["body_bold"]), Paragraph(product_type, s["body"]), Paragraph("Unsecured Credit Limit", s["body_bold"]), Paragraph(f"{unsecured_credit_limit:,.0f}", s["body"])],
            [Paragraph("Applicant Type", s["body_bold"]), Paragraph(applicant_type, s["body"]), Paragraph("Exempted Credit Limit", s["body_bold"]), Paragraph(f"{exempted_credit_limit:,.2f}", s["body"])],
            [Paragraph("Debt Management Programme", s["body_bold"]), Paragraph(debt_management_programme, s["body"]), Paragraph("ID Theft", s["body_bold"]), Paragraph(id_theft, s["body"])],
        ],
        colWidths=_scale_widths([35 * mm, 45 * mm, 55 * mm, 28 * mm]),
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F2F4F7")),
            ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F2F4F7")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]),
    ))
    story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Personal Details"))
    story.append(_pair_table([
        ("Surname", surname),
        ("First Name", first_name),
        ("Second Name", ""),
        ("Fore Names", ""),
        ("Unformatted Name", unformatted_name),
        ("ID Type", id_type),
        ("ID Number", id_number),
        ("Date of Birth", date_of_birth.strftime("%d/%m/%Y")),
        ("Gender", gender),
        ("Nationality", nationality),
        ("Marital Status", marital_status),
        ("Address", address),
    ], col_widths=(47 * mm, 88 * mm)))
    story.append(Spacer(1, 2 * mm))

    # additional_id is a single tuple rather than a list, so unlike additional_names/
    # additional_addresses there's no natural "empty" state -- build_persona passes ()
    # (falsy but not None) to skip this section entirely rather than print a fabricated
    # passport number under a real person's real NRIC.
    if additional_id:
        story.append(_section_bar("Additional Identification"))
        story.append(Table(
            [[Paragraph("Date Loaded", s["body_bold"]), Paragraph("ID Type", s["body_bold"]), Paragraph("ID Code", s["body_bold"])] ,
             [Paragraph(additional_id[2].strftime("%d/%m/%Y"), s["body"]), Paragraph(additional_id[0], s["body"]), Paragraph(additional_id[1], s["body"])]] ,
            colWidths=_scale_widths([34 * mm, 35 * mm, 40 * mm]),
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7"))]),
        ))
        story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Additional Names"))
    names_table_data = [[Paragraph("Date Loaded", s["body_bold"]), Paragraph("Name", s["body_bold"])] ]
    names_table_data.extend([[Paragraph(dt.strftime("%d/%m/%Y"), s["body"]), Paragraph(name, s["body"])] for dt, name in additional_names])
    story.append(Table(names_table_data, colWidths=_scale_widths([34 * mm, 80 * mm]), style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7"))])))
    story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Additional Addresses"))
    addr_table_data = [[Paragraph("Date Loaded", s["body_bold"]), Paragraph("Addresses", s["body_bold"])] ]
    addr_table_data.extend([[Paragraph(dt.strftime("%d/%m/%Y"), s["body"]), Paragraph(addr, s["body"])] for dt, addr in additional_addresses])
    story.append(Table(addr_table_data, colWidths=_scale_widths([34 * mm, 80 * mm]), style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7"))])))
    story.append(PageBreak())

    # Page 2
    story.append(_section_bar("Account Status History"))
    account_header = [[Paragraph(x, s["body_bold"]) for x in ["Product Type", "Grantor", "Bank", "Account Type", "Date Opened / Closed", "Overdue Balance", "Last 12 cycles", "Cash Advance/ Bal.Transfer/ Full Payment"]]]
    account_rows = []
    for row in accounts:
        account_rows.append([
            Paragraph(row.product_type, s["body"]),
            Paragraph(row.grantor, s["body"]),
            Paragraph(row.grantor, s["body"]),
            Paragraph(row.account_type, s["body"]),
            Paragraph(f"{row.date_opened}<br/>{row.date_closed}", s["body"]),
            Paragraph(row.overdue_balance, s["body"]),
            Paragraph(row.cycles, s["body"]),
            Paragraph(row.cash_advance, s["body"]),
        ])
    story.append(Table(account_header + account_rows, colWidths=_scale_widths([29 * mm, 18 * mm, 18 * mm, 16 * mm, 25 * mm, 20 * mm, 31 * mm, 31 * mm]), style=TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ])))
    story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Previous Enquiries"))
    prev_table = [[Paragraph(x, s["body_bold"]) for x in ["Date", "Grantor Bank", "Enquiry Type", "Product Type", "Account Type"]]]
    prev_table.extend([[Paragraph(a, s["body"]), Paragraph(b, s["body"]), Paragraph(c, s["body"]), Paragraph(d, s["body"]), Paragraph(e, s["body"])] for a, b, c, d, e in previous_enquiries])
    story.append(Table(prev_table, colWidths=_scale_widths([24 * mm, 28 * mm, 28 * mm, 48 * mm, 20 * mm]), style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 7.2)])))
    story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Default Records"))
    default_table = [[Paragraph(x, s["body_bold"]) for x in ["Product", "Client", "Date Loaded", "Original Amt at load Date", "Balance", "Status"]]]
    default_table.extend([[Paragraph(a, s["body"]), Paragraph(b, s["body"]), Paragraph(c, s["body"]), Paragraph(d, s["body"]), Paragraph(e, s["body"]), Paragraph(f, s["body"])] for a, b, c, d, e, f in default_records])
    story.append(Table(default_table, colWidths=_scale_widths([28 * mm, 25 * mm, 24 * mm, 30 * mm, 18 * mm, 36 * mm]), style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 7.2)])))
    story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Bankruptcy Proceedings"))
    story.append(Paragraph("Bankruptcy data is match to the input of ID type and number.", s["body_small"]))
    bk_table = [[Paragraph(x, s["body_bold"]) for x in ["Bankruptcy Number", "Order Date", "Petition Date", "Original Order Date", "Gazette Date"]]]
    bk_table.extend([[Paragraph(a, s["body"]), Paragraph(b, s["body"]), Paragraph(c, s["body"]), Paragraph(d, s["body"]), Paragraph(e, s["body"])] for a, b, c, d, e in bankruptcy_rows])
    story.append(Table(bk_table, colWidths=_scale_widths([28 * mm, 25 * mm, 25 * mm, 30 * mm, 24 * mm]), style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 7.2)])))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("For status on defaults which are related/ linked to bankruptcy, please refer to Bankruptcy Proceedings on the latest update.", s["body_small"]))
    story.append(PageBreak())

    # Page 3
    story.append(Paragraph("Bureau Score", s["section"]))
    story.append(Paragraph(
        "The Bureau Score is calculated from an algorithm based on information in your current available credit data and is a fluid number which may change from time to time in tandem with changes in your credit information.",
        s["body"],
    ))
    story.append(Paragraph(
        "The Bureau Score or this Credit Report does not draw any conclusions or make credit decisions for financial institutions. The Bureau Score is only one of various pieces of information used by financial institutions in their credit assessment process. The use of the Bureau Score by a financial institution for credit assessment is entirely optional. Each financial institution has its own internal credit score and risk profile for each applicant including the applicant’s financial and demographic information. Credit Bureau (Singapore) Pte Ltd is not involved in any way in the credit decision process of the financial institution.",
        s["body"],
    ))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Example 1: Scored >>", s["body_bold"]))
    story.append(Paragraph(f"Score......................... {score}", s["body"]))
    story.append(Paragraph(f"Risk Grade ....................{risk_grade}", s["body"]))
    story.append(Spacer(1, 1.5 * mm))
    story.append(_grade_bar(risk_grade))
    story.append(Spacer(1, 1.5 * mm))
    story.append(Paragraph("Risk Grade Description ......: Score 1000 - 1723: Prob of Default between &gt;= 3.48%", s["body"]))
    story.append(Paragraph(f"Probability of Default… ...{probability_of_default}", s["body"]))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Example 2: Non-Scored >>", s["body_bold"]))
    story.append(Paragraph("Score......................... Not Applicable", s["body"]))
    story.append(Paragraph("Risk Grade ....................HX", s["body"]))
    story.append(Spacer(1, 1.5 * mm))
    story.append(_hx_box())
    story.append(Spacer(1, 1.5 * mm))
    story.append(Paragraph("Risk Grade Description ........Public records (with or without inquiry/with or without trade)", s["body"]))
    story.append(Paragraph("Probability of Default........ Not Applicable", s["body"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("For more details on the description of various risk grades, please refer to the \"Credit Report Explanation\" section below.", s["body_small"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Explanation of Scorecard Values", s["section"]))
    story.append(Paragraph("Score", s["body_bold"]))
    story.append(Paragraph("The score ranges from 1000 to 2000, where, statistically, within the next 12 months, individuals scoring 1000 have the highest probability of defaulting on a repayment, whereas those who score 2000 have the lowest chance of reaching a delinquency status.", s["body"]))
    story.append(Paragraph("Probability of Default", s["body_bold"]))
    story.append(Paragraph("The probability of the consumer defaulting based on statistics and the population average, within the next 12 months.", s["body"]))
    story.append(Paragraph("Key Contributing Factors associated with this Rating", s["body_bold"]))
    story.append(Paragraph("Delinquency Presence — Greatly Increases Risk\nNot Enough Clean History — Marginally Increases Risk\nAdverse Credit History — Greatly Increases Risk", s["body"]))
    story.append(Paragraph("Factors affecting the Bureau Score", s["body_bold"]))
    story.append(Paragraph("The Bureau Score may be affected by various combinations of key contributing factors such as frequent/recent enquiries for new credit and presence of default or slow payment statuses. Possible ways to improve your Score is to reduce the number of unnecessary new credit applications, as lenders will enquire a credit report on you and these will reflect in the previous enquiries count. Paying your credit bills on a timely basis and avoiding any overdue and default payment behavior will also have a positive impact to your Score.", s["body"]))
    story.append(Paragraph("The above are some of the common contributing factors but the Bureau Score makes use of all available data to formulate the final result and it is not only dependent on any one of these.", s["body"]))
    story.append(PageBreak())

    # Page 4
    story.append(_section_bar("Narratives"))
    nar_table = [[Paragraph(x, s["body_bold"]) for x in ["Date Loaded", "Type", "Narrative"]]]
    nar_table.extend([[Paragraph(dt.strftime("%d/%m/%Y"), s["body"]), Paragraph(t, s["body"]), Paragraph(text, s["body"])] for dt, t, text in narratives])
    story.append(Table(nar_table, colWidths=_scale_widths([24 * mm, 34 * mm, 118 * mm]), style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 7.2), ("VALIGN", (0, 0), (-1, -1), "TOP")])) )
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("OTHER INFORMATION", s["section"]))
    story.append(_section_bar("Aggregated Outstanding Balances"))
    bal_rows, bal_totals = _bucket_balances(accounts)
    current_month = _month_label(enquiry_date)
    outstanding = [["Month", "Product Type", "Grantor Bank", "Secured Balances", "Unsecured Balances", "Interest Bearing Unsecured Balances", "Non-Interest Bearing Exempted Unsecured Balances"]]
    for i, row in enumerate(bal_rows):
        outstanding.append([current_month if i == 0 else "", *row])
    outstanding.append([
        "Total", "", "",
        _money0(bal_totals["secured"]), _money0(bal_totals["unsecured"]),
        _money0(bal_totals["interest"]), _money0(bal_totals["exempted"]),
    ])
    story.append(Table([[Paragraph(cell, s["tiny"]) for cell in row] for row in outstanding], colWidths=_scale_widths([20 * mm, 34 * mm, 22 * mm, 25 * mm, 25 * mm, 30 * mm, 30 * mm]), style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 6.5)])))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Aggregated Outstanding Balances for Preceding 5 Months", s["body_bold"]))
    bal_trend_rows = []
    for i, month in enumerate(_bucket_balance_history(accounts, 4), start=1):
        bal_trend_rows.append([Paragraph(c, s["tiny"]) for c in [
            _month_label(enquiry_date, i), "All", "All",
            _money0(month["secured"]), _money0(month["unsecured"]),
            _money0(month["interest"]), _money0(month["exempted"]),
        ]])
    story.append(Table(
        bal_trend_rows,
        colWidths=_scale_widths([22 * mm, 22 * mm, 22 * mm, 28 * mm, 22 * mm, 24 * mm, 24 * mm]),
        style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC"))]),
    ))
    story.append(Spacer(1, 2 * mm))
    story.append(_section_bar("Aggregated Monthly Instalment"))
    inst_rows, inst_totals = _bucket_instalments(accounts)
    monthly = [["Month", "Product Type", "Grantor Bank", "Account Type**", "Property***", "Non-Property", "Unsecured", "Exempted Unsecured"]]
    for i, row in enumerate(inst_rows):
        monthly.append([f"{current_month}*" if i == 0 else "", *row])
    monthly.append([
        "Total", "", "", "",
        _money0(inst_totals["property"]), _money0(inst_totals["non_property"]),
        _money0(inst_totals["unsecured"]), _money0(inst_totals["exempted"]),
    ])
    story.append(Table([[Paragraph(cell, s["tiny"]) for cell in row] for row in monthly], colWidths=_scale_widths([18 * mm, 33 * mm, 18 * mm, 17 * mm, 22 * mm, 18 * mm, 18 * mm, 20 * mm]), style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 6.3)])))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Aggregated Monthly Instalments for Preceding 5 Months", s["body_bold"]))
    # Instalments don't really change month to month absent a new/closed account or
    # a rate change, so the preceding months are held flat at the current totals.
    inst_trend_rows = []
    for i in range(1, 5):
        inst_trend_rows.append([Paragraph(c, s["tiny"]) for c in [
            _month_label(enquiry_date, i), "All", "All", "-",
            _money0(inst_totals["property"]), _money0(inst_totals["non_property"]),
            _money0(inst_totals["unsecured"]), _money0(inst_totals["exempted"]),
        ]])
    story.append(Table(
        inst_trend_rows,
        colWidths=_scale_widths([22 * mm, 22 * mm, 22 * mm, 18 * mm, 23 * mm, 18 * mm, 18 * mm, 18 * mm]),
        style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC"))]),
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("*The Aggregated Monthly Instalment Amounts for the latest month only include amounts uploaded by members of Credit Bureau Singapore to date.", s["tiny"]))
    story.append(Paragraph("**Monthly Instalment Amounts for Joint Accounts are reported as a full instalment amount due for a facility and does not reflect individually shared amounts for each joint borrower.", s["tiny"]))
    story.append(Paragraph("***The Aggregated Monthly Instalment Amounts reflected do not include the amount for Secured Bridging Loan with a tenure of six months or less.", s["tiny"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Disclaimer & Indemnity", s["section"]))
    story.append(Paragraph("The information contained in this Report has been collated from and/or provided to Credit Bureau (Singapore) Pte Ltd by various third parties and does not represent the opinion of Credit Bureau (Singapore) Pte Ltd in any way. No liability (in tort contract or otherwise howsoever) attaches to us with respect to the collation or supplying of the information, the Bureau Score or any use made of it whether in relation to its accuracy or completeness or any other matter whatsoever. The information is supplied on a confidential basis to you and not for the use of any other party save for any person on whose behalf you have sought the information. We are entitled to indemnity from you against any claims or loss made or sustained by us in consequence of the provision of the information sought to you.", s["tiny"]))
    story.append(Paragraph("END OF REPORT", s["body_bold"]))

    # Platypus centers any Table narrower than the frame by default, which pulls
    # several of the sample template's tables (Previous Enquiries, Bankruptcy
    # Proceedings, the "Preceding 5 Months" mini-tables, ...) out of alignment with
    # the full-width section bars above them. Left-align every table in one pass
    # rather than remembering hAlign="LEFT" at each of the many call sites above.
    for flowable in story:
        if isinstance(flowable, Table):
            flowable.hAlign = "LEFT"

    _build_pdf(output_path, story)
    return {
        "output_path": output_path,
        "consumer_name": consumer_name,
        "score": score,
        "risk_grade": risk_grade,
    }


# --------------------------------------------------------------------------
# Mastersheet-driven persona — one Credit Reports row + its Case Registry /
# Individuals context in, generate_credit_bureau_report() kwargs out.
# --------------------------------------------------------------------------

_PATRONYMIC_RE = re.compile(r"\b(bin|binte|binti|s/o|d/o)\b", re.I)


def _split_name(name: str) -> tuple[str, str, str]:
    """Best-effort Surname/First Name split for the Personal Details block.

    The mastersheet's applicant names mix several naming conventions: Chinese
    surname-first ("Tan Wei Ming"), an English given name in front of a Chinese
    surname-first name ("David Lee Wei Kiat"), and Malay/Indian patronymics
    ("Muhammad Faiz bin Ahmad", "Priya d/o Manoharan") that don't decompose into a
    Western surname/given pair at all. Patronymic names, and anything that isn't a
    clean 2- or 3-token name, go into Unformatted Name instead of a wrong split.
    """
    if _PATRONYMIC_RE.search(name):
        return "", "", name
    tokens = name.split()
    if len(tokens) == 2:
        return tokens[0], tokens[1], ""
    if len(tokens) == 3:
        return tokens[0], f"{tokens[1]} {tokens[2]}", ""
    if len(tokens) == 4:
        # "David Lee Wei Kiat" -> surname "Lee" (the token right before the two-part
        # Chinese given name), first name "David Wei Kiat".
        return tokens[1], f"{tokens[0]} {tokens[2]} {tokens[3]}", ""
    return "", "", name


def _rows(ws) -> list[dict]:
    header = [c.value for c in ws[1]]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(row):
            continue
        out.append(dict(zip(header, row)))
    return out


def build_persona(case: dict, people: list[dict]) -> dict:
    """Identity/enquiry-linkage kwargs for generate_credit_bureau_report(), derived
    from one Case Registry row and its matching Individuals rows. The consumer is
    always the Director & UBO -- the same applicant every other generator treats as
    the person behind the case."""
    case_id = str(case["Case ID"])
    rng = seeded_rng(f"{case_id}|credit-report")
    bank_map = _pick_grantor_banks(rng)

    director = next(
        (p for p in people if "director" in str(p.get("Role") or "").lower()),
        people[0] if people else {},
    )

    consumer_name = str(
        case.get("Applicant Name (Director & UBO)") or director.get("Name (auto/fill)") or ""
    ).strip()
    id_number = str(
        case.get("Applicant NRIC No.") or director.get("NRIC/Passport No. (auto/fill)") or ""
    ).strip()
    dob = (
        as_date(case.get("Applicant DOB (Director & UBO)"))
        or as_date(director.get("Date of Birth (auto/fill)"))
        or date(1980, 1, 1)
    )
    nationality = str(director.get("Nationality") or "Singaporean")
    address = str(director.get("Residential Address") or case.get("Registered Address") or "")
    enquiry_date = as_date(case.get("Application Date")) or date.today()

    id_type = "NRIC" if "singapor" in nationality.lower() else "Passport"
    surname, first_name, unformatted_name = _split_name(consumer_name)

    # Same seeded pick nric_generator.py's build_persona() uses for this individual's
    # NRIC card -- previously hardcoded "Male" here regardless of what the NRIC showed.
    individual_id = str(director.get("Individual ID") or "")
    gender = "Male" if derive_gender(individual_id) == "M" else "Female"

    postal_match = re.search(r"(\d{6})\s*$", address)
    postal_code = postal_match.group(1) if postal_match else ""

    marital_status = rng.choice(["Single", "Married", "Divorced", "Widowed"])
    score, risk_grade, probability_of_default = SCORE_BANDS[rng.randrange(len(SCORE_BANDS))]

    # Account and default dates follow this applicant's life; previous enquiries come in the
    # months just before this one.
    retime = _history_retimer(dob, enquiry_date)
    enquiry_shift = ((enquiry_date - timedelta(days=60)) - _TEMPLATE_LAST_ENQUIRY).days
    # The earliest-account date used to be drawn here on its own; it's now the earliest account
    # in the table (set below). The draws stay so the rest of the report doesn't shift.
    earliest_year = max(dob.year + 18, enquiry_date.year - rng.randint(5, 20))
    if earliest_year >= enquiry_date.year:
        earliest_year = enquiry_date.year - 1
    earliest_credit_account_date = date(earliest_year, rng.randint(1, 12), rng.randint(1, 28))

    # Drawn in the same order as before (enquiry number, limits, accounts) so every
    # case's seeded values stay put; the limits are then raised to cover the balances.
    enquiry_number = f"{rng.randint(0, 999_999_999):09d}"
    secured_credit_limit = SECURED_LIMITS[rng.randrange(len(SECURED_LIMITS))]
    unsecured_credit_limit = UNSECURED_LIMITS[rng.randrange(len(UNSECURED_LIMITS))]
    exempted_credit_limit = EXEMPTED_LIMITS[rng.randrange(len(EXEMPTED_LIMITS))]
    # Same products/narrative text every case (there's no mastersheet source to
    # vary them from), but the amounts/dates get a small per-case jitter and the
    # "Bank A"/"Bank B"/"Bank C" placeholders are swapped for 3 real bank names
    # (also seeded) so 36 cases don't all show byte-identical credit histories.
    accounts = _jitter_accounts(_pick_accounts(rng), rng, bank_map, retime)
    _, balances = _bucket_balances(accounts)

    persona = dict(
        enquiry_number=enquiry_number,
        enquiry_date=enquiry_date,
        reference=f"{id_number}_{enquiry_date:%d%m%y}",
        consumer_name=consumer_name,
        id_type=id_type,
        id_number=id_number,
        date_of_birth=dob,
        postal_code=postal_code,
        enquiry_type="New Application",
        product_type="Self",
        applicant_type="Primary",
        earliest_credit_account_date=earliest_credit_account_date,
        secured_credit_limit=_limit_covering(secured_credit_limit, balances["secured"], SECURED_LIMITS, 250_000),
        unsecured_credit_limit=_limit_covering(
            unsecured_credit_limit, balances["unsecured"] + balances["interest"], UNSECURED_LIMITS, 5_000
        ),
        exempted_credit_limit=exempted_credit_limit,
        debt_management_programme="N",
        id_theft="N",
        surname=surname,
        first_name=first_name,
        unformatted_name=unformatted_name,
        nationality=nationality,
        marital_status=marital_status,
        address=address,
        gender=gender,
        # No mastersheet source for prior aliases/addresses/IDs -- suppress the
        # sample ones rather than show fake history under a real NRIC.
        additional_id=(),
        additional_names=[],
        additional_addresses=[],
        score=score,
        risk_grade=risk_grade,
        probability_of_default=probability_of_default,
        accounts=accounts,
        previous_enquiries=_jitter_enquiries(SAMPLE_PREVIOUS_ENQUIRIES, rng, bank_map, enquiry_shift),
        # No mastersheet source for a default/bankruptcy/narrative history, and most
        # applicants have none of these -- each is only included for a small, seeded
        # per-case chance (independent rolls, so a case could show any subset of the
        # three) rather than showing the same "Negotiated Settlement" / bankruptcy
        # order / distress narratives on every single case.
        default_records=(
            _jitter_defaults(SAMPLE_DEFAULT_RECORDS, rng, bank_map, retime) if rng.random() < 0.15 else []
        ),
        bankruptcy_rows=(
            _jitter_bankruptcy(SAMPLE_BANKRUPTCY_ROWS, rng) if rng.random() < 0.08 else []
        ),
        narratives=(
            _jitter_narratives(SAMPLE_NARRATIVES, rng, bank_map) if rng.random() < 0.15 else []
        ),
    )
    # The summary's earliest known account is the earliest one the table lists.
    persona["earliest_credit_account_date"] = min(
        datetime.strptime(a.date_opened, "%d/%m/%Y").date() for a in persona["accounts"]
    )
    return persona


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--cases", nargs="*", help="case IDs to include; default is all")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    wb = openpyxl.load_workbook(args.xlsx, data_only=True)
    registry = {str(r["Case ID"]): r for r in _rows(wb["Case Registry"])}
    individuals_by_case: dict[str, list[dict]] = {}
    for r in _rows(wb["Individuals"]):
        individuals_by_case.setdefault(str(r["Case ID"]), []).append(r)

    count = 0
    for cr in _rows(wb["Credit Reports"]):
        case_id = str(cr["Case ID"])
        if args.cases and case_id not in args.cases:
            continue
        case = registry.get(case_id)
        if not case or not case.get("Company Name"):
            continue  # placeholder row, not yet filled in

        persona = build_persona(case, individuals_by_case.get(case_id, []))
        out_name = cr.get("Filename") or f"{case_id}_CreditReport.pdf"
        result = generate_credit_bureau_report(str(outdir / out_name), **persona)
        count += 1
        print(f"{case_id}: {result['consumer_name']} -> {result['output_path']}")

    print(f"wrote {count} credit report(s)")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    else:
        out_dir = Path.cwd() / "output"
        out_dir.mkdir(parents=True, exist_ok=True)

        result = generate_credit_bureau_report(
            str(out_dir / "credit_bureau_report.pdf"),
            enquiry_number="000054641",
            enquiry_date=date(2025, 6, 21),
            reference="S1234567D_210625",
            consumer_name="Peter Tan",
            id_type="NRIC",
            id_number="S1234567D",
            date_of_birth=date(1960, 1, 1),
            postal_code="552665",
            enquiry_type="New Application",
            product_type="Self",
            applicant_type="Primary",
            earliest_credit_account_date=date(2004, 3, 22),
            secured_credit_limit=2246000,
            unsecured_credit_limit=20000,
            exempted_credit_limit=0.00,
            debt_management_programme="Y",
            id_theft="Y",
            surname="Tan",
            first_name="Peter",
            nationality="Singapore",
            marital_status="Married",
            address="122, Sample Street Ave 8, 22-1555, Singapore, 552665",
        )
        print("Credit report:", result)
