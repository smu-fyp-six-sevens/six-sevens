"""
Generates an Overdraft Facility Letter of Offer PDF. Real-world fields:
facility limit, interest rate (typically Prime/reference rate + margin),
annual review date, security, commitment fee.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from loan_letter_common import (
    letterhead, addressee_block, terms_table, terms_and_conditions,
    signature_block, build_pdf, build_styles, format_currency,
)
from reportlab.platypus import Paragraph, Spacer
from reportlab.lib.units import mm


def generate_overdraft_letter(
    output_path: str,
    *,
    bank_name: str,
    bank_address_lines: list,
    company_name: str,
    company_address_lines: list,
    company_uen: str,
    director_name: str,
    ref_no: str,
    offer_date: date,
    facility_limit: float,
    reference_rate_name: str = "Bank's Prime Rate",
    margin_pct: float = 2.5,
    reference_rate_pct: float = 5.5,
    review_date: date,
    purpose: str = "General working capital purposes",
    security: str = "Fixed and floating charge over the Borrower's assets",
    commitment_fee_pct: float = 0.5,
) -> dict:
    s = build_styles()
    story = []

    story += letterhead(bank_name, bank_address_lines, ref_no, offer_date)
    story += addressee_block(
        company_name, company_address_lines + [f"UEN: {company_uen}"], director_name
    )

    story.append(Paragraph("<b>Letter of Offer — Overdraft Facility</b>", s["heading"]))
    story.append(Paragraph(
        "We are pleased to offer your company an overdraft facility on the terms and "
        "conditions set out below. This facility is repayable on demand and is subject to "
        "annual review by the Bank, including continued compliance with the approved "
        "borrowing limits and security requirements.", s["body"],
    ))

    all_in_rate = reference_rate_pct + margin_pct
    story.append(terms_table([
        ("Borrower", company_name),
        ("Facility Type", "Overdraft"),
        ("Facility Limit", format_currency(facility_limit)),
        ("Purpose", purpose),
        ("Interest Rate", f"{reference_rate_name} + {margin_pct:.2f}% (currently {all_in_rate:.2f}% p.a.)"),
        ("Interest Basis", "Computed daily on outstanding balance, charged monthly in arrears"),
        ("Commitment Fee", f"{commitment_fee_pct:.2f}% p.a. on the unutilised facility limit"),
        ("Clean-Up Requirement", "Outstanding balance to be reduced to nil for at least 30 consecutive days each year"),
        ("Availability", "Available for 12 months from the date of acceptance, subject to annual renewal"),
        ("Review Date", review_date.strftime("%d %B %Y")),
        ("Security", security),
        ("Validity of Offer", "30 days from the date of this letter"),
    ]))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("<b>Terms and Conditions</b>", s["heading"]))
    story.append(terms_and_conditions([
        "This facility is repayable on demand at any time at the Bank's sole discretion, "
        "notwithstanding the Review Date stated above.",
        "The Borrower shall not permit the outstanding balance to exceed the Facility Limit "
        "at any time without the Bank's prior written consent.",
        "Interest rates are subject to change in accordance with movements in the Bank's "
        "Prime Rate or other reference rate, with notice to the Borrower.",
        "A clean-up period may be required during which the outstanding balance is reduced to "
        "nil for a consecutive 30-day period each year.",
        "The Bank reserves the right to review, reduce, suspend, or cancel the facility at "
        "any time, including upon annual review.",
        "This offer is governed by the laws of the Republic of Singapore.",
    ]))

    story += signature_block(bank_name, "Rajesh Kumar", "Vice President, Corporate Banking", director_name)

    build_pdf(output_path, story)
    return {
        "output_path": output_path,
        "facility_limit": facility_limit,
        "all_in_rate_pct": all_in_rate,
        "review_date": review_date.isoformat(),
    }


if __name__ == "__main__":
    out_dir = Path.cwd() / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    result = generate_overdraft_letter(
        str(out_dir / "C004_Overdraft_LoanFacility.pdf"),
        bank_name="Meridian Bank",
        bank_address_lines=["25 Central Road, Meridian Centre", "Singapore 099123"],
        company_name="BlueWave Marine Services Pte Ltd",
        company_address_lines=["5 Toa Payoh Central, #03-21", "Singapore 319261"],
        company_uen="201745678D",
        director_name="Ong Zhi Hao",
        ref_no="OD/2026/007729",
        offer_date=date(2026, 2, 5),
        facility_limit=150000.00,
        review_date=date(2027, 2, 5),
    )
    print("Overdraft letter:", result)
