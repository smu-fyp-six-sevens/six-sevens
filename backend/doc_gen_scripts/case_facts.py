"""
case_facts.py — one deterministic source of company facts shared by every generator.

Each generator was previously seeding its own share capital, ownership split,
incorporation date and corporate secretary. Because the draw order differed between
scripts, the same case could end up with SGD 750,000 issued capital in the ACRA
profile and SGD 250,000 in the constitution — an accidental cross-document mismatch
that would poison the clean baseline.

The v9 mastersheet now carries share structure as authoritative columns rather than
leaving it to be invented: Case Registry has Incorporation Date, Share Class, Total
Issued Shares (auto), Par Value (SGD) and Issued & Paid-Up Capital (SGD) (auto); each
Individuals row has Shares Held and Shareholding % (auto). company_facts() reads those
directly instead of drawing them from a seeded RNG, and only falls back to invention
when a mastersheet cell is blank (e.g. an older sheet, or a hand-added row still
missing its auto-computed values). Facts with no mastersheet column at all — the
corporate secretarial firm, the secretary's identity, appointment timing — are still
invented from the seeded RNG so they stay consistent across documents.

Every generator should import company_facts() and use these values rather than
re-seeding its own.
"""

from __future__ import annotations

import hashlib
import random
import re
import warnings
from datetime import date, datetime, timedelta
from typing import Any

# The mastersheet has data-validation dropdowns (e.g. Fraud Injected? Y/N) that
# openpyxl can read fine but warns about on every single load_workbook() call --
# every generator in this package loads that same file, so silence it once, here,
# rather than at each of the ~10 call sites.
warnings.filterwarnings("ignore", message="Data Validation extension is not supported")

CORP_SEC_FIRMS = [
    ("Rivergate Corporate Services Pte. Ltd.", "8 Cross Street, #24-03 Manulife Tower, Singapore 048424"),
    ("Ashworth Corporate Advisory Pte. Ltd.", "137 Cecil Street, #09-01 Cecil Building, Singapore 069537"),
    ("Merlion Company Secretaries Pte. Ltd.", "20 Collyer Quay, #17-04, Singapore 049319"),
    ("Kallang Corporate Management Pte. Ltd.", "3 Church Street, #12-06 Samsung Hub, Singapore 049483"),
]

SECRETARY_NAMES = [
    "Chan Siew Peng",
    "Rajeswari d/o Muthusamy",
    "Goh Mei Ling",
    "Nurul Aisyah binte Rahman",
    "Wong Kah Chun",
    "Teo Li Xuan",
]

SHARE_SPLITS = [(60, 40), (70, 30), (75, 25), (51, 49), (80, 20), (65, 35)]
SHARE_CAPITALS = [100_000, 250_000, 500_000, 750_000, 1_000_000]


def seeded_rng(key: str) -> random.Random:
    """SHA-256 seeding, matching the convention used across docgen."""
    return random.Random(int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16))


def as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip()).date()
        except ValueError:
            return None
    return None


def company_facts(case: dict[str, Any], people: list[dict[str, Any]]) -> dict[str, Any]:
    """Facts that must agree across every document generated for a case."""
    case_id = str(case["Case ID"])
    rng = seeded_rng(f"{case_id}|company-facts")

    application_date = as_date(case.get("Application Date")) or date(2026, 1, 1)
    uen = str(case.get("Company UEN / Reg No.") or "")

    incorporation_date = as_date(case.get("Incorporation Date"))
    if incorporation_date is None:
        inc_year = int(uen[:4]) if re.match(r"^(19|20)\d{2}", uen) else application_date.year - 6
        incorporation_date = date(inc_year, rng.randint(1, 12), rng.randint(1, 28))

    total_shares = case.get("Total Issued Shares (auto)")
    capital = case.get("Issued & Paid-Up Capital (SGD) (auto)")
    par_value = case.get("Par Value (SGD)")
    share_class = str(case.get("Share Class") or "Ordinary")
    if total_shares is None or capital is None:
        capital = SHARE_CAPITALS[rng.randrange(len(SHARE_CAPITALS))]
        total_shares = capital
        par_value = 1.0
    else:
        total_shares = int(total_shares)
        capital = float(capital)
        par_value = float(par_value) if par_value is not None else 1.0

    firm, firm_address = CORP_SEC_FIRMS[rng.randrange(len(CORP_SEC_FIRMS))]
    secretary = SECRETARY_NAMES[rng.randrange(len(SECRETARY_NAMES))]
    secretary_id = f"S{rng.randint(6000000, 8999999)}{rng.choice('ABCDEFGHIJZ')}"
    secretary_appointed = incorporation_date + timedelta(days=rng.randint(0, 90))
    # Appointments are on record by the time the case's documents are issued (a few weeks before the
    # application), and never before incorporation.
    latest_appointment = max(incorporation_date, application_date - timedelta(days=30))
    secretary_appointed = min(secretary_appointed, latest_appointment)

    holders = []
    for i, person in enumerate(people):
        shares_held = person.get("Shares Held")
        pct_fraction = person.get("Shareholding % (auto)")
        if shares_held is None:
            fallback_split = SHARE_SPLITS[rng.randrange(len(SHARE_SPLITS))]
            pct = fallback_split[i] if i < len(fallback_split) else 0
            shares_held = round(total_shares * pct / 100)
        else:
            shares_held = int(shares_held)
            pct = round(pct_fraction * 100) if pct_fraction is not None else round(
                shares_held / total_shares * 100
            )

        holders.append(
            {
                "name": person.get("Name (auto/fill)", ""),
                "id": person.get("NRIC/Passport No. (auto/fill)", ""),
                "nationality": person.get("Nationality", ""),
                "address": person.get("Residential Address", ""),
                "role": person.get("Role", ""),
                "shares": shares_held,
                "pct": pct,
                "appointed": min(
                    incorporation_date + timedelta(days=0 if i == 0 else rng.randint(180, 1200)),
                    latest_appointment,
                ),
            }
        )

    return {
        "case_id": case_id,
        "company_name": case.get("Company Name", ""),
        "uen": uen,
        "company_type": case.get("Company Type", ""),
        "registered_address": case.get("Registered Address", ""),
        "application_date": application_date,
        "incorporation_date": incorporation_date,
        "share_class": share_class,
        "share_capital": capital,
        "total_shares": total_shares,
        "par_value": par_value,
        "holders": holders,
        "firm": firm,
        "firm_address": firm_address,
        "secretary": secretary,
        "secretary_id": secretary_id,
        "secretary_appointed": secretary_appointed,
    }
