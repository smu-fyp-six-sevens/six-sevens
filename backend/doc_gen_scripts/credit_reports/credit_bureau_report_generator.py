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

styles = getSampleStyleSheet()
FONT = "Helvetica"

# Logo supplied to more closely match the real CBS template (assets/CBS_Logo.png).
LOGO_PATH = Path(__file__).resolve().parent / "assets" / "CBS_Logo.png"
LOGO_ASPECT = 785 / 1893  # height / width of assets/CBS_Logo.png, for scaling by width
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


# Template rows for the credit-history blocks (Account Status History, Previous
# Enquiries, Default Records, Bankruptcy Proceedings, Narratives). No mastersheet
# column backs any of these, so build_persona() doesn't invent new ones -- it takes
# this same template and runs it through _jitter_* (below) so each case's numbers and
# dates move a little without the banks/products/narrative text changing case to case.
SAMPLE_ACCOUNTS = [
    AccountRow("Unsecured Credit Card", "Bank A", "Single", "22/03/2004", "05/03/2024", "6,500.00", "WHHHDDDDCBA*", "NNNNNNNNNNN-"),
    AccountRow("HDB Loan", "Bank A", "Joint", "10/10/2014", "", "", "AAAAAABAAAAA"),
    AccountRow("Executive Condominium Purchase", "Bank B", "Single", "01/01/2021", "", "", "ABBBBBBBAAAA"),
    AccountRow("Unsecured Personal Loan", "Bank B", "Single", "01/09/2010", "", "", "AAAAAAAAAAAB"),
    AccountRow("Unsecured Credit Card", "Bank B", "Single", "22/03/2004", "", "", "AAAAAAAAAAA*", "NNNNNNNNNNN-",),
    AccountRow("Private Residential Purchase", "Bank C", "Single", "30/11/2004", "20/07/2024", "90,000.00", "RHDDDCBAACBA"),
    AccountRow("Mortgage Restructured Loan", "Bank C", "Single", "20/07/2024", "", "", "AAAAAAAAAAAA"),
]
SAMPLE_PREVIOUS_ENQUIRIES = [
    ("01/01/2025", "Bank B", "New Application", "Executive Condominium Purchase", "Single"),
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


def _jitter_accounts(accounts: list[AccountRow], rng) -> list[AccountRow]:
    jittered = []
    for row in accounts:
        shift = rng.randint(-30, 30)  # keeps each row's open/close gap intact
        jittered.append(AccountRow(
            product_type=row.product_type,
            grantor=row.grantor,
            account_type=row.account_type,
            date_opened=_jitter_date(row.date_opened, rng, offset_days=shift),
            date_closed=_jitter_date(row.date_closed, rng, offset_days=shift),
            overdue_balance=_jitter_amount(row.overdue_balance, rng),
            cycles=row.cycles,
            cash_advance=row.cash_advance,
        ))
    return jittered


def _jitter_enquiries(rows: list[tuple], rng) -> list[tuple]:
    return [(_jitter_date(a, rng), b, c, d, e) for a, b, c, d, e in rows]


def _jitter_defaults(rows: list[tuple], rng) -> list[tuple]:
    return [
        (a, b, _jitter_date(c, rng), _jitter_amount(d, rng), _jitter_amount(e, rng), f)
        for a, b, c, d, e, f in rows
    ]


def _jitter_bankruptcy(rows: list[tuple], rng) -> list[tuple]:
    shift = rng.randint(-30, 30)  # one shared shift keeps the stages in sequence
    return [
        (a, _jitter_date(b, rng, offset_days=shift), _jitter_date(c, rng, offset_days=shift),
         _jitter_date(d, rng, offset_days=shift), e)
        for a, b, c, d, e in rows
    ]


def _jitter_narratives(rows: list[tuple], rng, max_days: int = 30) -> list[tuple]:
    return [(dt + timedelta(days=rng.randint(-max_days, max_days)), t, text) for dt, t, text in rows]


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


def _section_bar(text: str | list[str], col_widths: list[float] | None = None) -> Table:
    """Full-width navy header bar with white bold text -- the CBS template's section
    heading style (Enquiry Details, Personal Details, Account Status History, ...).
    Pass a list of 2+ strings for a bar split into that many labelled columns (e.g.
    the "Data Provided" / "Summary" two-up header)."""
    s = _styles()
    cells = [text] if isinstance(text, str) else text
    widths = col_widths or [163 * mm / len(cells)] * len(cells)
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
    return Table(
        [[Paragraph(k, s["body_bold"]), Paragraph(v, s["body"])] for k, v in pairs],
        colWidths=list(col_widths),
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
    default_records = default_records or SAMPLE_DEFAULT_RECORDS
    bankruptcy_rows = bankruptcy_rows or SAMPLE_BANKRUPTCY_ROWS
    narratives = narratives or SAMPLE_NARRATIVES

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
    story.append(_section_bar(["Data Provided", "Summary"], col_widths=[80 * mm, 83 * mm]))
    story.append(Table(
        [
            [Paragraph("Name", s["body_bold"]), Paragraph(consumer_name, s["body"]), Paragraph("Date of Earliest known Credit Account", s["body_bold"]), Paragraph(earliest_credit_account_date.strftime("%d/%m/%Y"), s["body"])],
            [Paragraph("ID Type", s["body_bold"]), Paragraph(id_type, s["body"]), Paragraph("Previous Enquiries", s["body_bold"]), Paragraph("3", s["body"])],
            [Paragraph("ID Number", s["body_bold"]), Paragraph(id_number, s["body"]), Paragraph("Accounts", s["body_bold"]), Paragraph(str(len(accounts)), s["body"])],
            [Paragraph("Date of Birth", s["body_bold"]), Paragraph(date_of_birth.strftime("%d/%m/%Y"), s["body"]), Paragraph("Defaults", s["body_bold"]), Paragraph(str(len(default_records)), s["body"])],
            [Paragraph("Postal Code", s["body_bold"]), Paragraph(postal_code, s["body"]), Paragraph("Bankruptcy Proceedings", s["body_bold"]), Paragraph("2", s["body"])],
            [Paragraph("Enquiry Type", s["body_bold"]), Paragraph(enquiry_type, s["body"]), Paragraph("Secured Credit Limit", s["body_bold"]), Paragraph(f"{secured_credit_limit:,.0f}", s["body"])],
            [Paragraph("Product Type", s["body_bold"]), Paragraph(product_type, s["body"]), Paragraph("Unsecured Credit Limit", s["body_bold"]), Paragraph(f"{unsecured_credit_limit:,.0f}", s["body"])],
            [Paragraph("Applicant Type", s["body_bold"]), Paragraph(applicant_type, s["body"]), Paragraph("Exempted Credit Limit", s["body_bold"]), Paragraph(f"{exempted_credit_limit:,.2f}", s["body"])],
            [Paragraph("Debt Management Programme", s["body_bold"]), Paragraph(debt_management_programme, s["body"]), Paragraph("ID Theft", s["body_bold"]), Paragraph(id_theft, s["body"])],
        ],
        colWidths=[35 * mm, 45 * mm, 55 * mm, 28 * mm],
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
        ("Gender", "Male"),
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
            colWidths=[34 * mm, 35 * mm, 40 * mm],
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7"))]),
        ))
        story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Additional Names"))
    names_table_data = [[Paragraph("Date Loaded", s["body_bold"]), Paragraph("Name", s["body_bold"])] ]
    names_table_data.extend([[Paragraph(dt.strftime("%d/%m/%Y"), s["body"]), Paragraph(name, s["body"])] for dt, name in additional_names])
    story.append(Table(names_table_data, colWidths=[34 * mm, 80 * mm], style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7"))])))
    story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Additional Addresses"))
    addr_table_data = [[Paragraph("Date Loaded", s["body_bold"]), Paragraph("Addresses", s["body_bold"])] ]
    addr_table_data.extend([[Paragraph(dt.strftime("%d/%m/%Y"), s["body"]), Paragraph(addr, s["body"])] for dt, addr in additional_addresses])
    story.append(Table(addr_table_data, colWidths=[34 * mm, 80 * mm], style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7"))])))
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
    story.append(Table(account_header + account_rows, colWidths=[30 * mm, 18 * mm, 18 * mm, 18 * mm, 26 * mm, 20 * mm, 28 * mm, 30 * mm], style=TableStyle([
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
    story.append(Table(prev_table, colWidths=[24 * mm, 28 * mm, 28 * mm, 48 * mm, 20 * mm], style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 7.2)])))
    story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Default Records"))
    default_table = [[Paragraph(x, s["body_bold"]) for x in ["Product", "Client", "Date Loaded", "Original Amt at load Date", "Balance", "Status"]]]
    default_table.extend([[Paragraph(a, s["body"]), Paragraph(b, s["body"]), Paragraph(c, s["body"]), Paragraph(d, s["body"]), Paragraph(e, s["body"]), Paragraph(f, s["body"])] for a, b, c, d, e, f in default_records])
    story.append(Table(default_table, colWidths=[28 * mm, 25 * mm, 24 * mm, 30 * mm, 18 * mm, 36 * mm], style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 7.2)])))
    story.append(Spacer(1, 2 * mm))

    story.append(_section_bar("Bankruptcy Proceedings"))
    story.append(Paragraph("Bankruptcy data is match to the input of ID type and number.", s["body_small"]))
    bk_table = [[Paragraph(x, s["body_bold"]) for x in ["Bankruptcy Number", "Order Date", "Petition Date", "Original Order Date", "Gazette Date"]]]
    bk_table.extend([[Paragraph(a, s["body"]), Paragraph(b, s["body"]), Paragraph(c, s["body"]), Paragraph(d, s["body"]), Paragraph(e, s["body"])] for a, b, c, d, e in bankruptcy_rows])
    story.append(Table(bk_table, colWidths=[28 * mm, 25 * mm, 25 * mm, 30 * mm, 24 * mm], style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 7.2)])))
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
    story.append(Paragraph(f"Example 1: Scored >> Score. ........................ {score}   Risk Grade .................... {risk_grade}", s["body_bold"]))
    story.append(Paragraph(f"Risk Grade Description ...... : Score 1000 - 1723: Prob of Default between >= 3.48%   Probability of Default… ... {probability_of_default}", s["body"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Example 2: Non-Scored >>", s["body_bold"]))
    story.append(Paragraph("Score. ........................ Not Applicable", s["body"]))
    story.append(Paragraph("Risk Grade .................... HX", s["body"]))
    story.append(Paragraph("Risk Grade Description ........ Public records (with or without inquiry/with or without trade)", s["body"]))
    story.append(Paragraph("Probability of Default ........ Not Applicable", s["body"]))
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
    story.append(Table(nar_table, colWidths=[24 * mm, 34 * mm, 118 * mm], style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 7.2), ("VALIGN", (0, 0), (-1, -1), "TOP")])) )
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("OTHER INFORMATION", s["section"]))
    story.append(_section_bar("Aggregated Outstanding Balances"))
    outstanding = [
        ["Month", "Product Type", "Grantor Bank", "Secured Balances", "Unsecured Balances", "Interest Bearing Unsecured Balances", "Non-Interest Bearing Exempted Unsecured Balances"],
        ["May 2025", "HDB Loan", "Bank A", "157,000.00", "0.00", "0.00", "0.00"],
        ["", "Executive Condominium Purchase", "Bank B", "1,700,250.00", "0.00", "0.00", "0.00"],
        ["", "Unsecured Personal Loan", "Bank B", "0.00", "7,000.00", "0.00", "0.00"],
        ["", "Unsecured Credit Card", "Bank B", "0.00", "0.00", "2,500.00", "0.00"],
        ["", "Mortgage Restructured Loan", "Bank C", "75,000.00", "0.00", "0.00", "0.00"],
        ["Total", "", "", "1,932,250.00", "7,000.00", "2,500.00", "0.00"],
    ]
    story.append(Table([[Paragraph(cell, s["tiny"]) for cell in row] for row in outstanding], colWidths=[20 * mm, 34 * mm, 22 * mm, 25 * mm, 25 * mm, 30 * mm, 30 * mm], style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 6.5)])))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph("Aggregated Outstanding Balances for Preceding 5 Months", s["body_bold"]))
    story.append(Table(
        [[Paragraph(c, s["tiny"]) for c in ["April 2025", "All", "All", "1,938,550.00", "7,300.00", "1,680.00", "0.00"]],
         [Paragraph(c, s["tiny"]) for c in ["March 2025", "All", "All", "1,944,850.00", "7,600.00", "1,700.00", "0.00"]],
         [Paragraph(c, s["tiny"]) for c in ["February 2025", "All", "All", "1,951,150.00", "7,900.00", "2,400.00", "0.00"]],
         [Paragraph(c, s["tiny"]) for c in ["January 2025", "All", "All", "1,957,450.00", "8,200.00", "1,100.00", "0.00"]],
         [Paragraph(c, s["tiny"]) for c in ["December 2024", "All", "All", "1,963,750.00", "8,500.00", "700.00", "0.00"]]],
        colWidths=[22 * mm, 22 * mm, 22 * mm, 28 * mm, 22 * mm, 24 * mm, 24 * mm],
        style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC"))]),
    ))
    story.append(Spacer(1, 2 * mm))
    story.append(_section_bar("Aggregated Monthly Instalment"))
    monthly = [
        ["Month", "Product Type", "Grantor Bank", "Account Type**", "Property***", "Non-Property", "Unsecured", "Exempted Unsecured"],
        ["May 2025*", "HDB Loan", "Bank A", "Joint", "2,800.00", "0.00", "0.00", "0.00"],
        ["", "Executive Condominium Purchase", "Bank B", "Single", "2,500.00", "0.00", "0.00", "0.00"],
        ["", "Unsecured Personal Loan", "Bank B", "Single", "0.00", "0.00", "300.00", "0.00"],
        ["", "Unsecured Credit Card", "Bank B", "Single", "0.00", "0.00", "100.00", "0.00"],
        ["", "Mortgage Restructured Loan", "Bank C", "Single", "1,000.00", "0.00", "0.00", "0.00"],
        ["Total", "", "", "", "6,300.00", "0.00", "400.00", "0.00"],
    ]
    story.append(Table([[Paragraph(cell, s["tiny"]) for cell in row] for row in monthly], colWidths=[18 * mm, 33 * mm, 18 * mm, 17 * mm, 22 * mm, 18 * mm, 18 * mm, 20 * mm], style=TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CCCCCC")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F4F7")), ("FONTSIZE", (0, 0), (-1, -1), 6.3)])))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Aggregated Monthly Instalments for Preceding 5 Months", s["body_bold"]))
    story.append(Table(
        [[Paragraph(c, s["tiny"]) for c in ["April 2025", "All", "All", "-", "6,300.00", "0.00", "380.00", "0.00"]],
         [Paragraph(c, s["tiny"]) for c in ["March 2025", "All", "All", "-", "6,300.00", "0.00", "415.00", "0.00"]],
         [Paragraph(c, s["tiny"]) for c in ["February 2025", "All", "All", "-", "6,300.00", "0.00", "476.00", "0.00"]],
         [Paragraph(c, s["tiny"]) for c in ["January 2025", "All", "All", "-", "6,300.00", "0.00", "350.00", "0.00"]],
         [Paragraph(c, s["tiny"]) for c in ["December 2024", "All", "All", "-", "6,300.00", "0.00", "350.00", "0.00"]]],
        colWidths=[22 * mm, 22 * mm, 22 * mm, 18 * mm, 23 * mm, 18 * mm, 18 * mm, 18 * mm],
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

    postal_match = re.search(r"(\d{6})\s*$", address)
    postal_code = postal_match.group(1) if postal_match else ""

    marital_status = rng.choice(["Single", "Married", "Divorced", "Widowed"])
    score, risk_grade, probability_of_default = SCORE_BANDS[rng.randrange(len(SCORE_BANDS))]

    earliest_year = max(dob.year + 18, enquiry_date.year - rng.randint(5, 20))
    if earliest_year >= enquiry_date.year:
        earliest_year = enquiry_date.year - 1
    earliest_credit_account_date = date(earliest_year, rng.randint(1, 12), rng.randint(1, 28))

    return dict(
        enquiry_number=f"{rng.randint(0, 999_999_999):09d}",
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
        secured_credit_limit=SECURED_LIMITS[rng.randrange(len(SECURED_LIMITS))],
        unsecured_credit_limit=UNSECURED_LIMITS[rng.randrange(len(UNSECURED_LIMITS))],
        exempted_credit_limit=EXEMPTED_LIMITS[rng.randrange(len(EXEMPTED_LIMITS))],
        debt_management_programme="N",
        id_theft="N",
        surname=surname,
        first_name=first_name,
        unformatted_name=unformatted_name,
        nationality=nationality,
        marital_status=marital_status,
        address=address,
        # No mastersheet source for prior aliases/addresses/IDs -- suppress the
        # sample ones rather than show fake history under a real NRIC.
        additional_id=(),
        additional_names=[],
        additional_addresses=[],
        score=score,
        risk_grade=risk_grade,
        probability_of_default=probability_of_default,
        # Same banks/products/narrative text every case (there's no mastersheet
        # source to vary them from), but the amounts and dates get a small per-case
        # jitter so 36 cases don't all show byte-identical credit histories.
        accounts=_jitter_accounts(SAMPLE_ACCOUNTS, rng),
        previous_enquiries=_jitter_enquiries(SAMPLE_PREVIOUS_ENQUIRIES, rng),
        default_records=_jitter_defaults(SAMPLE_DEFAULT_RECORDS, rng),
        bankruptcy_rows=_jitter_bankruptcy(SAMPLE_BANKRUPTCY_ROWS, rng),
        narratives=_jitter_narratives(SAMPLE_NARRATIVES, rng),
    )


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
