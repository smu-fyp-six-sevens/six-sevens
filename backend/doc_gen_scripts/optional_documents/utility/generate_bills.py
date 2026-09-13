#!/usr/bin/env python3
"""Fill bill.html with residential electricity bills used as CEO proof of address."""

from __future__ import annotations

import argparse
import json
import random
import re
import shutil
from datetime import date, timedelta
from pathlib import Path


TEMPLATE_PATH = Path(__file__).resolve().parent / "bill.html"
CSS_PATH = Path(__file__).resolve().parent / "bill.css"
PLACEHOLDER = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
          "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

BANKS = [
    "DBS", "OCBC", "UOB", "HSBC",
    "Standard Chartered", "Maybank", "CIMB",
]

ACCOUNT_TYPES = ["Residential"]

# name, street, unit, postal, home profile, company they run
RESIDENTS = [
    ("Tan Wei Ming", "18 Ardmore Park", "#15-02", "259960", "condo", "Apex Logistics Pte Ltd"),
    ("Rachel Ong", "8 Nathan Road", "#12-03", "248743", "condo", "Harbourview Trading Pte Ltd"),
    ("Suresh Nair", "15 Jalan Merah Saga", "", "278103", "landed", "Nexora Manufacturing Pte Ltd"),
    ("Amanda Lim", "31 Amber Road", "#16-02", "439941", "condo", "Eastgate Food Services Pte Ltd"),
    ("Jason Chew", "1 Cairnhill Road", "#18-02", "229719", "condo", "Meridian Capital Advisory Pte Ltd"),
    ("Priya Menon", "27 Namly Crescent", "", "267548", "landed", "Pacific Rim Supplies Pte Ltd"),
    ("David Koh", "18 Meyer Road", "#22-05", "437846", "condo", "Lumina Retail Group Pte Ltd"),
    ("Elaine Goh", "6 Leedon Heights", "#21-03", "267954", "condo", "Northstar Engineering Pte Ltd"),
    ("Christopher Yap", "12 Nassim Road", "#06-01", "258378", "condo", "Cedarwood Holdings Pte Ltd"),
    ("Melissa Tan", "3 Sentosa Cove Way", "#04-02", "098136", "landed", "Orion Marine Services Pte Ltd"),
    ("Aaron Teo", "10 Shelford Road", "#04-02", "288353", "condo", "Summit Healthcare Pte Ltd"),
    ("Nurul Hassan", "16 Eng Kong Terrace", "", "598988", "landed", "Vantage Construction Pte Ltd"),
    ("Jeanette Foo", "3 Draycott Park", "#10-01", "259382", "condo", "Golden Gate Imports Pte Ltd"),
    ("Tan Wei Jie", "1 Grange Road", "#23-08", "239693", "condo", "Horizon IT Solutions Pte Ltd"),
    ("Lim Hui Min", "8 Bin Tong Park", "", "269797", "landed", "Redwood Packaging Pte Ltd"),
    ("Marcus Ong", "9 Tomlinson Road", "#11-04", "247985", "condo", "Atlas Facilities Pte Ltd"),
    ("Clara Ng", "1 Balmoral Road", "#14-03", "259796", "condo", "Saffron Hospitality Pte Ltd"),
    ("Daniel Wong", "7 Ridley Park", "", "248480", "landed", "Peninsula Warehousing Pte Ltd"),
    ("Hannah Lee", "18 Mount Sinai Rise", "#07-02", "276906", "condo", "Evergreen Chemicals Pte Ltd"),
    ("Benjamin Chua", "5 Belmont Road", "#08-01", "269856", "condo", "Brightpath Education Pte Ltd"),
]


def fmt_date(value: date) -> str:
    return f"{value.day:02d} {MONTH_NAMES[value.month - 1]} {value.year}"


def fmt_money(value: float) -> str:
    return f"{value:,.2f}"


def pick_period(rng: random.Random) -> dict[str, date]:
    bill_year = rng.randint(2024, 2026)
    bill_month = rng.randint(1, 12)
    bill_day = rng.choice([1, 1, 1, 2, 3, 5])
    bill_date = date(bill_year, bill_month, bill_day)

    period_length = rng.choice([28, 29, 30, 31, 31])
    period_end = bill_date - timedelta(days=rng.randint(10, 16))
    period_start = period_end - timedelta(days=period_length - 1)

    due_date = bill_date + timedelta(days=rng.choice([12, 13, 14, 15]))
    deduction_date = due_date + timedelta(days=rng.choice([0, 1, 2, 3]))
    payment_received_date = bill_date - timedelta(days=rng.randint(12, 20))
    contract_end = date(
        bill_date.year + rng.randint(2, 6),
        rng.randint(1, 12),
        rng.choice([1, 5, 10, 15, 20, 28]),
    )

    return {
        "bill_date": bill_date,
        "period_start": period_start,
        "period_end": period_end,
        "due_date": due_date,
        "deduction_date": deduction_date,
        "payment_received_date": payment_received_date,
        "contract_end": contract_end,
    }


def month_labels(end: date) -> list[str]:
    labels = []
    year = end.year
    month = end.month
    for _ in range(6):
        labels.append(MONTHS[month - 1])
        month -= 1
        if month == 0:
            month = 12
            year -= 1
    labels.reverse()
    return labels


def consumption_series(rng: random.Random, profile: str) -> list[int]:
    if profile == "condo":
        base = rng.randint(380, 820)
        swing = 45
    elif profile == "landed":
        base = rng.randint(900, 1800)
        swing = 90
    else:
        base = rng.randint(500, 1100)
        swing = 60

    values = []
    current = float(base)
    for _ in range(6):
        current *= rng.uniform(0.90, 1.12)
        current += rng.uniform(-swing, swing)
        values.append(max(180, int(round(current))))
    return values


def build_record(rng: random.Random, index: int) -> dict[str, str]:
    name, street, unit, postal, profile, company = rng.choice(RESIDENTS)
    dates = pick_period(rng)
    account_type = rng.choice(ACCOUNT_TYPES)
    usage = consumption_series(rng, profile)
    current_kwh = usage[-1]
    months = month_labels(dates["period_end"])

    rate = rng.choice([0.2599, 0.2688, 0.2799, 0.2899, 0.2998, 0.3097])
    current_charges = round(current_kwh * rate, 2)

    scenario = rng.choice(["paid", "paid", "paid", "outstanding", "first"])
    previous_bill = round(usage[-2] * rate * rng.uniform(0.94, 1.06), 2)
    if scenario == "paid":
        brought_forward = previous_bill
        payment_received = brought_forward
        outstanding = 0.0
    elif scenario == "outstanding":
        brought_forward = previous_bill
        payment_received = round(brought_forward * rng.uniform(0.40, 0.80), 2)
        outstanding = round(brought_forward - payment_received, 2)
    else:
        brought_forward = 0.0
        payment_received = 0.0
        outstanding = 0.0

    total = round(outstanding + current_charges, 2)
    if profile == "landed":
        deposit = rng.choice(["$400.00", "$500.00", "$600.00", "$800.00"])
    else:
        deposit = rng.choice(["$200.00", "$250.00", "$300.00", "$400.00"])
    bank = rng.choice(BANKS)

    premise_street, premise_unit, premise_postal = street, unit, postal

    fields = {
        "BILL_DATE": fmt_date(dates["bill_date"]),
        "ACCOUNT_NUMBER": f"{rng.randint(21000000, 28999999)}",
        "STATEMENT_NUMBER": f"{rng.randint(1000000, 1999999)}",
        "BILLING_NAME": name,
        "BILLING_ADDRESS_LINE_1": street,
        "BILLING_ADDRESS_LINE_2": unit,
        "BILLING_POSTAL_CODE": postal,
        "TOTAL_AMOUNT": fmt_money(total),
        "PAYMENT_DUE_DATE": fmt_date(dates["due_date"]),
        "PREMISE_ADDRESS_LINE_1": premise_street,
        "PREMISE_ADDRESS_LINE_2": premise_unit,
        "PREMISE_POSTAL_CODE": premise_postal,
        "SP_ACCOUNT_NUMBER": f"93{rng.randint(10000000, 99999999)}",
        "BILLING_PERIOD_START": fmt_date(dates["period_start"]),
        "BILLING_PERIOD_END": fmt_date(dates["period_end"]),
        "ACCOUNT_TYPE": account_type,
        "SECURITY_DEPOSIT": deposit,
        "CONTRACT_END_DATE": fmt_date(dates["contract_end"]),
        "CURRENT_CONSUMPTION": str(current_kwh),
        "BALANCE_BROUGHT_FORWARD": fmt_money(brought_forward),
        "PAYMENT_RECEIVED_DATE": fmt_date(dates["payment_received_date"]),
        "PAYMENT_RECEIVED": fmt_money(payment_received),
        "OUTSTANDING_BALANCE": fmt_money(outstanding),
        "CURRENT_MONTH_CHARGES": fmt_money(current_charges),
        "BANK_NAME": bank,
        "BANK_ACCOUNT_LAST4": f"{rng.randint(0, 9999):04d}",
        "PAYMENT_DEDUCTION_DATE": fmt_date(dates["deduction_date"]),
        "CURRENT_MONTH": months[-1],
        "CURRENT_MONTH_KWH": str(current_kwh),
    }

    for i, (label, kwh) in enumerate(zip(months[:-1], usage[:-1]), start=1):
        fields[f"MONTH_{i}"] = label
        fields[f"MONTH_{i}_KWH"] = str(kwh)

    fields["_index"] = str(index)
    fields["_profile"] = profile
    fields["_scenario"] = scenario
    fields["_rate"] = f"{rate:.4f}"
    fields["_company"] = company
    return fields


def fill_template(template: str, fields: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in fields:
            raise KeyError(f"No value generated for {{{{{key}}}}}")
        return fields[key]

    html = PLACEHOLDER.sub(replace, template)
    return html.replace(
        "Electricity Bill - Mock Document",
        f"Electricity Bill - {fields['BILLING_NAME']}",
    )


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def write_index(output_dir: Path, bills: list[dict[str, str]]) -> None:
    rows = []
    for bill in bills:
        filename = bill["_filename"]
        rows.append(
            "<tr>"
            f"<td><a href=\"{filename}\">{filename}</a></td>"
            f"<td>{bill['BILLING_NAME']}</td>"
            f"<td>{bill['_company']}</td>"
            f"<td>{bill['BILLING_ADDRESS_LINE_1']} {bill['BILLING_ADDRESS_LINE_2']}</td>"
            f"<td>{bill['BILL_DATE']}</td>"
            f"<td>${bill['TOTAL_AMOUNT']}</td>"
            f"<td>{bill['CURRENT_CONSUMPTION']} kWh</td>"
            "</tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Generated Electricity Bills</title>
    <style>
        body {{ font-family: Helvetica, Arial, sans-serif; margin: 40px; color: #222; }}
        table {{ border-collapse: collapse; width: 100%; }}
        th, td {{ padding: 10px 12px; border-bottom: 1px solid #ddd; text-align: left; }}
        th {{ font-size: 12px; letter-spacing: 0.4px; }}
        a {{ color: #5c2a16; }}
    </style>
</head>
<body>
    <h1>Residential Electricity Bills</h1>
    <p>{len(bills)} CEO proof-of-residence bills generated from bill.html</p>
    <table>
        <thead>
            <tr>
                <th>File</th>
                <th>Resident</th>
                <th>Company</th>
                <th>Address</th>
                <th>Bill Date</th>
                <th>Amount</th>
                <th>Usage</th>
            </tr>
        </thead>
        <tbody>
            {''.join(rows)}
        </tbody>
    </table>
</body>
</html>
"""
    (output_dir / "index.html").write_text(html, encoding="utf-8")


def generate(count: int, output_dir: Path, seed: int | None) -> list[dict[str, str]]:
    rng = random.Random(seed)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    output_dir.mkdir(parents=True, exist_ok=True)

    for stale in output_dir.glob("bill_*.html"):
        stale.unlink()
    for stale_name in ("index.html", "manifest.json"):
        stale = output_dir / stale_name
        if stale.exists():
            stale.unlink()

    shutil.copy2(CSS_PATH, output_dir / "bill.css")

    used_names: set[str] = set()
    bills: list[dict[str, str]] = []

    for index in range(1, count + 1):
        fields = build_record(rng, index)
        while fields["BILLING_NAME"] in used_names and len(used_names) < len(RESIDENTS):
            fields = build_record(rng, index)
        used_names.add(fields["BILLING_NAME"])

        filename = f"bill_{index:03d}_{slug(fields['BILLING_NAME'])}.html"
        fields["_filename"] = filename
        html = fill_template(template, fields)
        html = html.replace('href="bill.css"', 'href="bill.css"')
        (output_dir / filename).write_text(html, encoding="utf-8")
        bills.append(fields)

    public_keys = [key for key in bills[0] if not key.startswith("_")]
    manifest = [
        {key: bill[key] for key in public_keys} | {
            "filename": bill["_filename"],
            "profile": bill["_profile"],
            "scenario": bill["_scenario"],
            "rate": bill["_rate"],
            "company": bill["_company"],
        }
        for bill in bills
    ]
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    write_index(output_dir, bills)
    return bills


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate residential electricity bills as CEO proof of address."
    )
    parser.add_argument(
        "-n", "--count",
        type=int,
        default=10,
        help="Number of bills to generate (default: 10)",
    )
    parser.add_argument(
        "-o", "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "output",
        help="Output directory (default: ./output)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Optional random seed for repeatable output",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.count < 1:
        raise SystemExit("--count must be at least 1")
    if not TEMPLATE_PATH.exists() or not CSS_PATH.exists():
        raise SystemExit("bill.html and bill.css must be in the same folder as this script.")

    bills = generate(args.count, args.out, args.seed)
    print(f"Generated {len(bills)} bills in {args.out}")
    print(f"Open {args.out / 'index.html'} to browse them.")


if __name__ == "__main__":
    main()
