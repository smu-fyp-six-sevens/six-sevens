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

# generators we added earlier
from doc_gen_scripts.nric_passport.nric_generator import generate_nric_card
from doc_gen_scripts.passport.passport_generator import generate_passport_page
from doc_gen_scripts.payslips.payslip_generator import generate_payslip
from doc_gen_scripts.corporate_bank_statement.bank_statement_generator import generate_bank_statement
from doc_gen_scripts.income_statement.income_statement_generator import generate_income_statement
from doc_gen_scripts.certificate_of_incorporation.certificate_of_incorporation_generator import generate_certificate_of_incorporation
from doc_gen_scripts.telecom_bill.utility_bill_generator import generate_utility_bill

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

    # 1) Credit report tab -> use for credit reports (credit_reports generator not present here)
    credit_df = _read_sheet("credit_report") if (MASTERSHEET.exists()) else pd.DataFrame()
    credit_row = _get_row_by_case(credit_df, case_id)
    if credit_row:
        # not implemented: credit report generator; save a placeholder PDF
        path = out / f"credit_report_{case_id}.pdf"
        path.write_text("Credit report generation placeholder\n" + str(credit_row))
        print("Wrote", path)

    # 2) Source of wealth docs - balance sheet & income statement
    sow_df = _read_sheet("source_of_wealth") if (MASTERSHEET.exists()) else pd.DataFrame()
    sow_row = _get_row_by_case(sow_df, case_id)
    if sow_row:
        # map columns - the mastersheet should have columns like `company_name`, `company_uen`, `period_end`, `revenue`, etc.
        try:
            bs_path = out / f"balance_sheet_{case_id}.pdf"
            is_path = out / f"income_statement_{case_id}.pdf"
            # attempt to extract relevant fields with fallbacks
            company_name = sow_row.get("company_name") or sow_row.get("Company Name") or "Company"
            company_uen = sow_row.get("company_uen") or sow_row.get("Company UEN") or "00000000A"
            period_end = sow_row.get("period_end") or sow_row.get("Period End") or sow_row.get("statement_date")
            if isinstance(period_end, str):
                period_end = datetime.fromisoformat(period_end).date()
            elif pd.isna(period_end):
                period_end = datetime.now().date()
            revenue = float(sow_row.get("revenue") or 0)
            cost_of_sales = float(sow_row.get("cost_of_sales") or 0)
            operating_expenses = float(sow_row.get("operating_expenses") or 0)

            generate_income_statement(str(is_path), company_name=company_name, company_uen=company_uen, period_end=period_end, revenue=revenue, cost_of_sales=cost_of_sales, operating_expenses=operating_expenses)
            print("Wrote", is_path)
        except Exception as e:
            print("Income statement generation failed:", e)

    # 3) Bank statement tab
    bank_df = _read_sheet("bank_statement") if (MASTERSHEET.exists()) else pd.DataFrame()
    bank_row = _get_row_by_case(bank_df, case_id)
    if bank_row:
        try:
            bank_path = out / f"bank_statement_{case_id}.pdf"
            bank_name = bank_row.get("bank_name") or "OCBC"
            account_holder = bank_row.get("account_holder") or "Customer"
            account_number = bank_row.get("account_number") or "XXXX-0000"
            statement_number = bank_row.get("statement_number") or "2026-0001"
            start = bank_row.get("period_start") or datetime.now().date()
            end = bank_row.get("period_end") or datetime.now().date()
            opening = float(bank_row.get("opening_balance") or 0)
            closing = float(bank_row.get("closing_balance") or 0)
            avail = float(bank_row.get("available_balance") or closing)
            generate_bank_statement(str(bank_path), bank_name=bank_name, account_holder=account_holder, account_number=account_number, statement_number=statement_number, statement_period_start=start, statement_period_end=end, opening_balance=opening, closing_balance=closing, available_balance=avail, branch_code=str(bank_row.get("branch_code") or "001"))
            print("Wrote", bank_path)
        except Exception as e:
            print("Bank statement generation failed:", e)

    # 4) NRIC/Passport/ID documents
    id_df = _read_sheet("id_documents") if (MASTERSHEET.exists()) else pd.DataFrame()
    id_row = _get_row_by_case(id_df, case_id)
    if id_row:
        try:
            nric_path = out / f"id_nric_{case_id}.pdf"
            generate_nric_card(str(nric_path), name=id_row.get("name") or "", nric_no=id_row.get("nric_no") or "", date_of_birth=id_row.get("dob") or datetime.now().date(), nationality=id_row.get("nationality") or "Singaporean", gender=id_row.get("gender") or "M", race=id_row.get("race") or "", address_lines=[id_row.get("address") or ""], issue_date=id_row.get("issue_date") or datetime.now().date(), expiry_date=id_row.get("expiry_date") or datetime.now().date(), serial_no=id_row.get("serial_no") or "")
            print("Wrote", nric_path)
        except Exception as e:
            print("ID generation failed:", e)

    # 5) Passport
    if id_row:
        try:
            pp_path = out / f"passport_{case_id}.pdf"
            generate_passport_page(str(pp_path), passport_no=id_row.get("passport_no") or "", surname=id_row.get("surname") or "", given_names=id_row.get("given_names") or "", nationality=id_row.get("nationality") or "", date_of_birth=id_row.get("dob") or datetime.now().date(), place_of_birth=id_row.get("place_of_birth") or "", sex=id_row.get("gender") or "M", date_of_issue=id_row.get("issue_date") or datetime.now().date(), date_of_expiry=id_row.get("expiry_date") or datetime.now().date())
            print("Wrote", pp_path)
        except Exception as e:
            print("Passport generation failed:", e)

    # 6) Payslip
    payslip_df = _read_sheet("payslips") if (MASTERSHEET.exists()) else pd.DataFrame()
    payslip_row = _get_row_by_case(payslip_df, case_id)
    if payslip_row:
        try:
            ps_path = out / f"payslip_{case_id}.pdf"
            generate_payslip(str(ps_path), employer_name=payslip_row.get("employer_name") or "Employer", employee_name=payslip_row.get("employee_name") or "Employee", employee_id=payslip_row.get("employee_id") or "", pay_period=payslip_row.get("pay_period") or "", payment_date=payslip_row.get("payment_date") or datetime.now().date(), basic_salary=float(payslip_row.get("basic_salary") or 0))
            print("Wrote", ps_path)
        except Exception as e:
            print("Payslip generation failed:", e)

    # 7) Certificate of Incorporation
    coi_df = _read_sheet("certificate_of_incorporation") if (MASTERSHEET.exists()) else pd.DataFrame()
    coi_row = _get_row_by_case(coi_df, case_id)
    if coi_row:
        try:
            coi_path = out / f"certificate_of_incorporation_{case_id}.pdf"
            generate_certificate_of_incorporation(str(coi_path), company_name=coi_row.get("company_name") or "", company_number=coi_row.get("company_number") or "", incorporation_date=coi_row.get("incorporation_date") or datetime.now().date())
            print("Wrote", coi_path)
        except Exception as e:
            print("COI generation failed:", e)

    # 8) Utility bill example (telecom/utility)
    util_df = _read_sheet("utility_bills") if (MASTERSHEET.exists()) else pd.DataFrame()
    util_row = _get_row_by_case(util_df, case_id)
    if util_row:
        try:
            util_path = out / f"utility_bill_{case_id}.pdf"
            generate_utility_bill(str(util_path), provider_name=util_row.get("provider_name") or "PacificLight", bill_date=util_row.get("bill_date") or datetime.now().date(), ple_account_no=util_row.get("ple_account_no") or "", statement_no=util_row.get("statement_no") or "", customer_name=util_row.get("customer_name") or "", address_lines=[util_row.get("address") or ""], total_amount_payable=float(util_row.get("total_amount_payable") or 0), payment_due=util_row.get("payment_due") or datetime.now().date(), invoice_address_lines=[util_row.get("invoice_address") or ""], mssl_account_no=util_row.get("mssl_account_no") or "", billing_period_start=util_row.get("billing_period_start") or datetime.now().date(), billing_period_end=util_row.get("billing_period_end") or datetime.now().date())
            print("Wrote", util_path)
        except Exception as e:
            print("Utility bill generation failed:", e)

    print("Generation completed for", case_id)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.generate_from_mastersheet <CASE_ID>")
        sys.exit(1)
    generate_for_case(sys.argv[1])
