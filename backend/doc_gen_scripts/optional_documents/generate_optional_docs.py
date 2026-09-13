#!/usr/bin/env python3
"""Generate company NOA, utility-bill, and investment PDFs from the optional-documents CSV.

CSV identity fields (name, address, UEN, NOA date, YA) are used where they exist.
Amounts, usage, holdings, and bill dates that are not in the CSV are synthesised
deterministically from the Case ID + filename so reruns stay stable.

Example:
    python3 generate_optional_docs.py --dry-run
    python3 generate_optional_docs.py --types noa,utility,investment
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import random
import re
import sys
from calendar import monthrange
from datetime import date, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_CSV = Path(
    "/Users/kylene/Downloads/v15_document_mastersheet.xlsx - Optional Documents.csv"
)

NOA_TYPE = "Notice of Assessment (NOA) - Company"
POA_TYPE = "Proof of Address (Company)"
SOW_TYPES = {
    "Source of Wealth (Individual) - Director & UBO",
    "Source of Wealth (Individual) - Shareholder",
}

sys.path.insert(0, str(ROOT / "company_noa"))
sys.path.insert(0, str(ROOT / "utility"))
sys.path.insert(0, str(ROOT / "investment"))

from generate_noa import (  # noqa: E402
    bundled_html,
    html_to_pdf,
    apply_placeholders,
)
from generate_bills import (  # noqa: E402
    CSS_PATH as BILL_CSS,
    TEMPLATE_PATH as BILL_HTML,
    build_record as build_bill_record,
    fill_template as fill_bill,
    fmt_date as fmt_bill_date,
    month_labels as bill_month_labels,
)
from generate_statements import (  # noqa: E402
    build_statement,
    make_address,
    render as render_statement,
)


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
POSTAL_RE = re.compile(r"(?:Singapore\s*)?(\d{6})\s*$", re.I)

# All generated document dates sit in this recent month.
DOC_YEAR = 2026
DOC_MONTH = 8  # August


def month_end(year: int, month: int) -> date:
    return date(year, month, monthrange(year, month)[1])


def seeded_rng(*parts: str) -> random.Random:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def money(value: float, decimals: int = 2) -> str:
    if decimals == 0:
        return f"{int(round(value)):,}"
    return f"{value:,.{decimals}f}"


def format_noa_date(value: date) -> str:
    return f"{value.day:02d} {MONTHS[value.month - 1]} {value.year}"


def split_address(raw: str) -> tuple[str, str, str]:
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    postal = ""
    kept: list[str] = []
    for part in parts:
        match = POSTAL_RE.search(part)
        if match and not postal:
            postal = match.group(1)
            remainder = POSTAL_RE.sub("", part).strip()
            remainder = re.sub(r"Singapore\s*$", "", remainder, flags=re.I).strip()
            if remainder:
                kept.append(remainder)
        else:
            kept.append(part)
    street = kept[0] if kept else (raw or "").strip()
    unit = ", ".join(kept[1:]) if len(kept) > 1 else ""
    return street, unit, postal


def addressee_lines(company: str, address: str) -> str:
    street, unit, postal = split_address(address)
    lines = [f'        <div class="name">{company.upper()}</div>']
    lines.append(f"        <div>{street.upper()}</div>")
    if unit:
        lines.append(f"        <div>{unit.upper()}</div>")
    if postal:
        lines.append(f"        <div>SINGAPORE {postal}</div>")
    return "\n".join(lines)


def year_of_assessment(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    return digits or "2025"


def compute_noa_tax(eci: int) -> dict[str, float | int]:
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


def noa_values(row: dict[str, str]) -> dict[str, str]:
    rng = seeded_rng(row["Case ID"], row["Filename"], "noa")
    issued = date(DOC_YEAR, DOC_MONTH, rng.choice([8, 10, 12, 14, 15, 18, 20, 22]))
    previous = date(DOC_YEAR, 7, rng.choice([3, 5, 8, 10, 12, 15]))
    eci = rng.randrange(240_000, 820_000, 1_000)
    tax = compute_noa_tax(eci)
    tracking = f"{rng.randint(100000000, 999999999)}-{rng.randint(0, 9)}-{rng.randint(0, 9)}"
    return {
        "TAX_REF": row["Tax Reference Number (UEN) (auto)"].strip(),
        "NOA_DATE": format_noa_date(issued),
        "YEAR_OF_ASSESSMENT": year_of_assessment(row["Year of Assessment"]),
        "ADDRESSEE_LINES": addressee_lines(
            row["Company Name (auto)"],
            row["Company Address (auto)"],
        ),
        "ECI": money(tax["eci"], 0),
        "EXEMPT_FIRST": money(tax["exempt_first"], 0),
        "EXEMPT_NEXT": money(tax["exempt_next"], 0),
        "ECI_AFTER": money(tax["after"], 0),
        "TAX_AMT": money(tax["tax"]),
        "REBATE_AMT": money(tax["rebate"]),
        "PAYABLE_AMT": money(tax["payable"]),
        "DISCHARGE_AMT": money(tax["discharge"]),
        "PREV_DATE": format_noa_date(previous),
        "TRACKING": tracking,
    }


def classify(row: dict[str, str]) -> str | None:
    kind = row["Document Type"].strip()
    filename = row["Filename"].lower()
    if kind == NOA_TYPE:
        return "noa"
    if kind == POA_TYPE:
        if "telecom" in filename:
            return "telecom"
        return "utility"
    if kind in SOW_TYPES:
        return "investment"
    return None


def inline_bill_html(html: str) -> str:
    css = BILL_CSS.read_text(encoding="utf-8")
    return html.replace(
        '<link rel="stylesheet" href="bill.css">',
        f"<style>{css}</style>",
    )


def build_utility_html(row: dict[str, str]) -> str:
    rng = seeded_rng(row["Case ID"], row["Filename"], "utility")
    fields = build_bill_record(rng, 1)
    street, unit, postal = split_address(row["Company Address (auto)"])
    fields["BILLING_NAME"] = row["Company Name (auto)"].strip()
    fields["BILLING_ADDRESS_LINE_1"] = street
    fields["BILLING_ADDRESS_LINE_2"] = unit
    fields["BILLING_POSTAL_CODE"] = postal or "000000"
    fields["PREMISE_ADDRESS_LINE_1"] = street
    fields["PREMISE_ADDRESS_LINE_2"] = unit
    fields["PREMISE_POSTAL_CODE"] = postal or "000000"
    fields["_company"] = row["Company Name (auto)"].strip()

    # Bill issued in August for July usage.
    bill_date = date(DOC_YEAR, DOC_MONTH, rng.choice([1, 1, 2, 3, 5]))
    period_end = month_end(DOC_YEAR, 7)
    period_start = date(DOC_YEAR, 7, 1)
    due_date = bill_date + timedelta(days=rng.choice([12, 13, 14, 15]))
    fields["BILL_DATE"] = fmt_bill_date(bill_date)
    fields["BILLING_PERIOD_START"] = fmt_bill_date(period_start)
    fields["BILLING_PERIOD_END"] = fmt_bill_date(period_end)
    fields["PAYMENT_DUE_DATE"] = fmt_bill_date(due_date)
    fields["PAYMENT_DEDUCTION_DATE"] = fmt_bill_date(
        due_date + timedelta(days=rng.choice([0, 1, 2]))
    )
    fields["PAYMENT_RECEIVED_DATE"] = fmt_bill_date(
        date(DOC_YEAR, 7, rng.choice([12, 14, 16, 18, 20]))
    )
    fields["CONTRACT_END_DATE"] = fmt_bill_date(
        date(DOC_YEAR + rng.randint(2, 4), rng.randint(1, 12), rng.choice([1, 15, 28]))
    )
    months = bill_month_labels(period_end)
    fields["CURRENT_MONTH"] = months[-1]
    for i, label in enumerate(months[:-1], start=1):
        fields[f"MONTH_{i}"] = label

    template = BILL_HTML.read_text(encoding="utf-8")
    return inline_bill_html(fill_bill(template, fields))


def sg_short_date(value: date) -> str:
    return f"{value.day:02d}/{value.month:02d}/{value.year % 100:02d}"


def build_telecom_html(row: dict[str, str]) -> str:
    rng = seeded_rng(row["Case ID"], row["Filename"], "telecom")
    street, unit, postal = split_address(row["Company Address (auto)"])
    bill_day = date(DOC_YEAR, DOC_MONTH, rng.choice([8, 10, 12, 15, 16, 18]))
    due = bill_day + timedelta(days=14)
    cutoff = bill_day - timedelta(days=3)
    previous = round(rng.uniform(80.0, 260.0), 2)
    overdue = round(rng.choice([0.0, 0.0, 0.0, rng.uniform(12.0, 48.0)]), 2)
    current = round(rng.uniform(95.0, 280.0), 2)
    gst = round(current * 9 / 109, 2)
    nontax = round(rng.uniform(0.0, 8.5), 2)
    savings = round(rng.choice([0.0, 5.0, 10.0, 15.0, 20.0]), 2)
    outstanding = round(previous + overdue + current - savings, 2)
    fields = {
        "BRAND_NAME": "StarHub",
        "BILL_PERIOD_LABEL": format_noa_date(bill_day),
        "CUSTOMER_NAME": row["Company Name (auto)"].strip().upper(),
        "BILLING_NAME": row["Company Name (auto)"].strip(),
        "ACCOUNT_NUMBER": f"1.{rng.randint(11000000, 19999999)}K",
        "ADDRESS_LINE_1": street,
        "ADDRESS_LINE_2": unit,
        "POSTAL_CODE": postal or "000000",
        "BILL_NUMBER": f"{rng.randint(1000000000000000, 1999999999999999)}",
        "DATE_OF_BILL": sg_short_date(bill_day),
        "DUE_DATE": sg_short_date(due),
        "PAYMENT_CODE": str(rng.randint(1000000, 9999999)),
        "GST_REG_NO": "M2-0092055-C",
        "PREVIOUS_BALANCE": f"{previous:.2f}",
        "OVERDUE_AMOUNT": f"{overdue:.2f}",
        "CURRENT_CHARGES": f"{current:.2f}",
        "TOTAL_OUTSTANDING": f"{outstanding:.2f}",
        "CURRENT_CHARGES_DUE_DATE": sg_short_date(due),
        "GST_AMOUNT": f"{gst:.2f}",
        "NON_TAXABLE_CHARGES": f"{nontax:.2f}",
        "TOTAL_SAVINGS": f"{savings:.2f}",
        "PAYMENT_CUTOFF_NOTE": (
            f"Payment received on or after {sg_short_date(cutoff)} will not be shown in this statement."
        ),
        "PROMO_PARAGRAPH_1": (
            "Roam on Conexus Mobile Alliance Networks and enjoy special rates in Hong Kong, "
            "Korea, Taiwan, Indonesia, Philippines or Japan. Details at www.starhub.com/roaming. "
            "Terms and Conditions apply."
        ),
        "PROMO_PARAGRAPH_2": (
            "StarHub TV on Mobile lets you watch sports and news on the go. "
            "SMS &lt;LiveTV&gt; to 6757 to subscribe or visit www.starhub.com/tv."
        ),
        "REMITTANCE_LINE_1": "Robinson Road Post Office",
        "REMITTANCE_LINE_2": "P. O. Box No. 61",
        "REMITTANCE_LINE_3": "Singapore 900131",
        "COMPANY_LETTERHEAD": "StarHub Ltd 51 Cuppage Road #07-00 StarHub Centre Singapore 229469",
        "ENQUIRY_PHONE": "(65) 6820 1637",
        "ENQUIRY_EMAIL": "customerservice@starhub.com",
        "REGISTRATION_NO": "199802208E",
        "PAGE_COUNT": "1",
    }
    template = (ROOT / "telecom" / "bill.html").read_text(encoding="utf-8")
    html = fill_bill(template, fields)
    css = (ROOT / "telecom" / "bill.css").read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="bill.css">',
        f"<style>{css}</style>",
    )
    logo = (ROOT / "telecom" / "starhub-logo.svg").read_bytes()
    logo_b64 = base64.b64encode(logo).decode("ascii")
    html = html.replace(
        'src="starhub-logo.svg"',
        f'src="data:image/svg+xml;base64,{logo_b64}"',
    )
    return html


def build_investment_html(row: dict[str, str]) -> str:
    rng = seeded_rng(row["Case ID"], row["Filename"], "investment")
    stmt = build_statement(rng, period_end=month_end(DOC_YEAR, DOC_MONTH))
    home = (row.get("Residential Address (auto)") or "").strip()
    street, unit, postal = split_address(home or row["Company Address (auto)"])
    stmt.name = row["Individual Name (auto)"].strip().upper()
    stmt.address = make_address(street, unit, postal or "000000")
    return render_statement(stmt)


def load_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate optional-document PDFs (company NOA, utility bills, investments)."
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help="Optional Documents CSV path",
    )
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        default=ROOT / "optional_docs_output",
        help="Output directory (default: ./optional_docs_output)",
    )
    parser.add_argument(
        "--types",
        default="noa,utility,telecom,investment",
        help="Comma-separated: noa, utility, telecom, investment",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be written without generating PDFs",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    wanted = {part.strip().lower() for part in args.types.split(",") if part.strip()}
    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    rows = load_rows(args.csv)
    jobs: list[tuple[str, dict[str, str]]] = []
    skipped: list[str] = []
    for row in rows:
        kind = classify(row)
        if kind is None:
            skipped.append(f"{row['Filename']} ({row['Document Type']})")
            continue
        if kind not in wanted:
            continue
        jobs.append((kind, row))

    print(f"CSV: {args.csv}")
    print(f"Jobs: {len(jobs)}  (noa={sum(k=='noa' for k,_ in jobs)}, "
          f"utility={sum(k=='utility' for k,_ in jobs)}, "
          f"telecom={sum(k=='telecom' for k,_ in jobs)}, "
          f"investment={sum(k=='investment' for k,_ in jobs)})")
    if skipped:
        print(f"Unmapped document types skipped: {len(skipped)}")

    if args.dry_run:
        for kind, row in jobs:
            print(f"  [{kind:11}] {row['Filename']:42} {row['Company Name (auto)']}")
        return

    args.out.mkdir(parents=True, exist_ok=True)
    for kind, row in jobs:
        dest = args.out / row["Filename"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if kind == "noa":
            html = apply_placeholders(bundled_html(), noa_values(row))
        elif kind == "utility":
            html = build_utility_html(row)
        elif kind == "telecom":
            html = build_telecom_html(row)
        else:
            html = build_investment_html(row)
        html_to_pdf(html, dest)
        print(f"Wrote {dest}")

    print(f"Done. {len(jobs)} PDFs in {args.out}")


if __name__ == "__main__":
    main()
