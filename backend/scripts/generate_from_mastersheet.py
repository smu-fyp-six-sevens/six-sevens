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
from doc_gen_scripts.payslips.generate_payslips import build_payslip_1
from doc_gen_scripts.corporate_bank_statement.docgen_bankstatement import generate as generate_bank_statement
from doc_gen_scripts.income_statement.income_statement_generator import generate_income_statement
from doc_gen_scripts.certificate_of_incorporation.certificate_of_incorporation_generator import generate_certificate_of_incorporation
from doc_gen_scripts.personal_proof_of_residence.utility_bill_generator import generate_utility_bill
from doc_gen_scripts.balance_sheet.balance_sheet_generator import generate_balance_sheet
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

    # 2) Source of wealth docs - balance sheet & income statement
    sow_df = _read_sheet("Source of Wealth Docs") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not sow_df.empty:
        for _, r in sow_df.iterrows():
            if str(r.get("Case ID")).strip() != str(case_id):
                continue
            doc_type = str(r.get("Document Type") or "").lower()
            try:
                if "income" in doc_type:
                    is_path = out / f"income_statement_{case_id}.pdf"
                    company_name = r.get("Company Name (auto)") or r.get("Company Name") or "Company"
                    company_uen = r.get("UEN (auto)") or r.get("UEN") or r.get("Company UEN") or "00000000A"
                    period_end = datetime.now().date()
                    generate_income_statement(str(is_path), company_name=company_name, company_uen=company_uen, period_end=period_end, revenue=0, cost_of_sales=0, operating_expenses=0)
                    print("Wrote", is_path)
                if "balance" in doc_type:
                    bs_path = out / f"balance_sheet_{case_id}.pdf"
                    company_name = r.get("Company Name (auto)") or r.get("Company Name") or "Company"
                    company_uen = r.get("UEN (auto)") or r.get("UEN") or r.get("Company UEN") or "00000000A"
                    generate_balance_sheet(str(bs_path), company_name=company_name, company_uen=company_uen, statement_date=datetime.now().date(), director_name="Director", fy2026_profit=0, fy2025_profit=0)
                    print("Wrote", bs_path)
            except Exception as e:
                print("Income/Balance generation failed:", e)

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

    # Company Constitution (master-driven generator)
    const_df = _read_sheet("Company Constitution") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not const_df.empty:
        # generate_constitution reads the mastersheet and emits files for the case
        try:
            paths = generate_constitution(MASTERSHEET, out, cases=[case_id])
            for p in paths:
                print("Wrote", p)
        except Exception as e:
            print("Company constitution generation failed:", e)

    # Certificate of Incumbency / ACRA profile (master-driven)
    coi_df = _read_sheet("Cert of Incumbency") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not coi_df.empty:
        try:
            paths = generate_cert_incumbency(MASTERSHEET, out, cases=[case_id])
            for p in paths:
                print("Wrote", p)
        except Exception as e:
            print("Certificate of incumbency generation failed:", e)

    # List of loans (facility letters)
    loans_df = _read_sheet("List of Loans") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not loans_df.empty:
        # list_of_loans generators import loan_letter_common from their directory;
        # make that directory importable then import generators lazily
        loans_pkg_path = BASE / "doc_gen_scripts" / "list_of_loans"
        import sys
        sys.path.insert(0, str(loans_pkg_path))
        try:
            from doc_gen_scripts.list_of_loans.efs_facility_generator import generate_efs_letter
            from doc_gen_scripts.list_of_loans.overdraft_facility_generator import generate_overdraft_letter
            from doc_gen_scripts.list_of_loans.term_loan_facility_generator import generate_term_loan_letter
        except Exception:
            generate_efs_letter = generate_overdraft_letter = generate_term_loan_letter = None
        for _, r in loans_df.iterrows():
            if str(r.get("Case ID")).strip() != str(case_id):
                continue
            try:
                facility_type = str(r.get("Loan Type") or "").lower()
                if ("efs" in facility_type or "enterprise" in facility_type) and generate_efs_letter:
                    outp = out / f"{r.get('Filename') or f'C010_EFS_LoanFacility_{case_id}.pdf'}"
                    generate_efs_letter(str(outp), bank_name=str(r.get("Company Name (auto)") or "Meridian Bank"), bank_address_lines=["Address"], company_name=str(r.get("Company Name (auto)") or "Company"), company_address_lines=["Address"], company_uen=str(r.get("UEN (auto)") or "00000000A"), director_name="Director", ref_no=str(r.get("Filename") or f"EFS/{case_id}"), offer_date=datetime.now().date(), facility_amount=float(r.get("Loan Amount (SGD)") or 0))
                    print("Wrote", outp)
                elif "overdraft" in facility_type and generate_overdraft_letter:
                    outp = out / f"{r.get('Filename') or f'C004_Overdraft_LoanFacility_{case_id}.pdf'}"
                    generate_overdraft_letter(str(outp), bank_name=str(r.get("Company Name (auto)") or "Meridian Bank"), bank_address_lines=["Address"], company_name=str(r.get("Company Name (auto)") or "Company"), company_address_lines=["Address"], company_uen=str(r.get("UEN (auto)") or "00000000A"), director_name="Director", ref_no=str(r.get("Filename") or f"OD/{case_id}"), offer_date=datetime.now().date(), facility_limit=float(r.get("Loan Amount (SGD)") or 0), review_date=datetime.now().date())
                    print("Wrote", outp)
                elif "term" in facility_type and generate_term_loan_letter:
                    outp = out / f"{r.get('Filename') or f'C009_TermLoan_LoanFacility_{case_id}.pdf'}"
                    generate_term_loan_letter(str(outp), bank_name=str(r.get("Company Name (auto)") or "Meridian Bank"), bank_address_lines=["Address"], company_name=str(r.get("Company Name (auto)") or "Company"), company_address_lines=["Address"], company_uen=str(r.get("UEN (auto)") or "00000000A"), director_name="Director", ref_no=str(r.get("Filename") or f"TL/{case_id}"), offer_date=datetime.now().date(), principal_amount=float(r.get("Loan Amount (SGD)") or 0), tenure_months=36, interest_rate_pa=float(r.get("Interest Rate (% p.a.)") or 6.0))
                    print("Wrote", outp)
            except Exception as e:
                print("Loan facility generation failed:", e)
        try:
            sys.path.remove(str(loans_pkg_path))
        except Exception:
            pass

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
                    generate_nric_card(str(nric_path), name=r.get("Individual Name (auto)") or r.get("Individual Name") or "", nric_no=str(r.get("NRIC/Passport No. (auto)") or r.get("NRIC/Passport No.") or ""), date_of_birth=r.get("Date of Birth (auto)") or datetime.now().date(), nationality=r.get("Nationality (Auto)") or "Singaporean", gender="", race="", address_lines=[r.get("Residential Address") or ""], issue_date=datetime.now().date(), expiry_date=datetime.now().date(), serial_no="")
                    print("Wrote", nric_path)
                if "passport" in template:
                    pp_path = out / f"{individual}_Passport.pdf"
                    generate_passport_page(str(pp_path), passport_no=str(r.get("NRIC/Passport No. (auto)") or ""), surname="", given_names=r.get("Individual Name (auto)") or "", nationality=r.get("Nationality (Auto)") or "", date_of_birth=r.get("Date of Birth (auto)") or datetime.now().date(), place_of_birth="", sex="", date_of_issue=datetime.now().date(), date_of_expiry=datetime.now().date())
                    print("Wrote", pp_path)
            except Exception as e:
                print("ID generation failed:", e)

    # 6) Payslip
    payslip_df = _read_sheet("Payslips") if (MASTERSHEET.exists()) else pd.DataFrame()
    if not payslip_df.empty:
        for _, r in payslip_df.iterrows():
            if str(r.get("Case ID (auto)")).strip() != str(case_id):
                continue
            try:
                individual = str(r.get("Individual ID") or r.get("Filename") or f"{case_id}-payslip")
                ps_path = out / f"{individual}_Payslip.pdf"
                data = {
                    "company_name": r.get("Employer Name") or "Employer",
                    "company_address": "",
                    "employee_name": r.get("Individual Name (auto)") or "Employee",
                    "employee_id": r.get("Individual ID") or "",
                    "pay_period": "",
                    "payment_date": datetime.now().date(),
                    "currency": "SGD",
                    "basic_salary": float(r.get("Net Pay") or 0),
                    "allowances": 0,
                    "overtime": 0,
                    "cpf_employee": 0,
                    "other_deductions": 0,
                }
                build_payslip_1(ps_path, data)
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
