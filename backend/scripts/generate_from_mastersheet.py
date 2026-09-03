"""
Parse `v9_document_mastersheet.xlsx` and generate documents for a given case id.
Saves generated PDFs into `documents_generated/<case_id>/`.

Usage (from backend/):
    python -m scripts.generate_from_mastersheet C001

This script depends on `openpyxl` and `pandas` (added to requirements).
"""

from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

from doc_gen_scripts.nric_passport.nric_generator import generate_nric_card
from doc_gen_scripts.nric_passport.passport_generator import generate_passport_page
from doc_gen_scripts.payslips.generate_payslips import build_payslip_1, build_payslip_2, build_payslip_3
from doc_gen_scripts.corp_source_of_wealth.balance_sheet_income_statement_generator import generate as generate_sow
from doc_gen_scripts.corporate_bank_statement.docgen_bankstatement import generate as generate_bank_statement
from doc_gen_scripts.certificate_of_incorporation.coi_generator import generate_certificate_of_incorporation
from doc_gen_scripts.personal_proof_of_residence.utility_bill_generator import generate_utility_bill
from doc_gen_scripts.credit_reports.credit_bureau_report_generator import generate_credit_bureau_report
from doc_gen_scripts.company_constitution.docgen_constitution import generate as generate_constitution
from doc_gen_scripts.certificate_of_incumbency.docgen_acra_profile import generate as generate_cert_incumbency
# list_of_loans generators are imported lazily inside generate_for_case

BASE = Path(__file__).resolve().parents[1]
MASTERSHEET = BASE / "v9_document_mastersheet.xlsx"
OUT_DIR = BASE / "documents_generated"


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

                def _fmt_date(v):
                    if v is None:
                        return datetime.now().strftime("%Y-%m-%d")
                    try:
                        return pd.to_datetime(v).strftime("%Y-%m-%d")
                    except Exception:
                        return str(v)

                # common numeric parsing
                def _to_float(x):
                    try:
                        return float(x) if x not in (None, "") else 0.0
                    except Exception:
                        return 0.0

                basic_salary = _to_float(r.get("Net Pay") or r.get("Basic Salary"))

                data = {
                    "company_name": r.get("Employer Name") or "Employer",
                    "company_address": "",
                    "employee_name": r.get("Individual Name (auto)") or "Employee",
                    "employee_id": r.get("Individual ID") or "",
                    "pay_period": str(r.get("Pay Period") or ""),
                    "payment_date": _fmt_date(r.get("Payment Date") or r.get("Date") or datetime.now().date()),
                    "currency": r.get("Currency") or "SGD",
                    # fields for payslip 1
                    "basic_salary": basic_salary,
                    "allowances": _to_float(r.get("Allowances")),
                    "overtime": _to_float(r.get("Overtime")),
                    "cpf_employee": _to_float(r.get("CPF Employee")),
                    "other_deductions": _to_float(r.get("Other Deductions")),
                    # fields for payslip 2
                    "variable_pay": _to_float(r.get("Variable Pay")),
                    "benefits": _to_float(r.get("Benefits")),
                    "tax_withholding": _to_float(r.get("Tax Withholding")),
                    # fields for payslip 3
                    "department": r.get("Department") or "",
                    "job_title": r.get("Role (auto)") or "",
                    "housing_allowance": _to_float(r.get("Housing Allowance")),
                    "transport_allowance": _to_float(r.get("Transport Allowance")),
                    "other_allowance": _to_float(r.get("Other Allowance")),
                    "retirement_contribution": _to_float(r.get("Retirement Contribution")),
                    "tax_deduction": _to_float(r.get("Tax Deduction")),
                    "other_deduction": _to_float(r.get("Other Deduction")),
                }
                builder(ps_path, data)
                print("Wrote", ps_path)
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

    # 8) Utility bill / Proof of Address
    util_df = _read_sheet("Proof of Address") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not util_df.empty:
        for _, r in util_df.iterrows():
            if str(r.get("Case ID (auto)")).strip() != str(case_id):
                continue
            template = str(r.get("Template") or "").lower()
            if "utility" in template:
                try:
                    util_path = out / str(r.get("Filename") or f"utility_bill_{case_id}.pdf")
                    generate_utility_bill(str(util_path), provider_name=r.get("Provider Name") or r.get("Employer Name") or "PacificLight", bill_date=r.get("Date") or datetime.now().date(), ple_account_no=r.get("Account No") or "", statement_no=r.get("Filename") or "", customer_name=r.get("Individual Name (auto)") or r.get("Company Name (auto)") or "", address_lines=[r.get("Residential Address") or r.get("Registered Address") or ""], total_amount_payable=float(r.get("Amount") or 0), payment_due=datetime.now().date(), invoice_address_lines=[r.get("Residential Address") or ""], mssl_account_no="", billing_period_start=datetime.now().date(), billing_period_end=datetime.now().date())
                    print("Wrote", util_path)
                except Exception as e:
                    print("Utility bill generation failed:", e)

    print("Generation completed for", case_id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.generate_from_mastersheet <CASE_ID>")
        sys.exit(1)
    generate_for_case(sys.argv[1])
