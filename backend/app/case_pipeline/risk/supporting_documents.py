"""
Additional supporting documents a loan officer should request from the
client, recommended from what the case's documents already show. Pure and
deterministic like case_risk.py. Run only as part of a case analysis (see
case_pipeline/service.py) and stored on that run; a re-run picks up documents
added, rejected, re-extracted or edited since.

Bank statement trade transactions:
- A large payment to a supplier needs a purchase order, and a large
  receipt from a customer needs a sales invoice, to show the money moved
  for a real trade.
- Only corporate statements accepted onto the checklist are read: a
  personal statement's credits are salary, not customer receipts.
- A transaction counts as trade unless its description marks it as
  something else (_NON_TRADE_RE: payroll, rent, utilities, card spend,
  bank charges, interest, loans, tax...) or names the account-holding
  company itself (a transfer between its own accounts). Debits are then
  supplier payments and credits customer receipts.
- "Large" is LARGE_TRANSACTION_AMOUNT or more, in the statement's currency.
- One recommendation per statement and document type, listing each
  transaction behind it with its own reason.
- Each transaction is named by its counterparty: the "counterparty"
  extraction now pulls per transaction, or for statements extracted before
  that, the name printed just before the amount in the description
  (_counterparty_from_description). With neither, the reason says "a
  supplier"/"a customer".

Source of wealth related-party balances:
- An "Amount due from related corporations" (or related companies/
  parties, intercompany, holding/subsidiary/associated company) asset on a
  company financial statement is money the company has lent within its
  group. It's only worth what will actually be repaid, so request a bank
  statement showing the repayment transactions or an intercompany
  repayment schedule.
- Any positive balance counts, in either asset section. A balance owed
  TO related parties is a liability and isn't flagged.
- One recommendation per financial statement, listing each such line.

Source of wealth borrowing growth:
- Total bank borrowings (current + non-current portions) up
  BORROWING_INCREASE_RATIO or more on the prior year means the company
  took on substantial new debt: request bank loan statements or bank loan
  repayment schedules to see the facilities and what's owed on them.
- Bank borrowings, bank/term loans and overdrafts count; finance leases
  don't (a bank loan statement wouldn't show them), nor do loans from
  directors, shareholders or related parties.
- Every line needs a prior-year figure, and the prior total must be above
  zero — otherwise there's nothing to compare against.

Bank statement coverage, concentration and loans (the measures are cross_document/signals.py's):
- Months between the company's statements that none covers: the statements for those months.
- Half or more of a statement's receipts (of at least CONCENTRATION_MIN_CREDITS) from one named
  customer: the contract or the invoices behind them.
- Loan repayments on a company statement while the case has no facility letter: the letter.

From the case analysis's own findings:
- Documents that disagree on an address (ADDRESS_MISMATCH): a bank statement from the last three
  months showing the current address.

An empty list means nothing in the case needs further evidence.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from ... import identifiers
from ..cross_document import signals
from ..models import CrossDocFinding
from ...fraud_detection.numerical_validation.common import CURRENCY_WORDS, as_number, format_money, table_rows
from ...fraud_detection.shared.field_values import decode_field_value
from ...models import (
    DocumentRecord,
    DocumentType,
    RecommendationItem,
    SupportingDocumentRecommendation,
    compute_fulfilled_requirements,
    counts_towards_checklist,
    is_personal_bank_statement,
)

LARGE_TRANSACTION_AMOUNT = 20_000.0
BORROWING_INCREASE_RATIO = 0.40

_NON_TRADE_RE = re.compile(
    r"\b("
    r"SALARY|PAYROLL|CPF|IRAS|TAX|GST|RENT|RENTAL|UTILITY|UTILITIES|TELECOM|"
    r"INTEREST|SERVICE CHARGE|SVC CHG|FEES?|COMMISSION|ATM|CASH|CARD|"
    r"LOAN|REPAYMENT|INSTAL?MENT|DIVIDEND|REVERSAL|REFUND"
    r")\b",
    re.IGNORECASE,
)

# The name in a description like "Funds Trf - FAST MERIDIAN LOGISTICS
# SERVICES SGD 22,065 EBACT6317..." is what's printed before the amount.
_NAME_BEFORE_AMOUNT_RE = re.compile(rf"(.+?)\s+(?:{CURRENCY_WORDS})\s*\d[\d,]*(?:\.\d+)?\b", re.IGNORECASE)
# Payment channel words that come before the name, never part of it.
_CHANNEL_WORDS = frozenset({
    "-", "CR", "DR", "FAST", "FUNDS", "GIRO", "IBG", "INWARD", "MEPS", "OTHER", "OUTWARD",
    "PAYMENT", "PAYNOW", "REMITTANCE", "RTF", "TRANSFER", "TRF", "TT",
})


def _counterparty_from_description(description: str) -> str | None:
    match = _NAME_BEFORE_AMOUNT_RE.search(description)
    if not match:
        return None
    name: list[str] = []
    # Walk back from the amount until a reference number or channel word.
    for token in reversed(match.group(1).split()):
        if any(c.isdigit() for c in token) or ":" in token or token.upper() in _CHANNEL_WORDS:
            break
        name.insert(0, token)
    return " ".join(name) or None


def _counterparty(row: dict, description: str) -> str | None:
    extracted = row.get("counterparty")
    if isinstance(extracted, str) and extracted.strip():
        return extracted.strip()
    return _counterparty_from_description(description)


def _sentence(text: str) -> str:
    """Ends text with one full stop, also when a name ends in "LTD."."""
    return text if text.endswith(".") else f"{text}."


def _is_trade(description: str, company_name: str | None) -> bool:
    if _NON_TRADE_RE.search(description):
        return False
    # A transfer to or from the company's own account is not trade.
    return not identifiers.mentions_company(description, company_name)


def _recommend_for_statement(document: DocumentRecord) -> list[SupportingDocumentRecommendation]:
    fields = document.extracted_fields or {}
    company_name = fields.get("company_name")
    supplier_payments: list[RecommendationItem] = []
    customer_receipts: list[RecommendationItem] = []
    for row in table_rows(decode_field_value(fields.get("transactions"))):
        amount = as_number(row.get("amount"))
        description = str(row.get("description") or "").strip()
        if amount is None or abs(amount) < LARGE_TRANSACTION_AMOUNT or not _is_trade(description, company_name):
            continue
        date = str(row.get("date") or "").strip() or None
        on_date = f" on {date}" if date else ""
        counterparty = _counterparty(row, description)
        if amount < 0:
            supplier_payments.append(RecommendationItem(
                date=date, description=description, amount=amount, counterparty=counterparty,
                reason=_sentence(f"Payment of {format_money(-amount)}{on_date} to {counterparty or 'a supplier'}"),
            ))
        else:
            customer_receipts.append(RecommendationItem(
                date=date, description=description, amount=amount, counterparty=counterparty,
                reason=_sentence(f"Receipt of {format_money(amount)}{on_date} from {counterparty or 'a customer'}"),
            ))

    threshold = format_money(LARGE_TRANSACTION_AMOUNT)
    recommendations: list[SupportingDocumentRecommendation] = []
    groups: list[tuple[Literal["purchase_order", "sales_invoice"], list[RecommendationItem], str]] = [
        ("purchase_order", supplier_payments, "supplier payment"),
        ("sales_invoice", customer_receipts, "customer receipt"),
    ]
    for document_type, transactions, kind in groups:
        if not transactions:
            continue
        count = len(transactions)
        name = document_type.replace("_", " ")
        recommendations.append(SupportingDocumentRecommendation(
            document_type=document_type,
            title=name.title(),
            reason=(
                f"{document.filename} shows {count} large {kind}{'s' if count > 1 else ''} "
                f"(of at least {threshold})."
            ),
            request=(
                f"Request a {name} for this transaction:"
                if count == 1
                else f"Request {name}s for these transactions:"
            ),
            source_document_id=document.id,
            source_filename=document.filename,
            items=transactions,
        ))
    return recommendations


_RELATED_PARTY_RE = re.compile(
    r"related\s+(corporations?|compan(y|ies)|part(y|ies))|inter-?company|holding\s+company|"
    r"subsidiar(y|ies)|associated\s+compan(y|ies)",
    re.IGNORECASE,
)
_OWED_BY_COMPANY_RE = re.compile(r"\bdue\s+to\b|payable", re.IGNORECASE)
_ASSET_SECTIONS = {"current_assets", "non_current_assets", None, ""}


_BANK_BORROWING_RE = re.compile(r"\bborrowings?\b|\bbank\s+loans?\b|\bterm\s+loans?\b|\boverdrafts?\b", re.IGNORECASE)
_NOT_BANK_LENDER_RE = re.compile(r"director|shareholder|related|inter-?company|holding|subsidiar|lease", re.IGNORECASE)
_LIABILITY_SECTIONS = {"current_liabilities", "non_current_liabilities"}


def _with_prior_year(amount: float, prior: float | None) -> str:
    """ "1,257,340.00 (prior year: 883,160.00)" """
    return format_money(amount) + (f" (prior year: {format_money(prior)})" if prior is not None else "")


def _borrowing_growth(document: DocumentRecord, rows: list[dict]) -> list[SupportingDocumentRecommendation]:
    lines: list[tuple[str, float, float]] = []
    for row in rows:
        label = str(row.get("label") or "").strip()
        if (
            row.get("section") not in _LIABILITY_SECTIONS
            or label.lower().startswith("total")
            or not _BANK_BORROWING_RE.search(label)
            or _NOT_BANK_LENDER_RE.search(label)
        ):
            continue
        amount = as_number(row.get("amount"))
        prior = as_number(row.get("amount_prior_year"))
        if amount is None or prior is None:
            return []
        lines.append((label, amount, prior))

    total, prior_total = sum(line[1] for line in lines), sum(line[2] for line in lines)
    if prior_total <= 0 or total < prior_total * (1 + BORROWING_INCREASE_RATIO):
        return []
    portions = [
        RecommendationItem(
            description=label, amount=amount, prior_amount=prior, reason=f"{label}: {_with_prior_year(amount, prior)}."
        )
        for label, amount, prior in lines
    ]
    # The request is for the total (a loan statement covers a whole facility,
    # both portions of it); the portions only show where the increase sits.
    item = portions[0] if len(portions) == 1 else RecommendationItem(
        description="Total bank borrowings", amount=total, prior_amount=prior_total,
        reason=f"Total bank borrowings: {_with_prior_year(total, prior_total)}.",
        breakdown=portions,
    )
    return [SupportingDocumentRecommendation(
        document_type="bank_loan_evidence",
        title="Bank Loan Statements or Bank Loan Repayment Schedules",
        reason=(
            f"{document.filename} shows a {total / prior_total - 1:.1%} increase in total bank borrowings "
            f"compared with the prior year, exceeding the {BORROWING_INCREASE_RATIO:.0%} threshold."
        ),
        request="Request bank loan statements or bank loan repayment schedules for this amount:",
        source_document_id=document.id,
        source_filename=document.filename,
        items=[item],
    )]


def _related_party_balances(document: DocumentRecord, rows: list[dict]) -> list[SupportingDocumentRecommendation]:
    items: list[RecommendationItem] = []
    for row in rows:
        label = str(row.get("label") or "").strip()
        amount = as_number(row.get("amount"))
        if (
            amount is None
            or amount <= 0
            or row.get("section") not in _ASSET_SECTIONS
            or not _RELATED_PARTY_RE.search(label)
            or _OWED_BY_COMPANY_RE.search(label)
        ):
            continue
        prior = as_number(row.get("amount_prior_year"))
        items.append(RecommendationItem(
            description=label, amount=amount, prior_amount=prior,
            reason=_sentence(f"{label}: {_with_prior_year(amount, prior)}"),
        ))
    if not items:
        return []
    return [SupportingDocumentRecommendation(
        document_type="related_party_repayment_evidence",
        title="Bank Statement or Intercompany Repayment Schedule",
        reason=f"{document.filename} shows money owed to the company by related parties.",
        request=(
            "Request a bank statement showing the repayment transactions or an intercompany "
            f"repayment schedule for {'this amount' if len(items) == 1 else 'these amounts'}:"
        ),
        source_document_id=document.id,
        source_filename=document.filename,
        items=items,
    )]


def _recommend_for_financial_statement(document: DocumentRecord) -> list[SupportingDocumentRecommendation]:
    rows = table_rows(decode_field_value((document.extracted_fields or {}).get("balance_sheet_items")))
    return _related_party_balances(document, rows) + _borrowing_growth(document, rows)


def _month_name(month: str) -> str:
    """ "2026-05" -> "May 2026" """
    return date(int(month[:4]), int(month[5:]), 1).strftime("%B %Y")


def _address_mismatch(
    findings: list[CrossDocFinding], documents: list[DocumentRecord],
) -> list[SupportingDocumentRecommendation]:
    """
    The documents disagree on an address (case analysis, ADDRESS_MISMATCH): a recent bank statement showing the
    current one settles which is right. One recommendation for every such finding.
    """
    by_id = {d.id: d for d in documents}
    items: list[RecommendationItem] = []
    source: DocumentRecord | None = None
    for finding in findings:
        if "ADDRESS_MISMATCH" not in finding.breach_flags:
            continue
        cited = next((by_id[i] for i in finding.document_ids if i in by_id), None)
        if cited is None:
            continue
        source = source or cited
        items.append(RecommendationItem(description=finding.summary, reason=_sentence(finding.summary)))
    if source is None:
        return []
    return [SupportingDocumentRecommendation(
        document_type="current_address_evidence",
        title="Recent Bank Statement Showing the Current Address",
        reason="The case's documents give different addresses.",
        request=(
            "Request a bank statement from the last three months showing the current address, to confirm which "
            f"address is right for {'this difference' if len(items) == 1 else 'these differences'}:"
        ),
        source_document_id=source.id,
        source_filename=source.filename,
        items=items,
    )]


def _missing_statement_months(statements: list[DocumentRecord]) -> list[SupportingDocumentRecommendation]:
    """Months between the company's statements that none covers: the statements for those months."""
    if len(statements) < 2:
        return []
    missing = signals.missing_months([signals.covered_months(s.extracted_fields or {}) for s in statements])
    if not missing:
        return []
    latest = statements[-1]
    names = [_month_name(m) for m in missing]
    return [SupportingDocumentRecommendation(
        document_type="missing_statement_months",
        title=f"Bank Statements for {', '.join(names)}",
        reason=f"The company's bank statements leave {len(missing)} month{'s' if len(missing) > 1 else ''} uncovered.",
        request=f"Request the bank statement{'s' if len(missing) > 1 else ''} for:",
        source_document_id=latest.id,
        source_filename=latest.filename,
        items=[RecommendationItem(description=m, reason=f"No statement covers {_month_name(m)}.") for m in missing],
    )]


def _customer_concentration(document: DocumentRecord) -> list[SupportingDocumentRecommendation]:
    """Over half of a company statement's receipts from one named customer: the contract or invoices behind them."""
    fields = document.extracted_fields or {}
    share = signals.concentration(fields, fields.get("company_name") or signals.value(fields, "entity_name"))
    if (
        not share.get("available") or share["credits"] < signals.CONCENTRATION_MIN_CREDITS
        or share["top_share"] < signals.CONCENTRATION_SHARE or share["top_counterparty"] == "(unnamed)"
    ):
        return []
    customer = share["top_counterparty"]
    return [SupportingDocumentRecommendation(
        document_type="customer_contract",
        title=f"Contract or Invoices with {customer}",
        reason=(
            f"{document.filename} shows {share['top_share']:.0%} of the company's receipts coming from one customer."
        ),
        request="Request the contract, or the invoices, behind these receipts:",
        source_document_id=document.id,
        source_filename=document.filename,
        items=[RecommendationItem(
            description=customer, amount=share["top_total"], counterparty=customer,
            reason=_sentence(
                f"{format_money(share['top_total'])} of {format_money(share['total_credits'])} received from {customer}"
            ),
        )],
    )]


_MAX_REPAYMENT_ITEMS = 5


def _facility_letter(document: DocumentRecord) -> list[SupportingDocumentRecommendation]:
    """Loan repayments on a company statement while the case has no facility letter: the letter for that loan."""
    repayments = signals.loan_repayments(document.extracted_fields or {})
    if not repayments:
        return []
    items = []
    for movement in repayments[:_MAX_REPAYMENT_ITEMS]:
        on_date = f" on {movement.day.strftime('%d/%m/%Y')}" if movement.day else ""
        to = movement.counterparty or "a lender"
        items.append(RecommendationItem(
            date=movement.day.isoformat() if movement.day else None, description=movement.description,
            amount=movement.amount, counterparty=movement.counterparty or None,
            reason=_sentence(f"Repayment of {format_money(-movement.amount)}{on_date} to {to}"),
        ))
    count = len(repayments)
    return [SupportingDocumentRecommendation(
        document_type="facility_letter",
        title="Facility Letter",
        reason=f"{document.filename} shows {count} loan repayment{'s' if count > 1 else ''}, and the case has no facility letter.",
        request="Request the facility letter for the loan behind these repayments:",
        source_document_id=document.id,
        source_filename=document.filename,
        items=items,
    )]


def recommend_supporting_documents(
    documents: list[DocumentRecord], findings: list[CrossDocFinding] | None = None,
) -> list[SupportingDocumentRecommendation]:
    """
    documents: the case's documents with plain {field_name: value} extracted_fields. findings: the run's
    cross-document findings, for the requests a finding makes (a recent statement for an address difference).
    """
    recommendations: list[SupportingDocumentRecommendation] = []
    counted = [d for d in documents if counts_towards_checklist(d)]
    has_facility_letter = any(d.document_type == DocumentType.LIST_OF_LOANS.value for d in counted)
    company_statements: list[DocumentRecord] = []
    for document in counted:
        if document.document_type == DocumentType.BANK_STATEMENT.value:
            if not is_personal_bank_statement(document.document_type, document.extracted_fields):
                company_statements.append(document)
                recommendations.extend(_recommend_for_statement(document))
                recommendations.extend(_customer_concentration(document))
                if not has_facility_letter:
                    recommendations.extend(_facility_letter(document))
        # Only a company's own financial statement: personal source of wealth
        # (e.g. a brokerage statement) fulfils source_of_personal_wealth instead.
        elif DocumentType.SOURCE_OF_WEALTH.value in compute_fulfilled_requirements(
            document.document_type, document.extracted_fields
        ):
            recommendations.extend(_recommend_for_financial_statement(document))
    recommendations.extend(_missing_statement_months(company_statements))
    recommendations.extend(_address_mismatch(findings or [], documents))
    return recommendations
