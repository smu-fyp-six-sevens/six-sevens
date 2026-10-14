"""
Additional supporting documents a loan officer should request from the
client, recommended from what the case's documents already show. Pure and
deterministic like case_risk.py, and computed live on every case read (see
case_service.build_summary), never stored — so it follows documents being
added, rejected, re-extracted or edited.

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

An empty list means nothing in the case needs further evidence.
"""

from __future__ import annotations

import re
from typing import Literal

from ...fraud_detection.numerical_validation.common import _CURRENCY_WORDS, _as_number, _decode, _money, _rows
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
_NAME_BEFORE_AMOUNT_RE = re.compile(rf"(.+?)\s+(?:{_CURRENCY_WORDS})\s*\d[\d,]*(?:\.\d+)?\b", re.IGNORECASE)
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


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text.upper()).split())


def _is_trade(description: str, company_name: str | None) -> bool:
    if _NON_TRADE_RE.search(description):
        return False
    return not (company_name and _normalise(company_name) in _normalise(description))


def _recommend_for_statement(document: DocumentRecord) -> list[SupportingDocumentRecommendation]:
    fields = document.extracted_fields or {}
    company_name = fields.get("company_name")
    supplier_payments: list[RecommendationItem] = []
    customer_receipts: list[RecommendationItem] = []
    for row in _rows(_decode(fields.get("transactions"))):
        amount = _as_number(row.get("amount"))
        description = str(row.get("description") or "").strip()
        if amount is None or abs(amount) < LARGE_TRANSACTION_AMOUNT or not _is_trade(description, company_name):
            continue
        date = str(row.get("date") or "").strip() or None
        on_date = f" on {date}" if date else ""
        counterparty = _counterparty(row, description)
        if amount < 0:
            supplier_payments.append(RecommendationItem(
                date=date, description=description, amount=amount, counterparty=counterparty,
                reason=_sentence(f"Payment of {_money(-amount)}{on_date} to {counterparty or 'a supplier'}"),
            ))
        else:
            customer_receipts.append(RecommendationItem(
                date=date, description=description, amount=amount, counterparty=counterparty,
                reason=_sentence(f"Receipt of {_money(amount)}{on_date} from {counterparty or 'a customer'}"),
            ))

    threshold = _money(LARGE_TRANSACTION_AMOUNT)
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
    return _money(amount) + (f" (prior year: {_money(prior)})" if prior is not None else "")


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
        amount = _as_number(row.get("amount"))
        prior = _as_number(row.get("amount_prior_year"))
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
        amount = _as_number(row.get("amount"))
        if (
            amount is None
            or amount <= 0
            or row.get("section") not in _ASSET_SECTIONS
            or not _RELATED_PARTY_RE.search(label)
            or _OWED_BY_COMPANY_RE.search(label)
        ):
            continue
        prior = _as_number(row.get("amount_prior_year"))
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
    rows = _rows(_decode((document.extracted_fields or {}).get("balance_sheet_items")))
    return _related_party_balances(document, rows) + _borrowing_growth(document, rows)


def recommend_supporting_documents(
    documents: list[DocumentRecord],
) -> list[SupportingDocumentRecommendation]:
    """documents: the case's documents with plain {field_name: value} extracted_fields."""
    recommendations: list[SupportingDocumentRecommendation] = []
    for document in documents:
        if not counts_towards_checklist(document):
            continue
        if document.document_type == DocumentType.BANK_STATEMENT.value:
            if not is_personal_bank_statement(document.document_type, document.extracted_fields):
                recommendations.extend(_recommend_for_statement(document))
        # Only a company's own financial statement: personal source of wealth
        # (e.g. a brokerage statement) fulfils source_of_personal_wealth instead.
        elif DocumentType.SOURCE_OF_WEALTH.value in compute_fulfilled_requirements(
            document.document_type, document.extracted_fields
        ):
            recommendations.extend(_recommend_for_financial_statement(document))
    return recommendations
