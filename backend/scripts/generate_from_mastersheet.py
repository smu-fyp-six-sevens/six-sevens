"""
Parse `v9_document_mastersheet.xlsx` and generate documents for a given case id.
Saves generated PDFs into `documents_generated/<case_id>/`.

Usage (from backend/):
    python -m scripts.generate_from_mastersheet C001

This script depends on `openpyxl` and `pandas` (added to requirements).
"""

from __future__ import annotations

import functools
import re
import sys
from pathlib import Path
from datetime import datetime, timedelta
import openpyxl
import pandas as pd

from doc_gen_scripts.nric_passport.nric_generator import generate_nric_card
from doc_gen_scripts.nric_passport.passport_generator import generate_passport_page
from doc_gen_scripts.payslips.generate_payslips import build_payslip_1, build_payslip_2, build_payslip_3, synthesize_payslip_data
from doc_gen_scripts.corp_source_of_wealth.balance_sheet_income_statement_generator import generate as generate_sow
from doc_gen_scripts.corporate_bank_statement.docgen_bankstatement import (
    generate as generate_bank_statement,
    load_case as load_bank_case,
    build_blocks as build_bank_blocks,
    BankProfile,
    BANK_PROFILES,
)
from doc_gen_scripts.certificate_of_incorporation.coi_generator import generate_certificate_of_incorporation
from doc_gen_scripts.personal_proof_of_residence.utility_bill_generator import generate_utility_bill
from doc_gen_scripts.personal_proof_of_residence.docgen_bankstatement_ocbc_personal import render_ocbc_personal
from doc_gen_scripts.credit_reports.credit_bureau_report_generator import generate_credit_bureau_report
from doc_gen_scripts.company_constitution.docgen_constitution import generate as generate_constitution
from doc_gen_scripts.certificate_of_incumbency.docgen_acra_profile import generate as generate_cert_incumbency
from doc_gen_scripts.fatca_crs.docgen_fatca_crs import generate as generate_fatca_crs
from doc_gen_scripts.case_facts import company_facts, as_date
from doc_gen_scripts.list_of_loans.term_loan_facility_generator import generate_term_loan_letter
from doc_gen_scripts.list_of_loans.efs_facility_generator import generate_efs_letter
from doc_gen_scripts.list_of_loans.overdraft_facility_generator import generate_overdraft_letter

BASE = Path(__file__).resolve().parents[1]
MASTERSHEET = BASE / "doc_gen_scripts" / "v9_document_mastersheet.xlsx"
OUT_DIR = BASE / "documents_generated"

# "OCBC Personal" isn't registered by default in docgen_bankstatement's BANK_PROFILES
# (only the corporate bank profiles are). Register it once here so load_bank_case()
# accepts it as a bank_override when we render a director's personal statement as a
# Proof of Address document (see section 9 below). Reuses the real OCBC logo asset
# that already ships with the corporate bank statement generator.
BANK_PROFILES.setdefault("OCBC Personal", BankProfile(
    name="OCBC",
    legal_name="Oversea-Chinese Banking Corporation Limited",
    address="65 Chulia Street, OCBC Centre, Singapore 049513",
    website="www.ocbc.com",
    footer_reg="Co. Reg. No. 193200032W",
    product="MONTHLY SAVINGS ACCOUNT",
    tagline=("", ""),
    logo_path=str(BASE / "doc_gen_scripts" / "corporate_bank_statement" / "assets" / "ocbc-logo.png"),
    logo_style="plain",
    template="ocbc_personal",
    branch="Orchard Road Branch",
))

# Bank letterhead addresses for the List of Loans facility letters (section 10) —
# a small local lookup rather than reusing BANK_PROFILES, since those letters are an
# unrelated document type from an unrelated (bank-side) sender.
LOAN_BANK_ADDRESSES: dict[str, list[str]] = {
    "DBS Bank": ["12 Marina Boulevard, Marina Bay Financial Centre Tower 3", "Singapore 018982"],
    "UOB": ["80 Raffles Place, UOB Plaza", "Singapore 048624"],
    "OCBC": ["65 Chulia Street, OCBC Centre", "Singapore 049513"],
}
DEFAULT_BANK_ADDRESS = ["1 Marina Boulevard", "Singapore 018989"]


@functools.lru_cache(maxsize=None)
def _openpyxl_all_rows(sheet_name: str) -> tuple[dict, ...]:
    """openpyxl read of one sheet, cached — gives clean `None`s for blank cells
    (unlike the pandas frames above, which turn them into NaN), matching what
    case_facts.company_facts() expects."""
    wb = openpyxl.load_workbook(str(MASTERSHEET), data_only=True)
    ws = wb[sheet_name]
    header = [c.value for c in ws[1]]
    return tuple(
        dict(zip(header, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(r)
    )


def _openpyxl_case_row(sheet_name: str, case_id: str, key: str = "Case ID") -> dict:
    for r in _openpyxl_all_rows(sheet_name):
        if str(r.get(key)) == str(case_id):
            return r
    return {}


def _openpyxl_case_rows(sheet_name: str, case_id: str, key: str = "Case ID") -> list[dict]:
    rows = [r for r in _openpyxl_all_rows(sheet_name) if str(r.get(key)) == str(case_id)]
    rows.sort(key=lambda r: r.get("Person Seq") or 0)
    return rows


def _parse_tenure_months(text: str) -> int:
    """'12 months (revolving)' -> 12, '5 years' -> 60. Falls back to 60 (5 years)
    if the Tenure column can't be parsed."""
    m = re.search(r"(\d+)\s*(month|year)", text, re.IGNORECASE)
    if not m:
        return 60
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * 12 if unit.startswith("year") else n


def _read_sheet(name: str) -> pd.DataFrame:
    xl = pd.ExcelFile(MASTERSHEET)
    if name not in xl.sheet_names:
        return pd.DataFrame()
    return xl.parse(name)


def _ensure_out(case_id: str) -> Path:
    p = OUT_DIR / case_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _get_row_by_case(df: pd.DataFrame, case_id: str) -> dict:
    if df.empty:
        return {}
    # find a column that references the case id (tolerate spaces/case)
    key_cols = [c for c in df.columns if "case" in str(c).lower() and "id" in str(c).lower()]
    if not key_cols:
        # fallback: any column that contains 'case'
        key_cols = [c for c in df.columns if "case" in str(c).lower()]
    if not key_cols:
        return {}
    key = key_cols[0]
    # be permissive about whitespace and types
    series = df[key].astype(str).str.strip()
    row = df[series == str(case_id)]
    if row.empty:
        return {}
    return row.iloc[0].to_dict()


def _parse_date(v):
    """Return a datetime.date or None for various input types (str, Timestamp, date)."""
    if v is None:
        return None
    try:
        return pd.to_datetime(v).date()
    except Exception:
        try:
            # fallback: try parsing ISO
            return datetime.fromisoformat(str(v)).date()
        except Exception:
            return None


def generate_for_case(case_id: str) -> None:
    out = _ensure_out(case_id)

    # 1) Credit report tab
    credit_df = _read_sheet("Credit Reports") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not credit_df.empty:
        for _, r in credit_df.iterrows():
            if str(r.get("Case ID")).strip() != str(case_id):
                continue
            credit_row = r.to_dict()
            try:
                cr_path = out / f"credit_report_{case_id}.pdf"
                generate_credit_bureau_report(
                    str(cr_path),
                    enquiry_number=str(credit_row.get("Filename") or f"CR-{case_id}"),
                    enquiry_date=credit_row.get("Incorporation Date (auto)") or datetime.now().date(),
                    reference=str(credit_row.get("Filename") or case_id),
                    consumer_name=str(credit_row.get("Company Name (auto)") or credit_row.get("Company Name") or "Consumer"),
                    id_type="NRIC",
                    id_number="",
                    date_of_birth=datetime.now().date(),
                    postal_code="",
                    enquiry_type="New Application",
                    product_type="",
                    applicant_type="",
                    earliest_credit_account_date=datetime.now().date(),
                    secured_credit_limit=float(credit_row.get("Overdraft Amount (SGD)") or 0),
                    unsecured_credit_limit=0,
                    exempted_credit_limit=0,
                    debt_management_programme="N",
                    id_theft="N",
                    surname="",
                    first_name="",
                    nationality="",
                    marital_status="",
                    address="",
                )
                print("Wrote", cr_path)
            except Exception as e:
                print("Credit report generation failed:", e)

    # 2) Source of wealth docs - combined financial statements
    sow_df = _read_sheet("Source of Wealth Docs") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not sow_df.empty:
        # Use the new combined generator which produces a single FS PDF per case
        try:
            paths = generate_sow(MASTERSHEET, out, cases=[case_id])
            for p in paths:
                print("Wrote", p)
        except Exception as e:
            print("Source-of-wealth generation failed:", e)

    # 3) Bank statement tab
    bank_df = _read_sheet("Bank Statements") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not bank_df.empty:
        for _, r in bank_df.iterrows():
            if str(r.get("Case ID")).strip() != str(case_id):
                continue
            try:
                bank_name = r.get("Bank Name (auto)") or r.get("Bank Name") or r.get("Bank Name (auto)") or "OCBC"
                out_pdf = generate_bank_statement(MASTERSHEET, case_id, out, bank=bank_name)
                print("Wrote", out_pdf)
            except Exception as e:
                print("Bank statement generation failed:", e)

    # 4) NRIC/Passport/ID documents
    id_df = _read_sheet("NRIC-Passport") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not id_df.empty:
        for _, r in id_df.iterrows():
            if str(r.get("Case ID (auto)")).strip() != str(case_id):
                continue
            try:
                template = str(r.get("Template") or "").lower()
                individual = str(r.get("Individual ID") or r.get("Filename") or f"{case_id}-id")
                if "nric" in template:
                    nric_path = out / f"{individual}_NRIC.pdf"
                    dob = _parse_date(r.get("Date of Birth (auto)")) or datetime.now().date()
                    issue = _parse_date(r.get("Issue Date")) or datetime.now().date()
                    expiry = _parse_date(r.get("Expiry Date")) or datetime.now().date()
                    generate_nric_card(str(nric_path), name=r.get("Individual Name (auto)") or r.get("Individual Name") or "", nric_no=str(r.get("NRIC/Passport No. (auto)") or r.get("NRIC/Passport No.") or ""), date_of_birth=dob, nationality=r.get("Nationality (Auto)") or "Singaporean", gender="", race="", address_lines=[r.get("Residential Address") or ""], issue_date=issue, expiry_date=expiry, serial_no="")
                    print("Wrote", nric_path)
                if "passport" in template:
                    pp_path = out / f"{individual}_Passport.pdf"
                    dob = _parse_date(r.get("Date of Birth (auto)")) or datetime.now().date()
                    doi = _parse_date(r.get("Date of Issue")) or datetime.now().date()
                    doe = _parse_date(r.get("Date of Expiry")) or datetime.now().date()
                    generate_passport_page(str(pp_path), passport_no=str(r.get("NRIC/Passport No. (auto)") or ""), surname="", given_names=r.get("Individual Name (auto)") or "", nationality=r.get("Nationality (Auto)") or "", date_of_birth=dob, place_of_birth="", sex="", date_of_issue=doi, date_of_expiry=doe)
                    print("Wrote", pp_path)
            except Exception as e:
                print("ID generation failed:", e)

    # 6) Payslip
    payslip_df = _read_sheet("Payslips") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not payslip_df.empty:
        for _, r in payslip_df.iterrows():
            if str(r.get("Case ID (auto)")) .strip() != str(case_id):
                continue
            try:
                individual = str(r.get("Individual ID") or r.get("Filename") or f"{case_id}-payslip")
                ps_path = out / f"{individual}_Payslip.pdf"
                # pick builder based on Template column (e.g. 'Payslip 1')
                template = str(r.get("Template") or "").lower()
                if "2" in template or "payslip 2" in template:
                    builder = build_payslip_2
                elif "3" in template or "payslip 3" in template:
                    builder = build_payslip_3
                else:
                    builder = build_payslip_1

                # The Payslips tab only carries identity fields (name, NRIC, role,
                # employer) — no salary figures — so the money fields are synthesized,
                # seeded on Individual ID so re-runs are reproducible.
                application_date = as_date(_openpyxl_case_row("Case Registry", case_id).get("Application Date"))
                data = synthesize_payslip_data(
                    individual,
                    employee_name=r.get("Individual Name (auto)") or "Employee",
                    employee_id=individual,
                    employer_name=r.get("Employer Name") or "Employer",
                    role=r.get("Role (auto)") or "",
                    application_date=application_date,
                )
                builder(ps_path, data)
            except Exception as e:
                print("Payslip generation failed:", e)

    # 7) Certificate of Incorporation
    coi_df = _read_sheet("Cert of Incorporation") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not coi_df.empty:
        for _, r in coi_df.iterrows():
            if str(r.get("Case ID")).strip() != str(case_id):
                continue
            try:
                coi_path = out / str(r.get("Filename") or f"certificate_of_incorporation_{case_id}.pdf")
                generate_certificate_of_incorporation(str(coi_path), company_name=r.get("Company Name (auto)") or "", company_number=str(r.get("UEN (auto)") or ""), incorporation_date=r.get("Incorporation Date (auto)") or datetime.now().date())
                print("Wrote", coi_path)
            except Exception as e:
                print("COI generation failed:", e)

    # 8) Proof of Address — Template varies per individual row: Utility Bill,
    # Bank Statement, or NRIC (the last is a no-op here: that individual's address is
    # already evidenced by the NRIC/Passport document generated in section 4).
    util_df = _read_sheet("Proof of Address") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not util_df.empty:
        for _, r in util_df.iterrows():
            if str(r.get("Case ID (auto)")).strip() != str(case_id):
                continue
            template = str(r.get("Template") or "").lower()
            individual = str(r.get("Individual ID") or r.get("Filename") or f"{case_id}-proof")

            if "utility" in template:
                try:
                    util_path = out / str(r.get("Filename") or f"utility_bill_{case_id}.pdf")
                    generate_utility_bill(str(util_path), provider_name=r.get("Provider Name") or r.get("Employer Name") or "PacificLight", bill_date=r.get("Date") or datetime.now().date(), ple_account_no=r.get("Account No") or "", statement_no=r.get("Filename") or "", customer_name=r.get("Individual Name (auto)") or r.get("Company Name (auto)") or "", address_lines=[r.get("Residential Address") or r.get("Registered Address") or ""], total_amount_payable=float(r.get("Amount") or 0), payment_due=datetime.now().date(), invoice_address_lines=[r.get("Residential Address") or ""], mssl_account_no="", billing_period_start=datetime.now().date(), billing_period_end=datetime.now().date())
                    print("Wrote", util_path)
                except Exception as e:
                    print("Utility bill generation failed:", e)

            elif "bank statement" in template:
                # Renders the case's own registered bank account in the OCBC personal
                # "Statement of Account" template, with the case's primary director as
                # account holder. Note: load_bank_case() always picks the first person
                # on the Individuals tab as director, so if a case has more than one
                # "Bank Statement" Proof of Address row (one per individual), they will
                # currently all come out identical apart from filename — fine for a
                # single-director case, but not yet individual-specific.
                try:
                    pa_path = out / str(r.get("Filename") or f"{individual}_ProofAddress.pdf")
                    ctx = load_bank_case(MASTERSHEET, case_id, bank_override="OCBC Personal")
                    blocks = build_bank_blocks(ctx)
                    render_ocbc_personal(ctx, blocks, pa_path)
                    print("Wrote", pa_path)
                except Exception as e:
                    print("Personal bank statement (proof of address) generation failed:", e)

            elif "nric" in template:
                pass

    # 9) Certificate of Incumbency
    incumbency_df = _read_sheet("Cert of Incumbency") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not incumbency_df.empty:
        try:
            for p in generate_cert_incumbency(MASTERSHEET, out, cases=[case_id]):
                print("Wrote", p)
        except Exception as e:
            print("Certificate of Incumbency generation failed:", e)

    # 10) Company Constitution
    constitution_df = _read_sheet("Company Constitution") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not constitution_df.empty:
        try:
            for p in generate_constitution(MASTERSHEET, out, cases=[case_id]):
                print("Wrote", p)
        except Exception as e:
            print("Company Constitution generation failed:", e)

    # 11) FATCA / CRS Self-Declaration (one combined self-cert form per case)
    fatca_df = _read_sheet("FATCA Self-Declaration") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not fatca_df.empty:
        try:
            for p in generate_fatca_crs(MASTERSHEET, out, cases=[case_id]):
                print("Wrote", p)
        except Exception as e:
            print("FATCA/CRS generation failed:", e)

    # 12) List of Loans — facility offer letters for loans the applicant already has
    # (not the loan being requested). One row per case; Loan Type picks the letter.
    loans_df = _read_sheet("List of Loans") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not loans_df.empty:
        for _, r in loans_df.iterrows():
            if str(r.get("Case ID")).strip() != str(case_id):
                continue
            loan_type = str(r.get("Loan Type") or "").strip()
            if not loan_type or loan_type.lower() == "none":
                continue
            try:
                case_row = _openpyxl_case_row("Case Registry", case_id)
                people_rows = _openpyxl_case_rows("Individuals", case_id)
                facts = company_facts(case_row, people_rows)
                director_name = facts["holders"][0]["name"] if facts["holders"] else "Director"
                bank_name = str(case_row.get("Bank Name") or "DBS Bank")
                bank_address_lines = LOAN_BANK_ADDRESSES.get(bank_name, DEFAULT_BANK_ADDRESS)
                company_address_lines = [p.strip() for p in str(facts["registered_address"]).split(",") if p.strip()]
                offer_date = facts["application_date"]
                amount = float(r.get("Loan Amount (SGD)") or 0)
                rate = float(r.get("Interest Rate (% p.a.)") or 6.0)
                tenure_months = _parse_tenure_months(str(r.get("Tenure") or ""))
                ref_no = f"{loan_type[:2].upper()}/{offer_date.year}/{case_id}"
                loan_path = out / str(r.get("Filename") or f"{case_id}_ListLoans.pdf")

                loan_type_l = loan_type.lower()
                if "term loan" in loan_type_l:
                    generate_term_loan_letter(
                        str(loan_path), bank_name=bank_name, bank_address_lines=bank_address_lines,
                        company_name=facts["company_name"], company_address_lines=company_address_lines,
                        company_uen=facts["uen"], director_name=director_name, ref_no=ref_no,
                        offer_date=offer_date, principal_amount=amount,
                        tenure_months=tenure_months, interest_rate_pa=rate,
                    )
                elif "enterprise financing" in loan_type_l or "efs" in loan_type_l:
                    generate_efs_letter(
                        str(loan_path), bank_name=bank_name, bank_address_lines=bank_address_lines,
                        company_name=facts["company_name"], company_address_lines=company_address_lines,
                        company_uen=facts["uen"], director_name=director_name, ref_no=ref_no,
                        offer_date=offer_date, facility_amount=amount,
                        tenure_years=max(round(tenure_months / 12), 1), interest_rate_pa=rate,
                    )
                elif "overdraft" in loan_type_l:
                    # generate_overdraft_letter has no flat interest_rate_pa param — it
                    # states rate as "reference rate + margin". Back reference_rate_pct
                    # out from the mastersheet's flat rate so the letter's own all-in-rate
                    # computation (reference + margin) matches it exactly.
                    margin_pct = 2.5
                    generate_overdraft_letter(
                        str(loan_path), bank_name=bank_name, bank_address_lines=bank_address_lines,
                        company_name=facts["company_name"], company_address_lines=company_address_lines,
                        company_uen=facts["uen"], director_name=director_name, ref_no=ref_no,
                        offer_date=offer_date, facility_limit=amount,
                        margin_pct=margin_pct, reference_rate_pct=max(rate - margin_pct, 0.1),
                        review_date=offer_date + timedelta(days=365),
                    )
                else:
                    print(f"Skipping unrecognised loan type {loan_type!r} for {case_id}")
                    continue
                print("Wrote", loan_path)
            except Exception as e:
                print("List of loans generation failed:", e)

    print("Generation completed for", case_id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.generate_from_mastersheet <CASE_ID>")
        sys.exit(1)
    generate_for_case(sys.argv[1])
