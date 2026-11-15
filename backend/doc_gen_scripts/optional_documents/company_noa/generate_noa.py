#!/usr/bin/env python3
"""generate_noa.py — Company Notice of Assessment (Amended), matching the IRAS
myTax Portal export.

Rewritten from an HTML/CSS + headless-Chrome pipeline to match the rest of
doc_gen_scripts: pure ReportLab canvas output (no browser dependency required),
driven by the mastersheet directly (Document Type == "Notice of Assessment (NOA)
- Company") instead of a standalone CSV export.

As of mastersheet v16, NOA has its own dedicated "Notice of Assessment" tab,
covering every case -- previously (v15) it lived inside "Optional Documents"
alongside Proof of Address / Source of Wealth rows, and only covered a subset
of cases.

Visual design -- layout, colours, wording, the computation table -- is copied
from noa.html/noa.css (kept alongside this file as the design reference; not
used at generation time anymore). NOA Date, Year of Assessment and the Tax
Reference Number now come straight from the mastersheet's own "(auto)" columns;
only the figures with no mastersheet column at all (ECI, the tax computation it
drives, the previous-assessment date, the tracking code) are still synthesized,
seeded per case so reruns are reproducible.

Usage:
cd backend/doc_gen_scripts/optional_documents/company_noa
python generate_noa.py ../../v16_document_mastersheet.xlsx --outdir out/
python generate_noa.py ../../v16_document_mastersheet.xlsx --outdir out/ --cases C042

Run with no arguments to build one example NOA ("Harbourview Trading") at
output/noa_harbourview_trading.pdf.
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
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader, simpleSplit
from reportlab.pdfgen import canvas as pdfcanvas

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import truth  # noqa: E402
from case_facts import seeded_rng  # noqa: E402
from synthetic_signature import draw_signature  # noqa: E402
from corp_source_of_wealth.balance_sheet_income_statement_generator import compute_years  # noqa: E402

FONT = "Helvetica"
BOLD = "Helvetica-Bold"

PAGE_W, PAGE_H = A4
MARGIN_TOP = 12 * mm
MARGIN_RIGHT = 14 * mm
MARGIN_BOTTOM = 28 * mm
MARGIN_LEFT = 18 * mm
CONTENT_W = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT

IRAS_BANNER = colors.HexColor("#1d70b8")
IRAS_SOFT = colors.HexColor("#d6e6f8")
INK = colors.HexColor("#222222")
MUTED = colors.HexColor("#333333")
RULE = colors.HexColor("#6bb3d6")
GREY_BOX = colors.HexColor("#f2f2f2")
YA_GREY = colors.HexColor("#f0f0f0")
LINK_BLUE = colors.HexColor("#1d70b8")
BLACK = colors.black

ASSETS = Path(__file__).resolve().parent
SHARED_ASSETS = ASSETS.parent.parent / "assets" / "images"
LOGO_PATH = SHARED_ASSETS / "iras-logo.png"
DEFAULT_OUT = ASSETS / "output" / "noa_harbourview_trading.pdf"

# The comptroller's signature is the same physical signature on every real NOA --
# seeded on a fixed string (not per-case) so it picks one cursive font and stays
# that font across the whole batch, rather than varying like a per-person seed.
SIGNATORY_SEED = "iras-noa|comptroller-of-income-tax"

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# x, width pairs lifted straight from noa.html's barcode <svg viewBox="0 0 81.9 24">
BARCODE_BARS = [
    (0.00, 1.15), (4.00, 1.15), (6.30, 2.85), (10.30, 2.85), (14.30, 1.15),
    (16.60, 2.85), (20.60, 1.15), (24.60, 1.15), (26.90, 1.15), (29.20, 2.85),
    (33.20, 1.15), (37.20, 1.15), (39.50, 1.15), (41.80, 2.85), (45.80, 2.85),
    (49.80, 2.85), (53.80, 1.15), (57.80, 1.15), (60.10, 1.15), (62.40, 2.85),
    (66.40, 1.15), (70.40, 1.15), (72.70, 2.85), (76.70, 2.85), (80.70, 1.15),
]

_POSTAL_RE = re.compile(r"(?:Singapore\s*)?(\d{6})\s*$", re.I)


def split_address(raw: str) -> tuple[str, str, str]:
    """'10 Anson Road, #18-12 International Plaza, Singapore 079903' -> street /
    unit / postal, same convention as the other generators' address splitters."""
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


def money(value: float | int, decimals: int = 2) -> str:
    if decimals == 0:
        return f"{int(round(value)):,}"
    return f"{value:,.{decimals}f}"


def format_noa_date(value: date) -> str:
    return f"{value.day:02d} {MONTHS[value.month - 1]} {value.year}"


def compute_noa_tax(eci: int) -> dict[str, float | int]:
    """Singapore's partial tax exemption scheme: 75% of the first $10,000 of
    chargeable income and 50% of the next $190,000 are exempt, tax at 17% on the
    remainder, then a rebate capped at $40,000."""
    first = min(eci, 10_000)
    nxt = min(max(eci - 10_000, 0), 190_000)
    exempt_first = int(round(first * 0.75))
    exempt_next = int(round(nxt * 0.50))
    after = eci - exempt_first - exempt_next
    tax = round(after * 0.17, 2)
    rebate = round(min(tax * 0.50, 40_000.00), 2)
    payable = round(tax - rebate, 2)
    return {
        "eci": eci,
        "exempt_first": exempt_first,
        "exempt_next": exempt_next,
        "after": after,
        "tax": tax,
        "rebate": rebate,
        "payable": payable,
        "discharge": rebate,
    }


def _load_logo() -> ImageReader | None:
    return ImageReader(str(LOGO_PATH)) if LOGO_PATH.exists() else None


def _text(c: pdfcanvas.Canvas, x: float, y: float, text: str, *, font: str = FONT,
          size: float = 10, color=INK, align: str = "left") -> None:
    c.setFillColor(color)
    c.setFont(font, size)
    if align == "right":
        c.drawRightString(x, y, text)
    elif align == "center":
        c.drawCentredString(x, y, text)
    else:
        c.drawString(x, y, text)


def _wrapped(c: pdfcanvas.Canvas, text: str, x: float, top: float, width: float, *,
             font: str = FONT, size: float = 10, leading: float | None = None, color=INK) -> float:
    leading = leading or size * 1.35
    c.setFillColor(color)
    c.setFont(font, size)
    y = top - size
    for line in simpleSplit(text, font, size, width):
        c.drawString(x, y, line)
        y -= leading
    return y + leading - size


def _rounded_pill(c: pdfcanvas.Canvas, x: float, y: float, w: float, h: float,
                   radius: float, fill_color) -> None:
    c.setFillColor(fill_color)
    c.roundRect(x, y, w, h, radius, fill=1, stroke=0)


def _save_pdf(output_path: str, draw_fn, *, synthetic_tag: bool = True) -> None:
    tmp_path = Path(output_path).with_suffix(".tmp.pdf")
    c = pdfcanvas.Canvas(str(tmp_path), pagesize=A4)
    draw_fn(c)
    c.save()

    reader = PdfReader(str(tmp_path))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    creation = datetime.now().strftime("D:%Y%m%d%H%M%S")
    meta = {"/Producer": "myTax Portal", "/Creator": "myTax Portal", "/CreationDate": creation}
    if synthetic_tag:
        meta["/Subject"] = "SYNTHETIC-TEST-DATA"
        meta["/Keywords"] = "SYNTHETIC-TEST-DATA"
    writer.add_metadata(meta)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as handle:
        writer.write(handle)
    tmp_path.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Renderer
# --------------------------------------------------------------------------

def generate_noa(
    output_path: str,
    *,
    tax_ref: str,
    noa_date: date,
    year_of_assessment: str,
    company_name: str,
    company_address: str,
    eci: int,
    previous_date: date | None = None,
    tracking: str | None = None,
    synthetic_tag: bool = True,
) -> dict:
    tax = compute_noa_tax(eci)
    previous_date = previous_date or (noa_date - timedelta(days=44))
    tracking = tracking or "810879239-0-7"
    street, unit, postal = split_address(company_address)
    logo = _load_logo()
    logo_w = 26 * mm
    logo_h = logo_w * 352 / 304  # iras-logo.png's own aspect ratio

    def draw_header(c: pdfcanvas.Canvas) -> float:
        top = PAGE_H - MARGIN_TOP
        meta_y = top - 2 * mm - 9
        _text(c, MARGIN_LEFT, meta_y, "Tax Reference Number :", size=9, color=MUTED)
        _text(c, MARGIN_LEFT + 42 * mm, meta_y, tax_ref, size=9, color=INK)
        meta_y -= 9 * 1.55
        _text(c, MARGIN_LEFT, meta_y, "Date :", size=9, color=MUTED)
        _text(c, MARGIN_LEFT + 42 * mm, meta_y, format_noa_date(noa_date), size=9, color=INK)

        logo_x = PAGE_W - MARGIN_RIGHT - logo_w
        logo_y = top - logo_h
        if logo is not None:
            c.drawImage(logo, logo_x, logo_y, width=logo_w, height=logo_h, mask="auto")
        meta_h = 2 * mm + 2 * (9 * 1.55)
        return top - max(logo_h, meta_h)

    def draw_footer(c: pdfcanvas.Canvas, sheet_id: str, page_label: str) -> None:
        base_y = 7 * mm
        _text(c, PAGE_W / 2, base_y + 2 * (8 * 1.45), "Website: www.iras.gov.sg  •  myTax Portal: mytax.iras.gov.sg",
              size=8, color=MUTED, align="center")
        _text(c, PAGE_W / 2, base_y + (8 * 1.45), "Tel: 1800 356 8622", size=8, color=MUTED, align="center")
        codes = [sheet_id, page_label, "301", "CAN00101", f"206-13-{tracking}"]
        n = len(codes)
        for i, token in enumerate(codes):
            x = MARGIN_LEFT + CONTENT_W * i / (n - 1)
            if i == 0:
                _text(c, x, base_y, token, size=8, color=MUTED, align="left")
            elif i == n - 1:
                _text(c, x, base_y, token, size=8, color=MUTED, align="right")
            else:
                _text(c, x, base_y, token, size=8, color=MUTED, align="center")

    def draw_page1(c: pdfcanvas.Canvas) -> None:
        y = draw_header(c)

        y -= 1.5 * mm
        _text(c, MARGIN_LEFT, y - 10.5, company_name.upper(), font=BOLD, size=10.5, color=INK)
        y -= 10.5 * 1.38
        for line in [street.upper(), unit.upper(), f"SINGAPORE {postal}"] if unit else [street.upper(), f"SINGAPORE {postal}"]:
            _text(c, MARGIN_LEFT, y - 10.5, line, size=10.5, color=INK)
            y -= 10.5 * 1.38

        # Ident block: side marks, barcode, sheet id -- purely decorative, but part
        # of the source design, so reproduced rather than dropped.
        y -= 2 * mm
        for mark_y in (y - 1 * mm, y - 4 * mm):
            c.setStrokeColor(BLACK)
            c.setLineWidth(1.4)
            c.line(MARGIN_LEFT - 12 * mm, mark_y, MARGIN_LEFT - 4 * mm, mark_y)
        bc_w = 36 * mm
        bc_h = 6.2 * mm
        scale = bc_w / 81.9
        c.setFillColor(BLACK)
        for bx, bw in BARCODE_BARS:
            c.rect(MARGIN_LEFT + bx * scale, y - bc_h, bw * scale, bc_h, fill=1, stroke=0)
        y -= bc_h + 11 * mm
        _text(c, MARGIN_LEFT + bc_w / 2, y, "1-1", size=9, color=MUTED, align="center")
        y -= 4 * mm

        # Title stack: banner pill + year-of-assessment pill.
        banner_h = 8.6 * mm
        _rounded_pill(c, MARGIN_LEFT, y - banner_h, CONTENT_W, banner_h, 7, IRAS_BANNER)
        _text(c, MARGIN_LEFT + 5 * mm, y - banner_h + 3 * mm, "Income Tax - Notice of Assessment (Amended)",
              font=BOLD, size=12.5, color=colors.white)
        y -= banner_h
        ya_h = 7.9 * mm
        _rounded_pill(c, MARGIN_LEFT, y - ya_h, CONTENT_W, ya_h, 7, YA_GREY)
        _text(c, MARGIN_LEFT + 5 * mm, y - ya_h + 2.6 * mm, f"Year of Assessment {year_of_assessment}",
              font=BOLD, size=11, color=INK)
        y -= ya_h + 5 * mm

        y = _wrapped(c, "We have revised your estimated tax assessment to allow the tax rebate announced in Budget 2025.",
                     MARGIN_LEFT, y, CONTENT_W, size=10, leading=13.5)
        y -= 2.8 * mm
        y = _wrapped(c, "The assessment may be revised after we have examined your Form C-S/ C, financial statements and "
                        "tax computation. No objection is required.", MARGIN_LEFT, y, CONTENT_W, size=10, leading=13.5)
        y -= 3 * mm
        _text(c, MARGIN_LEFT, y - 10, f"There is a tax discharge of ${money(tax['discharge'])} for this assessment.",
              font=BOLD, size=10, color=INK)
        y -= 10 + 4 * mm

        # "Total ($)" header, ruled top and bottom.
        c.setStrokeColor(RULE)
        c.setLineWidth(4)
        c.line(MARGIN_LEFT, y, MARGIN_LEFT + CONTENT_W, y)
        y -= 1.6 * mm
        _text(c, MARGIN_LEFT + CONTENT_W, y - 8, "Total ($)", font=BOLD, size=10.5, color=INK, align="right")
        y -= 1.6 * mm + 10.5
        c.setStrokeColor(RULE)
        c.setLineWidth(4)
        c.line(MARGIN_LEFT, y, MARGIN_LEFT + CONTENT_W, y)
        y -= 3 * mm

        amt_x = MARGIN_LEFT + CONTENT_W
        inner_x = amt_x - 34 * mm
        row_h = 10 * 1.4

        def row(label: str, amount: str | None, *, bold: bool = False, extra_gap: bool = False,
                inner: str | None = None, indent: float = 0.0) -> None:
            nonlocal y
            if extra_gap:
                y -= 3.2 * mm
            f = BOLD if bold else FONT
            _text(c, MARGIN_LEFT + indent, y - 10, label, font=f, size=10, color=INK)
            if inner:
                _text(c, inner_x, y - 10, inner, size=10, color=INK, align="right")
            if amount:
                _text(c, amt_x, y - 10, amount, font=f, size=10, color=INK, align="right")
            y -= row_h

        row("Estimated Chargeable Income before deducting Exempt Amount", money(tax["eci"], 0), bold=True)
        row("Less:  Exempt Amount", None, extra_gap=True)
        row("First 10,000 @ 75.00%", None, inner=money(tax["exempt_first"], 0), indent=6 * mm)
        row("Next 190,000 @ 50.00%", None, inner=money(tax["exempt_next"], 0), indent=6 * mm)
        row("Estimated Chargeable Income after deducting Exempt Amount", money(tax["after"], 0), bold=True, extra_gap=True)
        row(f"Tax on {money(tax['after'], 0)} @ 17.00%", money(tax["tax"]))
        row("Tax Assessed", money(tax["tax"]))
        row("Less:  Tax Rebate (net of CIT Rebate Cash Grant, if any)", money(tax["rebate"]), extra_gap=True)

        c.setStrokeColor(RULE)
        c.setLineWidth(4)
        c.line(MARGIN_LEFT, y, MARGIN_LEFT + CONTENT_W, y)
        y -= 2.2 * mm
        row("Estimated Tax Payable", money(tax["payable"]), bold=True)
        row(f"Less:  Previous Assessment dated {format_noa_date(previous_date)}", money(tax["tax"]))

        c.setStrokeColor(RULE)
        c.setLineWidth(4)
        c.line(MARGIN_LEFT, y, MARGIN_LEFT + CONTENT_W, y)
        y -= 2.2 * mm
        row("Estimated Tax Discharged per this Assessment", money(tax["discharge"]), bold=True)

        c.setStrokeColor(RULE)
        c.setLineWidth(4)
        c.line(MARGIN_LEFT, y + row_h - 10 * 1.4, MARGIN_LEFT + CONTENT_W, y + row_h - 10 * 1.4)

        draw_footer(c, "1-1", "Page 1 of 2")

    def draw_page2(c: pdfcanvas.Canvas) -> None:
        y = draw_header(c)
        y -= 28 * mm

        for mark_y in (y + 8 * mm, y + 8 * mm - 2.2 * mm):
            c.setStrokeColor(BLACK)
            c.setLineWidth(1.4)
            c.line(MARGIN_LEFT - 12 * mm, mark_y, MARGIN_LEFT - 4 * mm, mark_y)

        banner_h = 8.9 * mm
        _rounded_pill(c, MARGIN_LEFT, y - banner_h, 68 * mm, banner_h, 9, IRAS_SOFT)
        _text(c, MARGIN_LEFT + 5.5 * mm, y - banner_h + 2.7 * mm, "What do you need to do?", font=BOLD, size=12, color=INK)
        y -= banner_h + 1.6 * mm

        box_h = 12 * mm
        _rounded_pill(c, MARGIN_LEFT, y - box_h, CONTENT_W, box_h, 7, GREY_BOX)
        _text(c, MARGIN_LEFT + 10 * mm, y - box_h + box_h / 2 - 3, "•", size=10, color=INK)
        _text(c, MARGIN_LEFT + 14 * mm, y - box_h + box_h / 2 - 3,
              "View Account Summary at myTax Portal for your latest income tax balance.",
              font=BOLD, size=10, color=LINK_BLUE)
        y -= box_h + 8 * mm

        _text(c, MARGIN_LEFT, y - 10, "Thank you for your contribution towards nation building.", size=10, color=INK)
        y -= 10 + 10 * mm

        draw_signature(c, "OwFookChuen", MARGIN_LEFT, y, SIGNATORY_SEED, size=18)
        y -= 18 * 1.0 + 3.5 * mm
        _text(c, MARGIN_LEFT, y - 9.5, "OW FOOK CHUEN", size=9.5, color=INK)
        y -= 9.5 * 1.4
        _text(c, MARGIN_LEFT, y - 9.5, "COMPTROLLER OF INCOME TAX", size=9.5, color=INK)

        draw_footer(c, "2-2", "Page 2 of 2")

    def draw(c: pdfcanvas.Canvas) -> None:
        draw_page1(c)
        c.showPage()
        draw_page2(c)

    _save_pdf(output_path, draw, synthetic_tag=synthetic_tag)
    return {"output_path": output_path, "company_name": company_name, "tax_ref": tax_ref}


# --------------------------------------------------------------------------
# Mastersheet-driven persona
# --------------------------------------------------------------------------

def build_persona(row: dict[str, Any]) -> dict:
    """Identity/date fields come straight from the mastersheet's own "(auto)"
    columns and its NOA Date / Year of Assessment. ECI is not independently
    seeded here: it's the same case's actual profit_before_tax from the
    Source of Wealth income statement (see
    corp_source_of_wealth.balance_sheet_income_statement_generator.generate,
    which seeds that document's financial engine on f"{case_id}|sow-
    financials") — otherwise a reviewer cross-checking the NOA against the
    company's own financial statements would find two unrelated numbers for
    what should be the same figure. Only the tax computation it drives, the
    previous-assessment date, and the tracking code stay seeded per case."""
    case_id = str(row.get("Case ID") or "")
    filename = str(row.get("Filename") or "")
    rng = seeded_rng(f"{case_id}|{filename}|noa")

    noa_date = row.get("NOA Date")
    if isinstance(noa_date, datetime):
        noa_date = noa_date.date()
    elif not isinstance(noa_date, date):
        noa_date = date.today()

    cur, _prev = compute_years(f"{case_id}|sow-financials")
    eci = max(round(cur["profit_before_tax"]), 0)

    return dict(
        tax_ref=str(row.get("Tax Reference Number (UEN) (auto)") or "").strip(),
        noa_date=noa_date,
        year_of_assessment=re.sub(r"\D", "", str(row.get("Year of Assessment") or "")) or "2025",
        company_name=str(row.get("Company Name (auto)") or ""),
        company_address=str(row.get("Company Address (auto)") or ""),
        eci=eci,
        previous_date=date(noa_date.year, 7, rng.choice([3, 5, 8, 10, 12, 15]))
        if noa_date.month > 7 else (noa_date - timedelta(days=rng.randint(30, 60))),
        tracking=f"{rng.randint(100000000, 999999999)}-{rng.randint(0, 9)}-{rng.randint(0, 9)}",
    )


NOA_DOCUMENT_TYPE = "Notice of Assessment (NOA) - Company"


def _rows(ws) -> list[dict[str, Any]]:
    header = [c.value for c in ws[1]]
    return [dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)]


def load_cases(xlsx: str | Path, cases: list[str] | None = None) -> list[dict[str, Any]]:
    # As of mastersheet v16, NOA has its own dedicated "Notice of Assessment" tab
    # (split out of "Optional Documents", which used to hold it alongside Proof of
    # Address / Source of Wealth rows) and now covers every case, not just a subset.
    wb = openpyxl.load_workbook(str(xlsx), data_only=True)
    ws = wb["Notice of Assessment"]
    out = []
    for row in _rows(ws):
        if str(row.get("Document Type") or "").strip() != NOA_DOCUMENT_TYPE:
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
        filename = str(row.get("Filename") or f"{row.get('Case ID')}_NOA.pdf")
        path = Path(outdir) / filename
        persona = build_persona(row)
        generate_noa(str(path), synthetic_tag=synthetic_tag, **persona)
        truth.record(path, "notice_of_assessment",
                     truth.notice_of_assessment(persona, compute_noa_tax(persona["eci"])["payable"]))
        written.append(path)
        print(f"{row.get('Case ID')}  {filename}  {row.get('Company Name (auto)')}")
    return written


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate Company Notice of Assessment PDFs.")
    parser.add_argument("xlsx", nargs="?", help="Mastersheet path")
    parser.add_argument("--outdir", "-o", type=Path, default=ASSETS / "output", help="Output directory")
    parser.add_argument("--cases", nargs="*", help="Case IDs to include; default is all")
    parser.add_argument("--no-synthetic-tag", action="store_true", help="omit the SYNTHETIC-TEST-DATA metadata tag")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.xlsx:
        print(generate_noa(
            str(DEFAULT_OUT),
            tax_ref="201834567K",
            noa_date=date(2025, 4, 18),
            year_of_assessment="2025",
            company_name="Harbourview Trading Pte. Ltd.",
            company_address="10 Anson Road, #18-12 International Plaza, Singapore 079903",
            eci=428_000,
        ))
        return
    args.outdir.mkdir(parents=True, exist_ok=True)
    written = generate(args.xlsx, args.outdir, cases=args.cases, synthetic_tag=not args.no_synthetic_tag)
    print(f"Wrote {len(written)} NOA PDF(s) to {args.outdir}")


if __name__ == "__main__":
    main()
