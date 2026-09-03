"""
balance_sheet_income_statement_generator.py — generate a single combined
Source of Wealth financial-statements PDF (Statement of Comprehensive Income,
then Statement of Financial Position, then Notes to the Financial Statements)
per case.

The two statements used to be generated as separate PDFs (balance_sheet/ and
income_statement/), each hardcoding one fixed set of figures. This module
merges them into one flowing document and drives the figures from a seeded
RNG so every case gets a plausible, internally-consistent, but distinct set
of accounts: which optional line items appear (intangible assets, inventories,
finance leases, extra opex/asset/liability lines) and their amounts both vary
per case, while the two years' balance sheets are still guaranteed to balance
and the retained-earnings roll-forward and profit figures tie exactly between
the income statement and the balance sheet.

Notes are numbered by order of first appearance in the combined document
(via NoteRegistry) rather than hardcoded, so numbering stays correct no
matter which optional line items a given case happens to include — a note
referenced from both statements (e.g. property, plant and equipment) keeps
a single number.

Mastersheet reality check: the "Source of Wealth Docs" tab still lists two
rows per case (Document Type "Balance Sheet" and "Income Statement", each
with its own Filename). Since this script now produces one combined PDF,
generate() writes that same PDF out under both registered filenames so the
existing two-row structure keeps working without a mastersheet edit. If you
want a cleaner mastersheet, collapse those two rows into one per case (e.g.
Document Type "Financial Statements", Filename "C001_SourceWealth_FS.pdf").

To generate:

cd backend/doc_gen_scripts/corp_source_of_wealth
python balance_sheet_income_statement_generator.py ../v9_document_mastersheet.xlsx --outdir out/
python balance_sheet_income_statement_generator.py ../v9_document_mastersheet.xlsx --outdir out/ --cases C001
python balance_sheet_income_statement_generator.py ../v9_document_mastersheet.xlsx --outdir out/ --no-synthetic-tag
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openpyxl
from case_facts import as_date, company_facts, seeded_rng
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
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

styles = getSampleStyleSheet()
FONT = "Helvetica"
SYNTHETIC_TAG = "SYNTHETIC-TEST-DATA"

COL_WIDTHS = [92 * mm, 12 * mm, 30 * mm, 30 * mm]


# --------------------------------------------------------------------------
# Account variation pools
# --------------------------------------------------------------------------

OPEX_MANDATORY = [
    "Staff costs",
    "Directors' remuneration",
    "Rental expense",
    "Depreciation of property, plant and equipment",
]
OPEX_POOL = [
    "Utilities",
    "Repairs and maintenance",
    "Professional fees",
    "Marketing and advertising",
    "Transportation",
    "Insurance",
    "Entertainment expenses",
    "Audit fees",
    "IT and software expenses",
    "Training and development expenses",
    "Foreign exchange loss",
    "Bad debts written off",
    "Donations",
]

EXTRA_ASSET_POOL = ["Fixed deposits", "Amount due from related corporations", "Deposits placed"]
EXTRA_LIAB_POOL = ["Amount due to director", "Contract liabilities"]

SHARE_CAPITAL_POOL = [100_000, 150_000, 200_000, 250_000, 500_000, 750_000, 1_000_000]

# Financial year end: 31 Dec of the year before the application, or 31 Mar / 30 Jun of
# the application year itself, picked at random per case.
def _choose_period_end(rng, application_date: date) -> date:
    options = [
        date(application_date.year - 1, 12, 31),
        date(application_date.year, 3, 31),
        date(application_date.year, 6, 30),
    ]
    return rng.choice(options)

NOTE_DEFS: dict[str, tuple[str, str, str]] = {
    "Revenue": ("revenue", "REVENUE",
                "Revenue represents the invoiced value of goods sold, net of goods and "
                "services tax and trade discounts."),
    "Other income": ("other_income", "OTHER INCOME",
                      "Other income comprises interest income and sundry income."),
    "Staff costs": ("staff_costs", "STAFF COSTS",
                     "Staff costs include salaries, bonuses, and the Company's contributions "
                     "to the Central Provident Fund on behalf of employees."),
    "Depreciation of property, plant and equipment": ("ppe", "PROPERTY, PLANT AND EQUIPMENT",
        "Property, plant and equipment comprise office equipment, furniture and fittings, "
        "renovation, and motor vehicles used in the Company's trading operations, stated at "
        "cost less accumulated depreciation and accumulated impairment losses. Depreciation "
        "is computed on a straight-line basis over the estimated useful lives of the assets."),
    "Property, plant and equipment": ("ppe", "PROPERTY, PLANT AND EQUIPMENT",
        "Property, plant and equipment comprise office equipment, furniture and fittings, "
        "renovation, and motor vehicles used in the Company's trading operations, stated at "
        "cost less accumulated depreciation and accumulated impairment losses. Depreciation "
        "is computed on a straight-line basis over the estimated useful lives of the assets."),
    "Finance costs": ("finance_costs", "FINANCE COSTS",
                       "Finance costs comprise interest expense on bank borrowings and finance "
                       "lease liabilities."),
    "Tax expense": ("tax_expense", "TAX EXPENSE",
        "Tax expense represents the estimated Singapore corporate income tax payable, "
        "computed at the prevailing statutory tax rate on the Company's chargeable income "
        "for the year."),
    "Intangible assets": ("intangible", "INTANGIBLE ASSETS",
        "Intangible assets comprise computer software licences, amortised on a straight-line "
        "basis over their estimated useful lives."),
    "Inventories": ("inventories", "INVENTORIES",
        "Inventories are stated at the lower of cost and net realisable value. Cost is "
        "determined on a first-in-first-out basis."),
    "Trade receivables": ("trade_receivables", "TRADE RECEIVABLES",
        "Trade receivables are amounts due from customers in the ordinary course of "
        "business, stated net of allowance for expected credit losses."),
    "Cash and bank balances": ("cash", "CASH AND BANK BALANCES",
        "Cash and bank balances comprise cash on hand and balances with banks, including "
        "the Company's operating accounts used for day-to-day transactions."),
    "Bank borrowings, non-current portion": ("bank_borrowings", "BANK BORROWINGS",
        "Bank borrowings comprise a term loan facility, secured against the Company's "
        "property, plant and equipment, repayable in monthly instalments over the facility "
        "tenure."),
    "Bank borrowings, current portion": ("bank_borrowings", "BANK BORROWINGS",
        "Bank borrowings comprise a term loan facility, secured against the Company's "
        "property, plant and equipment, repayable in monthly instalments over the facility "
        "tenure."),
    "Share capital": ("share_capital", "SHARE CAPITAL",
        "Share capital comprises fully paid ordinary shares, with no par value, issued by "
        "the Company."),
}


class NoteRegistry:
    """Assigns note numbers by order of first reference (notes 1 and 2 are reserved
    for the fixed General Information / Basis of Preparation notes)."""

    def __init__(self) -> None:
        self._order: list[str] = []
        self._heading: dict[str, str] = {}
        self._body: dict[str, str] = {}

    def ref(self, label: str) -> str:
        if label not in NOTE_DEFS:
            return ""
        key, heading, body = NOTE_DEFS[label]
        if key not in self._heading:
            self._order.append(key)
            self._heading[key] = heading
            self._body[key] = body
        return str(self._order.index(key) + 3)

    def entries(self) -> list[tuple[str, str, str]]:
        return [(str(i + 3), self._heading[k], self._body[k]) for i, k in enumerate(self._order)]


# --------------------------------------------------------------------------
# Financial engine
# --------------------------------------------------------------------------

@dataclass
class Structure:
    opex_labels: list[str]
    nc_asset_labels: list[str]
    c_asset_labels_ex_cash: list[str]
    nc_liab_labels: list[str]
    c_liab_labels: list[str]
    equity_fraction: float


def _build_structure(rng) -> Structure:
    opex_extra = rng.sample(OPEX_POOL, rng.randint(4, 8))
    opex_labels = list(OPEX_MANDATORY) + opex_extra + ["Other operating expenses"]

    has_intangibles = rng.random() < 0.6
    has_inventories = rng.random() < 0.7
    has_finance_lease = rng.random() < 0.45
    has_nc_borrowings = rng.random() < 0.8

    nc_asset_labels = ["Property, plant and equipment"]
    if has_intangibles:
        nc_asset_labels.append("Intangible assets")

    extra_assets = rng.sample(EXTRA_ASSET_POOL, rng.randint(0, 2))
    c_asset_labels_ex_cash = (["Inventories"] if has_inventories else []) + [
        "Trade receivables", "Other receivables and prepayments"
    ] + extra_assets

    nc_liab_labels = (["Bank borrowings, non-current portion"] if has_nc_borrowings else [])
    if has_finance_lease:
        nc_liab_labels.append("Finance lease liabilities, non-current portion")

    extra_liabs = rng.sample(EXTRA_LIAB_POOL, rng.randint(0, 1))
    c_liab_labels = ["Trade payables", "Other payables and accruals",
                      "Bank borrowings, current portion"]
    if has_finance_lease:
        c_liab_labels.append("Finance lease liabilities, current portion")
    c_liab_labels += extra_liabs

    return Structure(
        opex_labels=opex_labels,
        nc_asset_labels=nc_asset_labels,
        c_asset_labels_ex_cash=c_asset_labels_ex_cash,
        nc_liab_labels=nc_liab_labels,
        c_liab_labels=c_liab_labels,
        equity_fraction=rng.uniform(0.38, 0.62),
    )


def _distribute(rng, total: float, weight_ranges: list[tuple[float, float]]) -> list[float]:
    if not weight_ranges:
        return []
    raw = [rng.uniform(lo, hi) for lo, hi in weight_ranges]
    s = sum(raw)
    amounts = [round(total * r / s, -1) for r in raw]
    amounts[-1] += round(total - sum(amounts))
    return amounts


def _build_year(rng, revenue: float, structure: Structure,
                 retained_earnings_begin: float, share_capital: float) -> dict[str, Any]:
    cost_ratio = rng.uniform(0.55, 0.70)
    cost_of_sales = round(revenue * cost_ratio, -2)
    gross_profit = revenue - cost_of_sales
    other_income = round(revenue * rng.uniform(0.004, 0.012), -2)

    n_mandatory = len(OPEX_MANDATORY)
    n_pool = len(structure.opex_labels) - n_mandatory - 1
    weight_ranges = (
        [(1.6, 2.6), (1.1, 1.8), (0.9, 1.5), (0.35, 0.6)][:n_mandatory]
        + [(0.12, 0.35)] * n_pool
        + [(0.25, 0.55)]
    )
    opex_total = round(gross_profit * rng.uniform(0.55, 0.72), -2)
    opex_amounts = _distribute(rng, opex_total, weight_ranges)
    opex_items = list(zip(structure.opex_labels, opex_amounts))

    profit_from_ops = gross_profit + other_income - opex_total
    finance_costs = round(revenue * rng.uniform(0.006, 0.018), -2)
    profit_before_tax = profit_from_ops - finance_costs
    tax_expense = round(max(profit_before_tax, 0) * 0.17, -2)
    profit_for_year = profit_before_tax - tax_expense

    retained_earnings_end = retained_earnings_begin + profit_for_year
    total_equity = share_capital + retained_earnings_end

    equity_fraction = min(max(structure.equity_fraction * rng.uniform(0.92, 1.08), 0.28), 0.72)
    total_assets = round(total_equity / equity_fraction, -2)
    total_liabilities = total_assets - total_equity

    nc_liab_fraction = rng.uniform(0.30, 0.50) if structure.nc_liab_labels else 0.0
    nc_liab_total = round(total_liabilities * nc_liab_fraction, -2) if structure.nc_liab_labels else 0.0
    c_liab_total_ex_tax = max(total_liabilities - nc_liab_total - tax_expense, 0.0)
    nc_liab_amounts = _distribute(rng, nc_liab_total, [(0.6, 1.6)] * len(structure.nc_liab_labels))
    c_liab_amounts = _distribute(rng, c_liab_total_ex_tax, [(0.6, 1.6)] * len(structure.c_liab_labels))
    liab_drift = total_liabilities - (sum(nc_liab_amounts) + sum(c_liab_amounts) + tax_expense)
    if c_liab_amounts:
        c_liab_amounts[-1] += liab_drift
    elif nc_liab_amounts:
        nc_liab_amounts[-1] += liab_drift
    else:
        tax_expense += liab_drift

    nc_asset_fraction = rng.uniform(0.30, 0.50)
    nc_asset_total = round(total_assets * nc_asset_fraction, -2)
    nc_asset_amounts = _distribute(rng, nc_asset_total, [(0.6, 1.6)] * len(structure.nc_asset_labels))

    c_asset_fraction_ex_cash = rng.uniform(0.30, 0.50)
    c_asset_total_ex_cash = round((total_assets - nc_asset_total) * c_asset_fraction_ex_cash, -2)
    c_asset_amounts = _distribute(
        rng, c_asset_total_ex_cash, [(0.6, 1.6)] * len(structure.c_asset_labels_ex_cash)
    )
    cash = total_assets - sum(nc_asset_amounts) - sum(c_asset_amounts)

    nc_asset_items = list(zip(structure.nc_asset_labels, nc_asset_amounts))
    c_asset_items = list(zip(structure.c_asset_labels_ex_cash, c_asset_amounts)) + [
        ("Cash and bank balances", cash)
    ]
    nc_liab_items = list(zip(structure.nc_liab_labels, nc_liab_amounts))
    c_liab_items = list(zip(structure.c_liab_labels, c_liab_amounts)) + [("Tax payable", tax_expense)]

    return {
        "revenue": revenue, "cost_of_sales": cost_of_sales, "gross_profit": gross_profit,
        "other_income": other_income, "opex_items": opex_items, "opex_total": opex_total,
        "profit_from_ops": profit_from_ops, "finance_costs": finance_costs,
        "profit_before_tax": profit_before_tax, "tax_expense": tax_expense,
        "profit_for_year": profit_for_year,
        "nc_asset_items": nc_asset_items, "c_asset_items": c_asset_items,
        "total_nc_assets": sum(nc_asset_amounts), "total_c_assets": sum(c_asset_amounts) + cash,
        "total_assets": total_assets,
        "nc_liab_items": nc_liab_items, "c_liab_items": c_liab_items,
        "total_nc_liab": sum(nc_liab_amounts), "total_c_liab": sum(c_liab_amounts) + tax_expense,
        "total_liabilities": total_liabilities,
        "share_capital": share_capital,
        "retained_earnings_begin": retained_earnings_begin,
        "retained_earnings_end": retained_earnings_end,
        "total_equity": total_equity,
    }


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

def _styles() -> dict[str, ParagraphStyle]:
    return {
        "title": ParagraphStyle("title", fontName=f"{FONT}-Bold", fontSize=15, leading=18, alignment=1),
        "subtitle": ParagraphStyle("subtitle", fontName=FONT, fontSize=9, leading=11, alignment=1),
        "body": ParagraphStyle("body", fontName=FONT, fontSize=9, leading=12),
        "body_indent": ParagraphStyle("body_indent", fontName=FONT, fontSize=9, leading=12, leftIndent=10),
        "body_bold": ParagraphStyle("body_bold", fontName=f"{FONT}-Bold", fontSize=9, leading=12),
        "body_center": ParagraphStyle("body_center", fontName=FONT, fontSize=9, leading=12, alignment=1),
        "section": ParagraphStyle("section", fontName=f"{FONT}-Bold", fontSize=9.5, leading=12, spaceBefore=3),
        "small": ParagraphStyle("small", fontName=FONT, fontSize=8, leading=10),
        "col_head": ParagraphStyle("col_head", fontName=f"{FONT}-Bold", fontSize=9, leading=11),
    }


def _money(v: float | str) -> str:
    if v == "" or v is None:
        return ""
    v = round(float(v))
    return f"${v:,.0f}" if v >= 0 else f"(${abs(v):,.0f})"


def _row(s, label, note, curr, prior, *, bold=False, indent=False) -> list:
    label_style = s["body_bold"] if bold else (s["body_indent"] if indent else s["body"])
    return [
        Paragraph(label, label_style),
        Paragraph(note, s["body"]),
        Paragraph(curr, s["body_bold"] if bold else s["body"]),
        Paragraph(prior, s["body_bold"] if bold else s["body"]),
    ]


def _section_row(s, label) -> list:
    return [Paragraph(label, s["section"]), "", "", ""]


def _table(rows, underline_rows: list[int] | None = None, double_underline_rows: list[int] | None = None) -> Table:
    style = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]
    for i in (underline_rows or []):
        style.append(("LINEBELOW", (0, i), (-1, i), 0.5, colors.HexColor("#999999")))
    for i in (double_underline_rows or []):
        style.append(("LINEBELOW", (0, i), (-1, i), 0.75, colors.black))
    return Table(rows, colWidths=COL_WIDTHS, style=TableStyle(style))


def _render_income_statement(story: list, s, registry: NoteRegistry, *, company_name: str,
                              company_uen: str, period_end: date, prior_period_end: date,
                              cur: dict, prev: dict) -> None:
    fy, fy_prior = f"FY{period_end.strftime('%y')}", f"FY{prior_period_end.strftime('%y')}"

    story.append(Paragraph(company_name, s["title"]))
    story.append(Paragraph(f"(UEN: {company_uen})", s["body_center"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("STATEMENT OF COMPREHENSIVE INCOME", s["title"]))
    story.append(Paragraph(f"FOR THE FINANCIAL YEAR ENDED {period_end.strftime('%d %B %Y').upper()}", s["subtitle"]))
    story.append(Paragraph("(All amounts stated in Singapore Dollars, unless otherwise indicated)", s["subtitle"]))
    story.append(Spacer(1, 5 * mm))

    story.append(Table(
        [[Paragraph("", s["col_head"]), Paragraph("Note", s["col_head"]),
          Paragraph(fy, s["col_head"]), Paragraph(fy_prior, s["col_head"])]],
        colWidths=COL_WIDTHS,
        style=TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black), ("BOTTOMPADDING", (0, 0), (-1, 0), 4)]),
    ))

    rows = []
    rows.append(_row(s, "Revenue", registry.ref("Revenue"), _money(cur["revenue"]), _money(prev["revenue"])))
    rows.append(_row(s, "Cost of sales", "", _money(-cur["cost_of_sales"]), _money(-prev["cost_of_sales"])))
    rows.append(_row(s, "Gross profit", "", _money(cur["gross_profit"]), _money(prev["gross_profit"]), bold=True))
    rows.append(_row(s, "Other income", registry.ref("Other income"),
                      _money(cur["other_income"]), _money(prev["other_income"])))
    rows.append(_section_row(s, "Operating expenses"))
    for (label, amt_c), (_, amt_p) in zip(cur["opex_items"], prev["opex_items"]):
        rows.append(_row(s, label, registry.ref(label), _money(-amt_c), _money(-amt_p), indent=True))
    rows.append(_row(s, "Total operating expenses", "", _money(-cur["opex_total"]), _money(-prev["opex_total"]), bold=True))
    rows.append(_row(s, "Profit from operations", "", _money(cur["profit_from_ops"]), _money(prev["profit_from_ops"]), bold=True))
    rows.append(_row(s, "Finance costs", registry.ref("Finance costs"),
                      _money(-cur["finance_costs"]), _money(-prev["finance_costs"])))
    rows.append(_row(s, "Profit before taxation", "", _money(cur["profit_before_tax"]), _money(prev["profit_before_tax"]), bold=True))
    rows.append(_row(s, "Tax expense", registry.ref("Tax expense"),
                      _money(-cur["tax_expense"]), _money(-prev["tax_expense"])))
    rows.append(_row(
        s, "PROFIT FOR THE YEAR, REPRESENTING TOTAL COMPREHENSIVE INCOME FOR THE YEAR", "",
        _money(cur["profit_for_year"]), _money(prev["profit_for_year"]), bold=True,
    ))

    underline = [i for i, r in enumerate(rows) if r[0].text in (
        "Cost of sales", "Total operating expenses", "Finance costs")]
    double = [i for i, r in enumerate(rows) if r[0].text in (
        "PROFIT FOR THE YEAR, REPRESENTING TOTAL COMPREHENSIVE INCOME FOR THE YEAR",)]
    story.append(_table(rows, underline_rows=underline, double_underline_rows=double))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("The accompanying notes form an integral part of these financial statements.", s["small"]))
    story.append(PageBreak())


def _render_balance_sheet(story: list, s, registry: NoteRegistry, *, company_name: str,
                           company_uen: str, period_end: date, prior_period_end: date,
                           director_name: str, signing_date: date, cur: dict, prev: dict) -> None:
    fy, fy_prior = f"FY{period_end.strftime('%y')}", f"FY{prior_period_end.strftime('%y')}"

    story.append(Paragraph(company_name, s["title"]))
    story.append(Paragraph(f"(UEN: {company_uen})", s["body_center"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("STATEMENT OF FINANCIAL POSITION", s["title"]))
    story.append(Paragraph(f"AS AT {period_end.strftime('%d %B %Y').upper()}", s["subtitle"]))
    story.append(Paragraph("(All amounts stated in Singapore Dollars, unless otherwise indicated)", s["subtitle"]))
    story.append(Spacer(1, 5 * mm))

    story.append(Table(
        [[Paragraph("", s["col_head"]), Paragraph("Note", s["col_head"]),
          Paragraph(fy, s["col_head"]), Paragraph(fy_prior, s["col_head"])]],
        colWidths=COL_WIDTHS,
        style=TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.black), ("BOTTOMPADDING", (0, 0), (-1, 0), 4)]),
    ))

    rows = []
    rows.append(_section_row(s, "ASSETS"))
    rows.append(_section_row(s, "Non-current assets"))
    for (label, amt_c), (_, amt_p) in zip(cur["nc_asset_items"], prev["nc_asset_items"]):
        rows.append(_row(s, label, registry.ref(label), _money(amt_c), _money(amt_p), indent=True))
    rows.append(_row(s, "Total non-current assets", "", _money(cur["total_nc_assets"]), _money(prev["total_nc_assets"]), bold=True))
    rows.append(_section_row(s, "Current assets"))
    for (label, amt_c), (_, amt_p) in zip(cur["c_asset_items"], prev["c_asset_items"]):
        rows.append(_row(s, label, registry.ref(label), _money(amt_c), _money(amt_p), indent=True))
    rows.append(_row(s, "Total current assets", "", _money(cur["total_c_assets"]), _money(prev["total_c_assets"]), bold=True))
    rows.append(_row(s, "TOTAL ASSETS", "", _money(cur["total_assets"]), _money(prev["total_assets"]), bold=True))

    rows.append(_section_row(s, "LIABILITIES"))
    rows.append(_section_row(s, "Non-current liabilities"))
    for (label, amt_c), (_, amt_p) in zip(cur["nc_liab_items"], prev["nc_liab_items"]):
        rows.append(_row(s, label, registry.ref(label), _money(amt_c), _money(amt_p), indent=True))
    rows.append(_row(s, "Total non-current liabilities", "", _money(cur["total_nc_liab"]), _money(prev["total_nc_liab"]), bold=True))
    rows.append(_section_row(s, "Current liabilities"))
    for (label, amt_c), (_, amt_p) in zip(cur["c_liab_items"], prev["c_liab_items"]):
        rows.append(_row(s, label, registry.ref(label), _money(amt_c), _money(amt_p), indent=True))
    rows.append(_row(s, "Total current liabilities", "", _money(cur["total_c_liab"]), _money(prev["total_c_liab"]), bold=True))
    rows.append(_row(s, "TOTAL LIABILITIES", "", _money(cur["total_liabilities"]), _money(prev["total_liabilities"]), bold=True))

    rows.append(_section_row(s, "EQUITY"))
    rows.append(_row(s, "Share capital", registry.ref("Share capital"),
                      _money(cur["share_capital"]), _money(prev["share_capital"])))
    rows.append(_row(s, "Retained earnings, beginning of year", "",
                      _money(cur["retained_earnings_begin"]), _money(prev["retained_earnings_begin"])))
    rows.append(_row(s, "Add: Profit for the year (per Income Statement)", "",
                      _money(cur["profit_for_year"]), _money(prev["profit_for_year"])))
    rows.append(_row(s, "Retained earnings, end of year", "",
                      _money(cur["retained_earnings_end"]), _money(prev["retained_earnings_end"])))
    rows.append(_row(s, "Total equity", "", _money(cur["total_equity"]), _money(prev["total_equity"]), bold=True))
    rows.append(_row(s, "TOTAL EQUITY AND LIABILITIES", "",
                      _money(cur["total_equity"] + cur["total_liabilities"]),
                      _money(prev["total_equity"] + prev["total_liabilities"]), bold=True))

    underline_labels = {
        "Total non-current assets", "Total current assets", "Total non-current liabilities",
        "Total current liabilities", "Retained earnings, end of year", "Total equity",
    }
    double_labels = {"TOTAL ASSETS", "TOTAL LIABILITIES", "TOTAL EQUITY AND LIABILITIES"}
    underline = [i for i, r in enumerate(rows) if r[0].text in underline_labels]
    double = [i for i, r in enumerate(rows) if r[0].text in double_labels]
    story.append(_table(rows, underline_rows=underline, double_underline_rows=double))

    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("The accompanying notes form an integral part of these financial statements.", s["small"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("ON BEHALF OF THE BOARD OF DIRECTORS", s["section"]))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph("_________________________", s["body"]))
    story.append(Paragraph(director_name, s["body_bold"]))
    story.append(Paragraph("Director", s["small"]))
    story.append(Paragraph(f"Date: {signing_date.strftime('%d %b %Y')}", s["small"]))
    story.append(PageBreak())


def _render_notes(story: list, s, registry: NoteRegistry, *, company_name: str, period_end: date) -> None:
    story.append(Paragraph(company_name, s["title"]))
    story.append(Paragraph("NOTES TO THE FINANCIAL STATEMENTS", s["title"]))
    story.append(Paragraph(f"FOR THE FINANCIAL YEAR ENDED {period_end.strftime('%d %B %Y').upper()}", s["subtitle"]))
    story.append(Spacer(1, 5 * mm))

    fixed = [
        ("1", "GENERAL INFORMATION",
         "The Company is incorporated in the Republic of Singapore. The principal activities "
         "of the Company are those of general trading."),
        ("2", "BASIS OF PREPARATION AND SIGNIFICANT ACCOUNTING POLICIES",
         "The financial statements have been prepared in accordance with the historical cost "
         "convention, except as disclosed in the accounting policies below, and are drawn up "
         "in accordance with the provisions of the Singapore Financial Reporting Standards "
         "for Small Entities (SFRS for Small Entities)."),
    ]
    for note_no, heading, body in fixed + registry.entries():
        story.append(Paragraph(f"{note_no}. {heading}", s["section"]))
        story.append(Paragraph(body, s["body"]))
        story.append(Spacer(1, 2 * mm))


def _build_pdf(output_path: str, story: list, *, producer: str = "DocuSuite Enterprise 4.2") -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    doc = BaseDocTemplate(
        str(tmp_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
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


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------

def generate_financial_statements(
    output_path: str,
    *,
    company_name: str,
    company_uen: str,
    director_name: str,
    period_end: date,
    seed: str,
    prior_period_end: date | None = None,
    signing_date: date | None = None,
    synthetic_tag: bool = True,
) -> dict:
    """Render one combined income-statement + balance-sheet + notes PDF.

    `seed` drives which optional accounts appear and every amount; the same
    seed always reproduces the same document.
    """
    rng = seeded_rng(seed)
    prior_period_end = prior_period_end or date(period_end.year - 1, period_end.month, period_end.day)
    signing_date = signing_date or period_end + timedelta(days=rng.randint(80, 150))

    structure = _build_structure(rng)
    share_capital = float(rng.choice(SHARE_CAPITAL_POOL))

    revenue_prev = round(rng.uniform(1_400_000, 5_200_000), -3)
    growth = rng.uniform(0.02, 0.22)
    revenue_curr = round(revenue_prev * (1 + growth), -3)

    retained_earnings_begin_prev = round(revenue_prev * rng.uniform(0.08, 0.22), -3)
    prev = _build_year(rng, revenue_prev, structure, retained_earnings_begin_prev, share_capital)
    cur = _build_year(rng, revenue_curr, structure, prev["retained_earnings_end"], share_capital)

    s = _styles()
    registry = NoteRegistry()
    story: list = []
    _render_income_statement(
        story, s, registry, company_name=company_name, company_uen=company_uen,
        period_end=period_end, prior_period_end=prior_period_end, cur=cur, prev=prev,
    )
    _render_balance_sheet(
        story, s, registry, company_name=company_name, company_uen=company_uen,
        period_end=period_end, prior_period_end=prior_period_end,
        director_name=director_name, signing_date=signing_date, cur=cur, prev=prev,
    )
    _render_notes(story, s, registry, company_name=company_name, period_end=period_end)

    _build_pdf(output_path, story)

    if synthetic_tag:
        reader = PdfReader(output_path)
        writer = PdfWriter(clone_from=reader)
        writer.add_metadata({"/Keywords": SYNTHETIC_TAG})
        with open(output_path, "wb") as fh:
            writer.write(fh)

    return {
        "output_path": output_path, "company_name": company_name,
        "period_end": period_end.isoformat(), "profit_for_year": cur["profit_for_year"],
        "total_assets": cur["total_assets"],
    }


# --------------------------------------------------------------------------
# Mastersheet CLI
# --------------------------------------------------------------------------

def _rows(ws) -> list[dict[str, Any]]:
    header = [c.value for c in ws[1]]
    return [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]


def load_cases(xlsx: str | Path, cases: list[str] | None = None) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(str(xlsx), data_only=True)
    registry = {str(r["Case ID"]): r for r in _rows(wb["Case Registry"])}

    doc_rows: dict[str, dict[str, dict[str, Any]]] = {}
    for r in _rows(wb["Source of Wealth Docs"]):
        case_id = str(r["Case ID"])
        doc_type = str(r.get("Document Type") or "")
        doc_rows.setdefault(case_id, {})[doc_type] = r

    people: dict[str, list[dict[str, Any]]] = {}
    for ind in _rows(wb["Individuals"]):
        people.setdefault(str(ind["Case ID"]), []).append(ind)

    out = []
    for case_id, case in registry.items():
        if cases and case_id not in cases:
            continue
        if not case.get("Company Name"):
            continue
        docs = doc_rows.get(case_id, {})
        if "Balance Sheet" not in docs and "Income Statement" not in docs:
            continue
        out.append({
            "case_id": case_id,
            "case": case,
            "bs_row": docs.get("Balance Sheet", {}),
            "is_row": docs.get("Income Statement", {}),
            "people": sorted(people.get(case_id, []), key=lambda r: r.get("Person Seq") or 0),
        })
    return out


def generate(xlsx: str | Path, outdir: str | Path, cases: list[str] | None = None,
             synthetic_tag: bool = True) -> list[Path]:
    written: list[Path] = []
    for record in load_cases(xlsx, cases):
        case_id = record["case_id"]
        facts = company_facts(record["case"], record["people"])
        director_name = facts["holders"][0]["name"] if facts["holders"] else ""

        application_date = as_date(record["case"].get("Application Date")) or date(2026, 1, 1)
        period_end = _choose_period_end(seeded_rng(f"{case_id}|sow-fye"), application_date)

        filenames = {
            record["bs_row"].get("Filename"),
            record["is_row"].get("Filename"),
        } - {None}
        if not filenames:
            filenames = {f"{case_id}_SourceWealth_Financials.pdf"}

        primary = Path(outdir) / sorted(filenames)[0]
        result = generate_financial_statements(
            str(primary),
            company_name=facts["company_name"],
            company_uen=facts["uen"],
            director_name=director_name,
            period_end=period_end,
            seed=f"{case_id}|sow-financials",
            synthetic_tag=synthetic_tag,
        )
        written.append(primary)

        # The mastersheet previously carried separate rows for Balance Sheet and
        # Income Statement. This generator now emits a single combined PDF per
        # case. Do not duplicate the same PDF under multiple filenames.

        print(f"{case_id}  {primary.name}  "
              f"profit={result['profit_for_year']:,.0f}  assets={result['total_assets']:,.0f}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("xlsx")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--cases", nargs="*")
    ap.add_argument("--no-synthetic-tag", action="store_true")
    args = ap.parse_args()
    Path(args.outdir).mkdir(parents=True, exist_ok=True)
    generate(args.xlsx, args.outdir, args.cases, synthetic_tag=not args.no_synthetic_tag)


if __name__ == "__main__":
    main()
