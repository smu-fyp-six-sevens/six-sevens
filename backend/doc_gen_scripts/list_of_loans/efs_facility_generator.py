"""
Generates an Enterprise Financing Scheme (EFS) Letter of Offer PDF — the
government-backed SME loan scheme, administered via a Participating
Financial Institution. Real-world fields: scheme name, government risk-share
percentage, facility amount, tenure, interest rate, security.
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


def generate_efs_letter(
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
    scheme_name: str = "Enterprise Financing Scheme – SME Working Capital Loan",
    facility_amount: float,
    government_risk_share_pct: int = 70,
    tenure_years: int = 5,
    interest_rate_pa: float = 6.5,
    purpose: str = "Working capital for business operations",
    security: str = "Unsecured, backed by Enterprise Singapore risk-share",
) -> dict:
    s = build_styles()
    story = []

    story += letterhead(bank_name, bank_address_lines, ref_no, offer_date)
    story += addressee_block(
        company_name, company_address_lines + [f"UEN: {company_uen}"], director_name
    )

    story.append(Paragraph(
        f"<b>Letter of Offer — {scheme_name}</b>", s["heading"]
    ))
    story.append(Paragraph(
        "We are pleased to inform you that your company's application for financing under the "
        "above scheme has been approved, subject to the terms and conditions set out below. "
        "This approval remains subject to final verification of supporting documents, execution "
        "of the Bank's standard facility agreement, and confirmation of eligibility under the "
        "relevant Enterprise Financing Scheme guidelines.", s["body"],
    ))

    fi_risk_share = 100 - government_risk_share_pct
    story.append(terms_table([
        ("Borrower", company_name),
        ("Scheme", scheme_name),
        ("Facility Type", "Term Loan under Enterprise Financing Scheme"),
        ("Facility Amount", format_currency(facility_amount)),
        ("Purpose", purpose),
        ("Tenure", f"{tenure_years} years"),
        ("Interest Rate", f"{interest_rate_pa:.2f}% per annum"),
        ("Government Risk Share", f"{government_risk_share_pct}%"),
        ("Participating FI Risk Share", f"{fi_risk_share}%"),
        ("Processing Fee", "1.00% of the facility amount, subject to minimum fee and eligibility checks"),
        ("Scheme Conditions", "Subject to prevailing Enterprise Singapore criteria and the Bank's internal credit approval"),
        ("Security", security),
        ("Validity of Offer", "30 days from the date of this letter"),
    ]))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("<b>Terms and Conditions</b>", s["heading"]))
    story.append(terms_and_conditions([
        "This facility is granted subject to the prevailing terms of the Enterprise Financing "
        "Scheme as administered by Enterprise Singapore, which may be varied from time to time.",
        "The Borrower shall utilise the facility strictly for the stated purpose.",
        "The Borrower shall promptly provide any additional information or documents requested "
        "by the Bank or Enterprise Singapore to support scheme administration.",
        "Any Government risk share is subject to the scheme terms, actual drawdown, and "
        "satisfaction of all eligibility criteria at the time of disbursement.",
        "The Bank reserves the right to review, suspend, or cancel the facility in the event "
        "of a material adverse change in the Borrower's financial condition.",
        "All amounts owing under this facility shall become immediately due and payable upon "
        "an event of default, as defined in the Bank's standard banking facility terms.",
        "This offer is governed by the laws of the Republic of Singapore.",
    ]))

    story += signature_block(bank_name, "Tan Mei Ling", "Assistant Vice President, SME Banking", director_name, seed=ref_no, offer_date=offer_date)

    meta = build_pdf(output_path, story)
    return {
        "output_path": output_path,
        "facility_amount": facility_amount,
        "government_risk_share_pct": government_risk_share_pct,
        "scheme_name": scheme_name,
    }


if __name__ == "__main__":
    out_dir = Path.cwd() / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    result = generate_efs_letter(
        str(out_dir / "C010_EFS_LoanFacility.pdf"),
        bank_name="Meridian Bank",
        bank_address_lines=["25 Central Road, Meridian Centre", "Singapore 099123"],
        company_name="Meridian Consulting Group Pte Ltd",
        company_address_lines=["3 Sengkang Central, #11-08", "Singapore 544888"],
        company_uen="202090123J",
        director_name="Goh Ka Yee",
        ref_no="EFS/2026/003841",
        offer_date=date(2026, 3, 15),
        facility_amount=350000.00,
    )
    print("EFS letter:", result)
