"""
Truth files: what a generated document was built from, saved beside it for the success-metric scorer.

A generator holds every value it prints (the transactions, balances and payslip figures it invented, and the
identity facts it read from the mastersheet). With DOCGEN_TRUTH=1 it also writes them to
`<document>.truth.json`, under the field names the extractor uses (app/langgraph/extraction_prompts.py), so the
scorer can compare extraction against them field by field (scripts/metrics/score.py).

Only what is printed is kept. Each value is looked for in the PDF the generator just wrote; one that isn't on the
page (a figure a template doesn't show) goes under "not_printed" and is never scored, so the extractor isn't
marked down for a value it could not have read.

Off by default: without DOCGEN_TRUTH nothing here runs and generation is unchanged. Nothing is kept in memory
between calls, because generators import this module under two names (`truth` and `doc_gen_scripts.truth`).
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

TRUTH_SUFFIX = ".truth.json"

_DATE_FORMATS = (
    "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %y", "%B %d, %Y", "%b %d, %Y",
    "%d.%m.%Y", "%d-%b-%Y", "%d%m%Y", "%Y%m%d",
)
_PARSE_FORMATS = ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y", "%B %d, %Y", "%Y-%m-%d %H:%M:%S")


def enabled() -> bool:
    return os.environ.get("DOCGEN_TRUTH", "").strip() not in ("", "0")


def truth_path(document: str | Path) -> Path:
    document = Path(document)
    return document.with_name(document.name + TRUTH_SUFFIX)


def to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in _PARSE_FORMATS:
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return None


# --------------------------------------------------------------------------
# Is a value on the page?
# --------------------------------------------------------------------------

def _alnum(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


class _Page:
    """The text a PDF prints (its form-field values included), in the three shapes the checks need."""

    def __init__(self, pdf: Path) -> None:
        import pymupdf

        parts: list[str] = []
        with pymupdf.open(stream=pdf.read_bytes(), filetype="pdf") as doc:
            for page in doc:
                parts.append(page.get_text())
                for widget in page.widgets() or []:
                    if widget.field_value not in (None, "", "Off"):
                        parts.append(str(widget.field_value))
        text = "\n".join(parts)
        self.alnum = _alnum(text)
        self.tokens = set(re.findall(r"[a-z0-9]+", text.casefold()))
        self.numbers: set[float] = set()
        for match in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
            try:
                self.numbers.add(round(float(match.replace(",", "")), 2))
            except ValueError:
                pass

    def shows(self, value: Any) -> bool:
        if isinstance(value, bool) or value is None:
            return False
        if isinstance(value, (int, float)):
            return round(abs(float(value)), 2) in self.numbers
        if isinstance(value, (date, datetime)):
            day = value if not isinstance(value, datetime) else value.date()
            shapes = [day.strftime(fmt) for fmt in _DATE_FORMATS]
            shapes += [f"{day.day} {day:%b %Y}", f"{day.day} {day:%B %Y}", f"{day.day}/{day.month}/{day.year}"]
            return any(_alnum(shape) in self.alnum for shape in shapes)
        text = str(value).strip()
        if not text:
            return False
        if _alnum(text) and _alnum(text) in self.alnum:
            return True
        # An address is often printed over several lines, or with its parts in separate boxes.
        tokens = re.findall(r"[a-z0-9]+", text.casefold())
        return len(tokens) > 1 and all(token in self.tokens for token in tokens)


def _plain(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 2)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def _split_printed(fields: dict[str, Any], page: _Page | None, derived: set[str]) -> tuple[dict, dict]:
    """(printed, not printed). A list of rows keeps each row's printed columns; a row with none is dropped."""
    printed: dict[str, Any] = {}
    missing: dict[str, Any] = {}
    for name, value in fields.items():
        if value is None or value == "":
            continue
        if page is None or name in derived:
            printed[name] = value
        elif isinstance(value, list):
            if not value:
                printed[name] = value  # "none on record" has nothing to look for
                continue
            rows, dropped = [], []
            for row in value:
                if not isinstance(row, dict):
                    (rows if page.shows(row) else dropped).append(row)
                    continue
                kept = {k: v for k, v in row.items() if v not in (None, "") and page.shows(v)}
                lost = {k: v for k, v in row.items() if v not in (None, "") and k not in kept}
                if kept:
                    rows.append(kept)
                if lost:
                    dropped.append(lost)
            if rows:
                printed[name] = rows
            if dropped:
                missing[name] = dropped
        elif page.shows(value):
            printed[name] = value
        else:
            missing[name] = value
    return printed, missing


def record(
    output_path: str | Path,
    document_type: str,
    fields: dict[str, Any],
    *,
    derived: Iterable[str] = (),
    also_acceptable: Iterable[str] = (),
    check_pdf: str | Path | None = None,
    files: Iterable[str] | None = None,
    reference: dict[str, Any] | None = None,
) -> None:
    """
    Save `fields` as the truth for the document at `output_path`.

    derived: fields that describe the document rather than quote it (entity_type, document_side), so they are
    kept without being looked for on the page.
    also_acceptable: other document types the classifier may reasonably give this file (a personal bank statement
    filed as proof of address).
    check_pdf: the PDF to read when `output_path` isn't one (an NRIC side cropped to an image).
    reference: printed values that are not extraction fields, kept so a scripted forgery can find them on the
    page (scripts/metrics/frauds.py). Never scored.
    """
    if not enabled():
        return
    output_path = Path(output_path)
    source = Path(check_pdf) if check_pdf else output_path
    page: _Page | None = None
    try:
        if source.exists() and source.read_bytes().startswith(b"%PDF"):
            page = _Page(source)
    except Exception as exc:  # truth is a by-product: never fail the document over it
        print(f"Truth: could not read {source.name} to check its values ({exc}); kept unchecked")
    printed, missing = _split_printed(fields, page, set(derived))
    payload = {
        "filename": output_path.name,
        "files": list(files) if files else [output_path.name],
        "document_type": document_type,
        "also_acceptable": list(also_acceptable),
        "seed": os.environ.get("DOCGEN_SEED", "").strip() or None,
        "checked_against_page": page is not None,
        "fields": _plain(printed),
        "not_printed": _plain(missing),
        "reference": _plain(reference or {}),
        "fraud_expected": False,
        "tampering": [],
    }
    truth_path(output_path).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def follow_rename(original: Path, written: list[Path]) -> None:
    """Keep a truth file with its document when the normalisation pass splits it into one image per page."""
    source = truth_path(original)
    if not source.exists() or written == [original] or not written:
        return
    payload = json.loads(source.read_text())
    payload["files"] = [p.name for p in written]
    source.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------
# One mapper per document type: generator values -> the extractor's field names
# --------------------------------------------------------------------------

def _join(lines: Iterable[Any]) -> str:
    return ", ".join(str(part).strip() for part in lines if str(part or "").strip())


def _holder(name: Any, address: Any, entity_type: str) -> dict[str, Any]:
    """A statement's or bill's holder, under the names extraction stores it by (extract._apply_entity_fields):
    company_name/company_address for a company, individual_name/individual_address for a person."""
    corporate = entity_type == "company"
    prefix = "company" if corporate else "individual"
    return {f"{prefix}_name": name, f"{prefix}_address": address, "entity_type": "corporate" if corporate else "individual"}


def payslip(data: dict, template: str, net_pay: float) -> dict[str, Any]:
    if "2" in template:
        earnings = data["basic_salary"] + data["variable_pay"] + data["benefits"]
        cpf, deductions = data["cpf_employee_2"], data["cpf_employee_2"] + data["other_deductions"]
    elif "3" in template:
        earnings = (data["basic_salary"] + data["housing_allowance"] + data["transport_allowance"]
                    + data["other_allowance"])
        cpf = data["retirement_contribution"]
        deductions = cpf + data["tax_deduction"] + data["other_deduction"]
    else:
        earnings = data["basic_salary"] + data["allowances"] + data["overtime"]
        cpf, deductions = data["cpf_employee"], data["cpf_employee"] + data["other_deductions"]
    pay_date = to_date(data.get("payment_date"))
    return {
        "individual_name": data["employee_name"],
        "company_name": data["company_name"],
        "company_address": data.get("company_address"),
        "net_pay": float(net_pay),
        "basic_pay": float(data["basic_salary"]),
        "total_earnings": float(earnings),
        "total_deductions": float(deductions),
        "employee_cpf": float(cpf),
        "pay_date": pay_date,
        "pay_period_end": pay_date,
    }


def nric(persona: dict, side: str) -> dict[str, Any]:
    front = {
        "nric_number": persona["nric_no"],
        "individual_name": persona["name"],
        "date_of_birth": persona["date_of_birth"],
        "country": persona.get("birth_place", "Singapore"),
        "race": persona["race"],
        "sex": persona["gender"],
    }
    back = {
        "nric_number": persona["nric_no"],
        "individual_address": _join(persona["address_lines"]),
        "date_of_issue": persona["issue_date"],
    }
    fields = {"front": front, "back": back}.get(side) or {**front, **back}
    return {**fields, "document_side": side}


def passport(persona: dict) -> dict[str, Any]:
    return {
        "individual_name": f"{persona['surname']} {persona['given_names']}".strip(),
        "passport_number": persona["passport_no"],
        "date_of_birth": persona["date_of_birth"],
        "nationality": persona["nationality"],
        "date_of_expiry": persona["date_of_expiry"],
        "date_of_issue": persona["date_of_issue"],
        "place_of_birth": persona["place_of_birth"],
        "sex": persona["sex"],
        "nric_number": persona.get("national_id_no"),
    }


def certificate_of_incorporation(context: dict) -> dict[str, Any]:
    former = (context.get("former_names") or [{}])[0]
    incorporated = to_date(context.get("incorporation_date"))
    return {
        "company_name": context.get("company_name"),
        "uen": context.get("uen"),
        "incorporation_date": incorporated,
        "company_type": context.get("company_status"),
        "incorporating_act": "Companies Act 1967",
        "certificate_date": to_date(context.get("issue_date")) or incorporated,
        "company_former_name": former.get("name"),
        "company_former_name_change_date": to_date(former.get("effective_from")),
    }


def utility_bill(customer_name: str, address_lines: list[str], data: dict, entity_type: str) -> dict[str, Any]:
    return {
        **_holder(customer_name, _join(address_lines), entity_type),
        "bill_date": data.get("bill_date"),
        "billing_period_end": data.get("billing_period_end"),
        "total_payable": data.get("total_amount_payable"),
        "due_date": data.get("payment_due"),
        "previous_balance": data.get("previous_balance"),
        "payment_received": data.get("payment_received_amount"),
        "current_charges": data.get("current_charges"),
        "contract_end_date": data.get("contract_end_date"),
        "usage_this_period": data.get("usage_kwh"),
    }


def telecom_bill(persona: dict) -> dict[str, Any]:
    return {
        **_holder(persona["customer_name"], _join([persona["address_line_1"], persona["address_line_2"],
                                                   f"Singapore {persona['postal_code']}"]), "company"),
        "bill_date": persona["date_of_bill"],
        "due_date": persona["due_date"],
        "previous_balance": persona["previous_balance"],
        "current_charges": persona["current_charges"],
        "total_payable": persona["total_outstanding"],
        "gst_amount": persona["gst_amount"],
    }


def credit_report(persona: dict) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "individual_name": persona["consumer_name"],
        "id_number": persona["id_number"],
        "date_of_birth": persona["date_of_birth"],
        "nationality": persona["nationality"],
        "individual_address": persona["address"],
        "report_date": persona["enquiry_date"],
    }
    # Only the "none on record" answer is kept: the populated tables have no fixed row shape to compare.
    if not persona.get("default_records"):
        fields["defaults"] = []
    if not persona.get("bankruptcy_rows"):
        fields["bankruptcy_proceedings"] = []
    return fields


def _period_end(months: list[date]) -> date | None:
    if not months:
        return None
    last = months[-1]
    following = date(last.year + (last.month == 12), (last.month % 12) + 1, 1)
    return date.fromordinal(following.toordinal() - 1)


def _transactions(blocks: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "date": txn.txn_date,
            "description": " ".join(txn.lines),
            "amount": round(txn.deposit if txn.deposit is not None else -(txn.withdrawal or 0.0), 2),
            "balance": round(txn.balance, 2),
        }
        for block in blocks for txn in block.txns
    ]


def bank_statement(ctx: dict, blocks: list[Any]) -> dict[str, Any]:
    months = ctx.get("months") or []
    return {
        "bank_name": ctx["bank"].legal_name,
        "account_number": ctx["account_no"],
        **_holder(ctx["company_name"], ctx["address"], "company"),
        "statement_period_start": months[0] if months else None,
        "statement_period_end": _period_end(months),
        "opening_balance": blocks[0].opening if blocks else None,
        "ledger_balance": blocks[-1].closing if blocks else None,
        "total_credits": round(sum(b.total_deposit for b in blocks), 2),
        "total_debits": round(sum(b.total_withdrawal for b in blocks), 2),
        "transactions": _transactions(blocks),
    }


def personal_bank_statement(ctx: dict, blocks: list[Any]) -> dict[str, Any]:
    """Filed as proof of address, so the holder and address lead; the statement fields are there for a classifier
    that calls it a bank statement instead."""
    months = ctx.get("months") or []
    return {
        **_holder(ctx.get("account_holder"), _join(ctx.get("holder_address") or []), "individual"),
        "account_number": ctx.get("account_no"),
        "statement_period_start": months[0] if months else None,
        "statement_period_end": _period_end(months),
        "opening_balance": blocks[0].opening if blocks else None,
        "ledger_balance": blocks[-1].closing if blocks else None,
        "transactions": _transactions(blocks),
    }


def financial_statements(*, company_name: str, uen: str, period_end: date, signing_date: date | None,
                         cur: dict, prev: dict) -> dict[str, Any]:
    return {
        "company_name": company_name,
        "uen": uen,
        "financial_year_end": period_end,
        "signing_date": signing_date,
        "revenue": cur["revenue"],
        "profit_for_the_year": cur["profit_for_year"],
        "profit_for_the_year_prior_year": prev["profit_for_year"],
        "total_assets": cur["total_assets"],
        "total_liabilities": cur["total_liabilities"],
        "total_equity": cur["total_equity"],
        "share_capital": cur["share_capital"],
        "total_current_assets": cur["total_c_assets"],
        "total_non_current_assets": cur["total_nc_assets"],
        "total_current_liabilities": cur["total_c_liab"],
        "total_non_current_liabilities": cur["total_nc_liab"],
        "retained_earnings_closing": cur["retained_earnings_end"],
        "retained_earnings_closing_prior_year": prev["retained_earnings_end"],
        "total_assets_prior_year": prev["total_assets"],
        "total_liabilities_prior_year": prev["total_liabilities"],
        "total_equity_prior_year": prev["total_equity"],
    }


def investment_statement(stmt: Any) -> dict[str, Any]:
    return {
        "account_holder_name": stmt.name,
        "individual_address": stmt.address if isinstance(stmt.address, str) else _join(stmt.address),
        "account_number": stmt.account_number,
        "net_asset_value": stmt.end_nav,
        "portfolio_value": stmt.portfolio,
        "cash_balance": stmt.cash_balance,
    }


def certificate_of_incumbency(content: dict) -> dict[str, Any]:
    return {
        "company_name": content["company_name"],
        "uen": content["uen"],
        "incorporation_date": content["incorporation_date"],
        "company_type": content["company_type"],
        "company_address": content["registered_address"],
        "share_capital": content["share_capital"],
        "share_class": content["share_type"],
        "shareholders": [
            {"name": h["name"], "identification_no": h["id"], "address": h["address"], "shares": h["shares"]}
            for h in content["shareholders"]
        ],
        "officers": [
            {"name": o["name"], "identification_no": o["id"], "position": o["position"],
             "date_of_appointment": o["appointed"]}
            for o in content["officers"]
        ],
        "company_former_name": content.get("former_name"),
        "document_date": content["profile_date"],
        "last_agm_date": content["last_agm"],
        "last_annual_return_date": content["last_ar"],
        "financial_year_end": to_date(content.get("fye")),
    }


def company_constitution(content: dict) -> dict[str, Any]:
    return {
        "company_name": content["company_name"],
        "uen": content["uen"],
        "incorporation_date": content["incorporation_date"],
        "company_address": content["registered_address"],
        "share_capital": content["share_capital"],
        "issued_shares": sum(s["shares"] for s in content["subscribers"]),
        "subscribers": [
            {"name": s["name"], "address": s["address"], "id_number": s["id"], "shares": s["shares"]}
            for s in content["subscribers"]
        ],
        "quorum": content.get("quorum"),
        "adoption_date": content.get("adoption_date"),
    }


def fatca(persona: dict) -> dict[str, Any]:
    address = persona.get("registered_address") or {}
    unit = f"#{address['floor']}-{address['unit']}" if address.get("floor") and address.get("unit") else ""
    signatory = (persona.get("signatories") or [{}])[0]
    residences = persona.get("tax_residences") or []
    return {
        "company_name": persona["entity_name"],
        "company_address": _join([f"{address.get('block', '')} {address.get('street', '')}".strip(), unit,
                                  f"{address.get('city', '')} {address.get('postal_code', '')}".strip()]),
        "tin": residences[0]["tin"] if residences else None,
        "declaration_date": to_date(signatory.get("date")) or signatory.get("date"),
        "country_of_incorporation": persona.get("country_of_incorporation"),
        "tax_residences": [{"country": r.get("country"), "tin": r.get("tin")} for r in residences],
        "controlling_persons": [{"name": p.get("name")} for p in persona.get("controlling_persons") or []],
    }


def notice_of_assessment(persona: dict, tax_payable: float) -> dict[str, Any]:
    return {
        "company_name": persona["company_name"],
        "tax_reference_number": persona["tax_ref"],
        "company_address": persona["company_address"],
        "year_of_assessment": persona["year_of_assessment"],
        "noa_date": persona["noa_date"],
        "eci": persona["eci"],
        "tax_payable": tax_payable,
        "previous_assessment_date": persona.get("previous_date"),
    }


def loan_letter(*, facility_type: str, company_name: str, company_address_lines: list[str], uen: str,
                director_name: str, amount: float, offer_date: date, interest_rate: float | None = None,
                tenure: str | None = None, monthly_instalment: float | None = None,
                review_date: date | None = None) -> dict[str, Any]:
    return {
        "company_name": company_name,
        "company_address": _join(company_address_lines),
        "individual_name": director_name,
        "loan_amount": amount,
        "uen": uen,
        "facility_type": facility_type,
        "offer_date": offer_date,
        "interest_rate": interest_rate,
        "loan_tenure": tenure,
        "monthly_instalment": monthly_instalment,
        "review_date": review_date,
    }
