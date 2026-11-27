from __future__ import annotations

import json
import pytest

from app.case_pipeline.models import CrossDocFinding, FindingCategory
from app.models import DocumentRecord, DocumentStatus, RoutingStatus
from app.case_pipeline.risk.supporting_documents import _counterparty_from_description, recommend_supporting_documents

COMPANY = "ANCHORPOINT INSURANCE BROKERS PTE LTD"

SUPPLIER_PAYMENT = {
    "date": "13/06/2026",
    "description": "Funds Trf - FAST MERIDIAN LOGISTICS SERVICES SGD 22,065 EBACT63178455117 0016RF2973597",
    "amount": -22064.76,
    "balance": 17990.65,
}
CUSTOMER_RECEIPT = {
    "date": "29/06/2026",
    "description": "Inward Cr - FAST OTHER GREENFIELD RETAIL GROUP PTE. LTD. SGD 31,343 SG3P872201974171 C615637982089",
    "amount": 31343.12,
    "balance": 44765.62,
}


def _statement(
    transactions: list[dict] | str,
    *,
    document_id: str = "statement",
    status: DocumentStatus = DocumentStatus.ANALYZED,
    routing_status: RoutingStatus = RoutingStatus.ACCEPTED,
    document_type: str = "bank_statement",
    **fields,
) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename=f"{document_id}.pdf",
        document_type=document_type,
        classification_confidence=0.95,
        routing_status=routing_status,
        status=status,
        uploaded_at="2026-10-01T00:00:00Z",
        extracted_fields={"company_name": COMPANY, "transactions": transactions, **fields},
    )


def test_large_supplier_payment_recommends_a_purchase_order() -> None:
    [recommendation] = recommend_supporting_documents([_statement([SUPPLIER_PAYMENT])])

    assert recommendation.document_type == "purchase_order"
    assert recommendation.title == "Purchase Order"
    assert recommendation.request == "Request a purchase order for this transaction:"
    assert recommendation.source_document_id == "statement"
    assert "1 large supplier payment" in recommendation.reason
    [transaction] = recommendation.items
    assert transaction.amount == -22064.76
    assert transaction.date == "13/06/2026"
    assert transaction.counterparty == "MERIDIAN LOGISTICS SERVICES"
    assert transaction.reason == "Payment of 22,064.76 on 13/06/2026 to MERIDIAN LOGISTICS SERVICES."


def test_large_customer_receipt_recommends_a_sales_invoice() -> None:
    [recommendation] = recommend_supporting_documents([_statement([CUSTOMER_RECEIPT])])

    assert recommendation.document_type == "sales_invoice"
    assert recommendation.items[0].reason == (
        "Receipt of 31,343.12 on 29/06/2026 from GREENFIELD RETAIL GROUP PTE. LTD."
    )


def test_one_recommendation_per_statement_and_document_type() -> None:
    second_receipt = {**CUSTOMER_RECEIPT, "date": "02/07/2026", "amount": 32009.95}
    recommendations = recommend_supporting_documents(
        [_statement([CUSTOMER_RECEIPT, SUPPLIER_PAYMENT, second_receipt])]
    )

    assert [r.document_type for r in recommendations] == ["purchase_order", "sales_invoice"]
    assert len(recommendations[1].items) == 2
    assert recommendations[1].request == "Request sales invoices for these transactions:"
    assert recommendations[1].reason == "statement.pdf shows 2 large customer receipts (of at least 20,000.00)."


def test_transactions_below_the_threshold_need_nothing() -> None:
    small = [{**SUPPLIER_PAYMENT, "amount": -7420.70}, {**CUSTOMER_RECEIPT, "amount": 19999.99}]

    assert recommend_supporting_documents([_statement(small)]) == []


def test_amount_exactly_at_the_threshold_is_large() -> None:
    assert len(recommend_supporting_documents([_statement([{**CUSTOMER_RECEIPT, "amount": 20000}])])) == 1


def test_non_trade_transactions_are_ignored() -> None:
    non_trade = [
        {"date": "25/06/2026", "description": "Funds Transfer-IB M:+6586270441 SALARY PAYMENT SGD 26,361", "amount": -26361.2},
        {"date": "01/06/2026", "description": "GIRO Payment PREMISES RENTAL JUN26", "amount": -24512.71},
        {"date": "15/06/2026", "description": "GIRO Payment SP SERVICES LTD UTILITIES", "amount": -20630.52},
        {"date": "07/06/2026", "description": "Card Transaction COURTS MEGASTORE SI NG", "amount": -21314.71},
        {"date": "01/07/2026", "description": "TERM LOAN DISBURSEMENT", "amount": 150000.0},
        {"date": "30/06/2026", "description": "INTEREST CREDIT", "amount": 20500.0},
        {"date": "30/06/2026", "description": "IRAS GIRO CORPORATE TAX", "amount": -40000.0},
    ]

    assert recommend_supporting_documents([_statement(non_trade)]) == []


def test_transfer_between_the_companys_own_accounts_is_not_trade() -> None:
    own_transfer = {**CUSTOMER_RECEIPT, "description": f"Inward Cr - FAST OTHER {COMPANY.replace(' LTD', ' LTD.')} SGD 50,000"}

    assert recommend_supporting_documents([_statement([own_transfer])]) == []


def test_an_own_account_transfer_is_recognised_however_the_suffix_is_written() -> None:
    own_transfer = {**SUPPLIER_PAYMENT, "description": "IBG TRANSFER TO ANCHORPOINT INSURANCE BROKERS PTE. LTD. SGD 50,000"}
    statement = _statement([own_transfer], company_name="Anchorpoint Insurance Brokers Private Limited")

    assert recommend_supporting_documents([statement]) == []
    # Another company that only shares the first word is still trade.
    other = {**SUPPLIER_PAYMENT, "description": "IBG TRANSFER TO ANCHORPOINT LOGISTICS PTE LTD SGD 50,000"}
    assert recommend_supporting_documents([_statement([other], company_name="Anchorpoint Insurance Brokers Private Limited")])


def test_amounts_and_transactions_given_as_strings_are_read() -> None:
    rows = [{**SUPPLIER_PAYMENT, "amount": "-22,064.76"}]

    [recommendation] = recommend_supporting_documents([_statement(json.dumps(rows))])

    assert recommendation.items[0].amount == -22064.76


def test_personal_statement_is_ignored() -> None:
    document = _statement([CUSTOMER_RECEIPT], company_name=None, individual_name="MUHAMMAD DANISH BIN ISMAIL")

    assert recommend_supporting_documents([document]) == []


def test_documents_not_counting_towards_the_checklist_are_ignored() -> None:
    documents = [
        _statement([SUPPLIER_PAYMENT], document_id="rejected", status=DocumentStatus.REJECTED),
        _statement([SUPPLIER_PAYMENT], document_id="manual", routing_status=RoutingStatus.MANUAL_REVIEW),
        _statement([SUPPLIER_PAYMENT], document_id="payslip", document_type="payslip"),
    ]

    assert recommend_supporting_documents(documents) == []


def test_case_with_nothing_needing_evidence_returns_an_empty_list() -> None:
    assert recommend_supporting_documents([]) == []
    assert recommend_supporting_documents([_statement([])]) == []


# --- counterparty -----------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # UOB
        ("Funds Trf - FAST MERIDIAN LOGISTICS SERVICES SGD 22,065 EBACT63178455117 0016RF2973597",
         "MERIDIAN LOGISTICS SERVICES"),
        ("Inward Cr - FAST OTHER GREENFIELD RETAIL GROUP PTE. LTD. SGD 31,343 SG3P872201974171 C615637982089",
         "GREENFIELD RETAIL GROUP PTE. LTD."),
        # DBS
        ("REMITTANCE TRANSFER OF FUNDS RTF EBACT12345678901 0016RF1234567 SEAWAY FREIGHT (S) PTE LTD SGD 22,506",
         "SEAWAY FREIGHT (S) PTE LTD"),
        ("Inward PayNow PAYMENT SG3P123456789012 C123456789012 OTHER ORIENT PACIFIC SUPPLIES PTE LTD SGD 43,553",
         "ORIENT PACIFIC SUPPLIES PTE LTD"),
        # OCBC
        ("EBACT12345678901 GIRO TAN HENG PACKAGING PTE LTD SGD 26,759", "TAN HENG PACKAGING PTE LTD"),
        # No amount to anchor on, or nothing but references before it.
        ("ONLINE TRANSFER", None),
        ("FAST PAYMENT EBACT12345678901 SGD 22,000", None),
    ],
)
def test_counterparty_is_read_from_the_description(description: str, expected: str | None) -> None:
    assert _counterparty_from_description(description) == expected


def test_extracted_counterparty_wins_over_the_description() -> None:
    row = {**SUPPLIER_PAYMENT, "counterparty": "Meridian Logistics Services"}

    [recommendation] = recommend_supporting_documents([_statement([row])])

    assert recommendation.items[0].counterparty == "Meridian Logistics Services"


def test_unnamed_counterparty_falls_back_to_a_generic_reason() -> None:
    row = {**CUSTOMER_RECEIPT, "description": "ONLINE TRANSFER"}

    [recommendation] = recommend_supporting_documents([_statement([row])])

    assert recommendation.items[0].counterparty is None
    assert recommendation.items[0].reason == "Receipt of 31,343.12 on 29/06/2026 from a customer."


# --- source of wealth: related-party balances --------------------------------


def _financial_statement(
    balance_sheet_items: list[dict] | str, status: DocumentStatus = DocumentStatus.ANALYZED
) -> DocumentRecord:
    return DocumentRecord(
        id="financials",
        case_id="case-1",
        filename="BrightSpark_BalanceSheet_Audited.pdf",
        document_type="source_of_wealth",
        classification_confidence=0.95,
        routing_status=RoutingStatus.ACCEPTED,
        status=status,
        uploaded_at="2026-10-01T00:00:00Z",
        extracted_fields={
            "company_name": "BrightSpark Digital Marketing Pte Ltd",
            "uen": "202145123W",
            "balance_sheet_items": balance_sheet_items,
        },
    )


RELATED_CORPORATIONS = {
    "section": "current_assets", "label": "Amount due from related corporations",
    "amount": 352920, "amount_prior_year": 127900, "note": None,
}
BRIGHTSPARK_BALANCE_SHEET = [
    {"section": "non_current_assets", "label": "Property, plant and equipment", "amount": 830610, "amount_prior_year": 738070},
    {"section": "current_assets", "label": "Trade receivables", "amount": 409980, "amount_prior_year": 257420},
    RELATED_CORPORATIONS,
    {"section": "current_liabilities", "label": "Trade payables", "amount": 518520, "amount_prior_year": 380080},
    {"section": "equity", "label": "Share capital", "amount": 500000, "amount_prior_year": 500000},
]


def test_amount_due_from_related_corporations_requests_repayment_evidence() -> None:
    [recommendation] = recommend_supporting_documents([_financial_statement(BRIGHTSPARK_BALANCE_SHEET)])

    assert recommendation.document_type == "related_party_repayment_evidence"
    assert recommendation.title == "Bank Statement or Intercompany Repayment Schedule"
    assert recommendation.reason == (
        "BrightSpark_BalanceSheet_Audited.pdf shows money owed to the company by related parties."
    )
    assert recommendation.request == (
        "Request a bank statement showing the repayment transactions or an intercompany "
        "repayment schedule for this amount:"
    )
    [item] = recommendation.items
    assert item.amount == 352920
    assert item.reason == "Amount due from related corporations: 352,920.00 (prior year: 127,900.00)."


@pytest.mark.parametrize(
    "label",
    [
        "Amount due from related companies",
        "Amount due from related parties",
        "Intercompany receivables",
        "Amount due from holding company",
        "Amount due from subsidiaries",
    ],
)
def test_other_related_party_wordings_are_recognised(label: str) -> None:
    rows = [{**RELATED_CORPORATIONS, "label": label}]

    assert len(recommend_supporting_documents([_financial_statement(rows)])) == 1


@pytest.mark.parametrize(
    "row",
    [
        # Owed BY the company: a liability, not money to recover.
        {**RELATED_CORPORATIONS, "section": "current_liabilities", "label": "Amount due to related corporations"},
        {**RELATED_CORPORATIONS, "label": "Amount due to related corporations"},
        # Nothing outstanding this year.
        {**RELATED_CORPORATIONS, "amount": 0},
        # Not a related party.
        {**RELATED_CORPORATIONS, "label": "Trade receivables"},
    ],
)
def test_balances_that_need_no_repayment_evidence(row: dict) -> None:
    assert recommend_supporting_documents([_financial_statement([row])]) == []


def test_balance_sheet_given_as_a_string_is_read() -> None:
    assert len(recommend_supporting_documents([_financial_statement(json.dumps(BRIGHTSPARK_BALANCE_SHEET))])) == 1


def test_rejected_financial_statement_is_ignored() -> None:
    document = _financial_statement(BRIGHTSPARK_BALANCE_SHEET, status=DocumentStatus.REJECTED)

    assert recommend_supporting_documents([document]) == []


# --- source of wealth: borrowing growth ---------------------------------------


def _borrowings(current: tuple[float, float], non_current: tuple[float, float]) -> list[dict]:
    """(this year, prior year) for each portion."""
    return [
        {"section": "current_liabilities", "label": "Bank borrowings, current portion",
         "amount": current[0], "amount_prior_year": current[1]},
        {"section": "non_current_liabilities", "label": "Bank borrowings, non-current portion",
         "amount": non_current[0], "amount_prior_year": non_current[1]},
    ]


def test_borrowings_up_40_percent_request_bank_loan_evidence() -> None:
    rows = _borrowings(current=(516_090, 343_330), non_current=(1_334_700, 948_200))

    [recommendation] = recommend_supporting_documents([_financial_statement(rows)])

    assert recommendation.document_type == "bank_loan_evidence"
    assert recommendation.title == "Bank Loan Statements or Bank Loan Repayment Schedules"
    assert recommendation.reason == (
        "BrightSpark_BalanceSheet_Audited.pdf shows a 43.3% increase in total bank borrowings "
        "compared with the prior year, exceeding the 40% threshold."
    )
    assert recommendation.request == "Request bank loan statements or bank loan repayment schedules for this amount:"
    [total] = recommendation.items
    assert total.reason == "Total bank borrowings: 1,850,790.00 (prior year: 1,291,530.00)."
    assert (total.amount, total.prior_amount) == (1_850_790, 1_291_530)
    assert [(p.amount, p.prior_amount) for p in total.breakdown] == [(516_090, 343_330), (1_334_700, 948_200)]
    assert [portion.reason for portion in total.breakdown] == [
        "Bank borrowings, current portion: 516,090.00 (prior year: 343,330.00).",
        "Bank borrowings, non-current portion: 1,334,700.00 (prior year: 948,200.00).",
    ]


def test_brightspark_balance_sheet_requests_both_documents() -> None:
    rows = BRIGHTSPARK_BALANCE_SHEET + _borrowings(current=(349_440, 295_360), non_current=(907_900, 587_800))

    recommendations = recommend_supporting_documents([_financial_statement(rows)])

    assert [r.document_type for r in recommendations] == ["related_party_repayment_evidence", "bank_loan_evidence"]
    assert "shows a 42.4% increase" in recommendations[1].reason


def test_borrowings_up_less_than_40_percent_need_nothing() -> None:
    rows = _borrowings(current=(400_000, 300_000), non_current=(600_000, 500_000))  # +25%

    assert recommend_supporting_documents([_financial_statement(rows)]) == []


def test_a_single_borrowing_line_has_no_total_row() -> None:
    rows = [{"section": "non_current_liabilities", "label": "Term loan", "amount": 700_000, "amount_prior_year": 500_000}]

    [recommendation] = recommend_supporting_documents([_financial_statement(rows)])

    [item] = recommendation.items
    assert item.description == "Term loan"
    assert item.breakdown == []


@pytest.mark.parametrize(
    "extra",
    [
        # Not bank debt: these never count towards the total.
        {"section": "current_liabilities", "label": "Finance lease liabilities, current portion"},
        {"section": "current_liabilities", "label": "Amount due to director"},
        {"section": "current_liabilities", "label": "Loan from shareholder"},
        # A subtotal row would count the same debt twice.
        {"section": "non_current_liabilities", "label": "Total bank borrowings"},
    ],
)
def test_only_bank_borrowing_lines_are_totalled(extra: dict) -> None:
    rows = _borrowings(current=(400_000, 300_000), non_current=(600_000, 500_000)) + [
        {**extra, "amount": 5_000_000, "amount_prior_year": 1}
    ]

    assert recommend_supporting_documents([_financial_statement(rows)]) == []


def test_borrowings_without_a_prior_year_cannot_be_compared() -> None:
    rows = _borrowings(current=(400_000, 0), non_current=(600_000, 0))
    rows[0]["amount_prior_year"] = None

    assert recommend_supporting_documents([_financial_statement(rows)]) == []
    assert recommend_supporting_documents([_financial_statement(_borrowings((400_000, 0), (600_000, 0)))]) == []


def test_borrowings_up_exactly_40_percent_are_flagged() -> None:
    rows = _borrowings(current=(140_000, 100_000), non_current=(280_000, 200_000))

    assert len(recommend_supporting_documents([_financial_statement(rows)])) == 1


def test_personal_source_of_wealth_is_ignored() -> None:
    document = _financial_statement(BRIGHTSPARK_BALANCE_SHEET)
    document.extracted_fields = {"account_holder_name": "TAN WEI MING", "balance_sheet_items": BRIGHTSPARK_BALANCE_SHEET}

    assert recommend_supporting_documents([document]) == []


# --- across the case -----------------------------------------------------------


def test_every_document_in_the_case_is_read() -> None:
    documents = [
        _statement([SUPPLIER_PAYMENT], document_id="june"),
        _statement([CUSTOMER_RECEIPT], document_id="july"),
        _financial_statement(BRIGHTSPARK_BALANCE_SHEET),
    ]

    recommendations = recommend_supporting_documents(documents)

    assert [(r.source_document_id, r.document_type) for r in recommendations] == [
        ("june", "purchase_order"),
        ("july", "sales_invoice"),
        ("financials", "related_party_repayment_evidence"),
    ]


def test_several_related_party_balances_are_listed_together() -> None:
    rows = [RELATED_CORPORATIONS, {**RELATED_CORPORATIONS, "label": "Amount due from holding company", "amount": 50_000}]

    [recommendation] = recommend_supporting_documents([_financial_statement(rows)])

    assert len(recommendation.items) == 2
    assert recommendation.request.endswith("for these amounts:")


def test_related_party_balance_without_a_prior_year() -> None:
    rows = [{**RELATED_CORPORATIONS, "amount_prior_year": None}]

    [recommendation] = recommend_supporting_documents([_financial_statement(rows)])

    assert recommendation.items[0].reason == "Amount due from related corporations: 352,920.00."



# --- The newer rules: address, missing months, concentration, facility letter --------------


def _address_finding(summary: str, document_ids: list[str], flags: list[str] | None = None) -> CrossDocFinding:
    return CrossDocFinding(
        id=f"xd-{summary[:4]}", hypothesis_id="det", summary=summary, rationale="", severity="medium",
        category=FindingCategory.CROSS_DOCUMENT_VARIANCE, document_ids=document_ids,
        breach_flags=["ADDRESS_MISMATCH"] if flags is None else flags,
    )


def _personal_statement(document_id: str = "por") -> DocumentRecord:
    return _statement([], document_id=document_id, entity_type="individual", individual_name="TOH YONG SHENG")


def test_an_address_mismatch_asks_for_a_recent_statement_showing_the_current_address() -> None:
    # The recommendation the score-drop demo relies on: the old statement's address against the rest of the case.
    por = _personal_statement()
    finding = _address_finding('Toh Yong Sheng\'s address is "236 Serangoon Avenue 3" on the proof of address', ["por"])

    [recommendation] = recommend_supporting_documents([por], [finding])

    assert recommendation.title == "Recent Bank Statement Showing the Current Address"
    assert recommendation.source_document_id == "por"
    assert "this difference" in recommendation.request
    assert [i.description for i in recommendation.items] == [finding.summary]


def test_several_address_findings_make_one_recommendation_listing_each() -> None:
    por, other = _personal_statement(), _personal_statement("por-2")
    findings = [_address_finding("First address difference", ["por"]),
                _address_finding("Second address difference", ["por-2"])]

    [recommendation] = recommend_supporting_documents([por, other], findings)

    assert len(recommendation.items) == 2 and "these differences" in recommendation.request


def test_an_address_finding_whose_documents_arent_in_the_case_asks_for_nothing() -> None:
    finding = _address_finding("An address difference", ["removed-document"])

    assert recommend_supporting_documents([_personal_statement()], [finding]) == []


def test_a_finding_without_the_address_flag_asks_for_no_address_evidence() -> None:
    finding = _address_finding("The company name differs", ["por"], flags=["NAME_MISMATCH"])

    assert recommend_supporting_documents([_personal_statement()], [finding]) == []


def test_a_month_between_two_company_statements_asks_for_that_months_statement() -> None:
    april = _statement([], document_id="april", statement_period_start="2026-04-01", statement_period_end="2026-04-30")
    june = _statement([], document_id="june", statement_period_start="2026-06-01", statement_period_end="2026-06-30")

    [recommendation] = recommend_supporting_documents([april, june])

    assert recommendation.title == "Bank Statements for May 2026"
    assert [i.description for i in recommendation.items] == ["2026-05"]


def test_consecutive_company_statements_ask_for_nothing_more() -> None:
    april = _statement([], document_id="april", statement_period_start="2026-04-01", statement_period_end="2026-04-30")
    may = _statement([], document_id="may", statement_period_start="2026-05-01", statement_period_end="2026-05-31")

    assert recommend_supporting_documents([april, may]) == []


def _receipts(customer: str | None, count: int, amount: float = 5_000.0) -> list[dict]:
    return [{"date": f"2026-04-{day + 1:02d}", "amount": amount, "description": "Inward Cr",
             **({"counterparty": customer} if customer else {})} for day in range(count)]


def test_most_receipts_from_one_named_customer_ask_for_its_contract_or_invoices() -> None:
    rows = _receipts("Orion Academy Pte Ltd", 6) + _receipts("Small Client", 1, 1_000.0)

    titles = [r.title for r in recommend_supporting_documents([_statement(rows)])]

    assert "Contract or Invoices with Orion Academy Pte Ltd" in titles


@pytest.mark.parametrize("rows", [
    _receipts("Orion Academy Pte Ltd", 5),                                  # fewer than 6 credits
    _receipts("Orion Academy Pte Ltd", 3) + _receipts("Other Client", 3) + _receipts("Third Client", 2),  # 3 of 8 at most
    _receipts(None, 8),                                                     # an unnamed payer
], ids=["too few receipts", "not concentrated", "unnamed customer"])
def test_receipts_that_arent_concentrated_on_a_named_customer_ask_for_no_contract(rows) -> None:
    titles = [r.title for r in recommend_supporting_documents([_statement(rows)])]

    assert not any(t.startswith("Contract or Invoices") for t in titles)


_REPAYMENT = {"date": "2026-04-05", "amount": -10_681.80, "description": "GIRO TERM LOAN INSTALMENT DBS"}


def test_loan_repayments_with_no_facility_letter_ask_for_one() -> None:
    [recommendation] = [r for r in recommend_supporting_documents([_statement([_REPAYMENT])])
                        if r.title == "Facility Letter"]

    [item] = recommendation.items
    assert item.amount == -10_681.80 and item.date == "2026-04-05"


def test_loan_repayments_ask_for_no_facility_letter_when_the_case_has_one() -> None:
    letter = _statement([], document_id="letter", document_type="list_of_loans")

    titles = [r.title for r in recommend_supporting_documents([_statement([_REPAYMENT]), letter])]

    assert "Facility Letter" not in titles


def test_an_item_without_an_amount_serialises_with_amount_null() -> None:
    [recommendation] = recommend_supporting_documents(
        [_personal_statement()], [_address_finding("An address difference", ["por"])])

    [item] = json.loads(recommendation.model_dump_json())["items"]
    assert "amount" in item and item["amount"] is None
