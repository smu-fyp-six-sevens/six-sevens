from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app import case_service
from app.models import CaseRecord, DocumentRecord, DocumentStatus, RoutingStatus
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


def _case() -> CaseRecord:
    return CaseRecord(
        id="case-1", greenlink_case_id="GLDB-1",
        created_at="2026-10-01T00:00:00Z", updated_at="2026-10-01T00:00:00Z",
    )


def _analysis_recommending(documents: list[DocumentRecord]):
    """A completed analysis run holding what recommend_supporting_documents produced for the documents."""
    from app.case_pipeline.models import CaseAnalysis

    return CaseAnalysis(
        run_id="run-1", case_id="case-1", status="complete", fingerprint="",
        started_at="2026-10-01T00:00:00Z",
        supporting_documents=recommend_supporting_documents(documents),
    )


def test_case_summary_carries_the_recommendations_its_analysis_produced() -> None:
    statement = _statement([SUPPLIER_PAYMENT])
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.langgraph_trace") as trace,
        patch("app.case_service.analysis_repository") as analyses,
    ):
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = [statement]
        trace.get_detection_states.return_value = ({}, set())
        analyses.get_latest_summary.return_value = _analysis_recommending([statement])

        summary = case_service.get_case_summary("case-1")

    assert summary is not None
    assert [r.document_type for r in summary.supporting_document_recommendations] == ["purchase_order"]


def test_case_without_an_analysis_recommends_nothing() -> None:
    # Recommendations are produced only by an analysis run; the summary no longer recomputes them.
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.langgraph_trace") as trace,
        patch("app.case_service.analysis_repository") as analyses,
    ):
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = [_statement([SUPPLIER_PAYMENT])]
        trace.get_detection_states.return_value = ({}, set())
        analyses.get_latest_summary.return_value = None

        summary = case_service.get_case_summary("case-1")

    assert summary is not None
    assert summary.supporting_document_recommendations == []


def test_case_summary_with_nothing_to_request_has_an_empty_list() -> None:
    assert case_service.build_summary(_case(), []).supporting_document_recommendations == []


def test_several_related_party_balances_are_listed_together() -> None:
    rows = [RELATED_CORPORATIONS, {**RELATED_CORPORATIONS, "label": "Amount due from holding company", "amount": 50_000}]

    [recommendation] = recommend_supporting_documents([_financial_statement(rows)])

    assert len(recommendation.items) == 2
    assert recommendation.request.endswith("for these amounts:")


def test_related_party_balance_without_a_prior_year() -> None:
    rows = [{**RELATED_CORPORATIONS, "amount_prior_year": None}]

    [recommendation] = recommend_supporting_documents([_financial_statement(rows)])

    assert recommendation.items[0].reason == "Amount due from related corporations: 352,920.00."


def test_case_snapshot_pushed_to_the_page_carries_the_recommendations() -> None:
    # The snapshot pushed over the websocket must carry the analysis's
    # recommendations through serialisation, or the card would silently go empty.
    statement = _statement([SUPPLIER_PAYMENT])
    with (
        patch("app.case_service.case_repository") as case_repo,
        patch("app.case_service.document_repository") as doc_repo,
        patch("app.case_service.langgraph_trace") as trace,
        patch("app.case_service.extracted_fields_repository") as fields_repo,
        patch("app.case_service.analysis_repository") as analyses,
    ):
        case_repo.get_case.return_value = _case()
        doc_repo.list_documents.return_value = [statement]
        trace.get_detection_states.return_value = ({}, set())
        analyses.get_latest_summary.return_value = _analysis_recommending([statement])
        fields_repo.get_extracted_fields_for_documents.return_value = {}

        snapshot = case_service.build_case_snapshot("case-1")

    assert snapshot is not None
    [recommendation] = snapshot["case"]["supporting_document_recommendations"]
    assert recommendation["document_type"] == "purchase_order"
    assert recommendation["items"][0]["counterparty"] == "MERIDIAN LOGISTICS SERVICES"
