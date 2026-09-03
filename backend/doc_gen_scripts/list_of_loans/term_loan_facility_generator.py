"""
Generates a Term Loan Facility Letter of Offer PDF. Real-world fields:
principal amount, tenure, interest rate, amortized monthly instalment,
security. The monthly instalment is computed via standard amortization —
this value should be reused as the recurring debit injected into this
case's Bank Statement generator, so the two documents reconcile by
construction rather than by coincidence.
"""

from datetime import date

from loan_letter_common import (
    letterhead, addressee_block, terms_table, terms_and_conditions,
    signature_block, build_pdf, build_styles, format_currency,
)
from reportlab.platypus import Paragraph, Spacer
from reportlab.lib.units import mm


def compute_monthly_instalment(principal: float, annual_rate_pct: float, tenure_months: int) -> float:
    """Standard amortization formula: M = P * r(1+r)^n / ((1+r)^n - 1)."""
    r = (annual_rate_pct / 100) / 12
    if r == 0:
        return round(principal / tenure_months, 2)
    factor = (1 + r) ** tenure_months
    return round(principal * r * factor / (factor - 1), 2)


def generate_term_loan_letter(
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
    principal_amount: float,
    tenure_months: int = 60,
    interest_rate_pa: float = 6.0,
    purpose: str = "Acquisition of business equipment and expansion of operations",
    security: str = "Fixed and floating charge over the Borrower's assets, "
                     "joint and several personal guarantee of all directors",
) -> dict:
    s = build_styles()
    story = []

    monthly_instalment = compute_monthly_instalment(principal_amount, interest_rate_pa, tenure_months)

    story += letterhead(bank_name, bank_address_lines, ref_no, offer_date)
    story += addressee_block(
        company_name, company_address_lines + [f"UEN: {company_uen}"], director_name
    )

    story.append(Paragraph("<b>Letter of Offer — Term Loan Facility</b>", s["heading"]))
    story.append(Paragraph(
        "We are pleased to offer your company a term loan facility on the terms and "
        "conditions set out below. This offer is based on the information provided in the "
        "credit application and remains subject to completion of the Bank's standard due "
        "diligence and documentation.", s["body"],
    ))

    story.append(terms_table([
        ("Borrower", company_name),
        ("Facility Type", "Term Loan"),
        ("Principal Amount", format_currency(principal_amount)),
        ("Purpose", purpose),
        ("Tenure", f"{tenure_months} months"),
        ("Interest Rate", f"{interest_rate_pa:.2f}% per annum, fixed"),
        ("Repayment", f"{tenure_months} equal monthly instalments of SGD {monthly_instalment:,.2f}"),
        ("First Repayment Date", "One month after first drawdown, on the corresponding day of each month"),
        ("Processing Fee", "1.00% of the principal amount, deducted on first drawdown"),
        ("Late Interest", "2.00% per annum above the contracted interest rate on overdue amounts"),
        ("Security", security),
        ("Validity of Offer", "30 days from the date of this letter"),
    ]))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("<b>Terms and Conditions</b>", s["heading"]))
    story.append(terms_and_conditions([
        "The Borrower shall repay the facility by way of the monthly instalments stated above, "
        "commencing one month after the date of first drawdown and continuing until the facility "
        "is fully amortised.",
        "Any prepayment, whether full or partial, shall be subject to the Bank's prevailing "
        "prepayment fee schedule and written confirmation by the Bank.",
        "The Borrower shall maintain all security interests requested by the Bank and shall "
        "execute all supplemental documents reasonably required to perfect such security.",
        "The Bank reserves the right to review, suspend, or cancel any undrawn portion of the "
        "facility in the event of a material adverse change in the Borrower's financial "
        "condition.",
        "This offer is governed by the laws of the Republic of Singapore.",
    ]))

    story += signature_block(bank_name, "David Ng", "Senior Relationship Manager, Business Banking", director_name)

    build_pdf(output_path, story)
    return {
        "output_path": output_path,
        "principal_amount": principal_amount,
        "tenure_months": tenure_months,
        "monthly_instalment": monthly_instalment,
    }


if __name__ == "__main__":
    out_dir = Path.cwd() / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    result = generate_term_loan_letter(
        str(out_dir / "C009_TermLoan_LoanFacility.pdf"),
        bank_name="Meridian Bank",
        bank_address_lines=["25 Central Road, Meridian Centre", "Singapore 099123"],
        company_name="Titan Steel Works Pte Ltd",
        company_address_lines=["112 Clementi Street 13, #02-45", "Singapore 120112"],
        company_uen="201989012I",
        director_name="Aisha binte Rahman",
        ref_no="TL/2026/005512",
        offer_date=date(2026, 2, 25),
        principal_amount=800000.00,
        tenure_months=60,
        interest_rate_pa=6.0,
    )
    print("Term loan letter:", result)
    print(f"-> Monthly instalment (SGD {result['monthly_instalment']:,.2f}) — reuse this exact figure "
          f"as the recurring debit in this case's Bank Statement generator for reconciliation.")
