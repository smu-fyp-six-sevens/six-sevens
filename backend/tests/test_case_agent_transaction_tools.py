"""
The case analysis agent's transaction tools (case_pipeline/cross_document/tools/transactions.py): what it reads to
weigh a payslip against the bank credits, on an October statement whose rows mostly print no year ("05 OCT"), as
statements do. Each tool's answer is checked, and that it is logged with the refs verify.py relies on.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.case_pipeline.cross_document.evidence import build_context
from app.case_pipeline.cross_document.tools.common import ToolKit
from app.case_pipeline.cross_document.tools.transactions import build_transaction_tools
from app.models import DocumentRecord, DocumentStatus

STATEMENT = "bank_statement_1"
OCTOBER = {"statement_period_start": "01 Oct 2026", "statement_period_end": "31 Oct 2026"}
ROWS = [
    {"date": "05 OCT", "description": "GIRO SALARY", "counterparty": "Brightwave Analytics", "amount": 5200},
    {"date": "07 OCT", "description": "RENT", "counterparty": "Keppel Land", "amount": -1800},
    {"date": "2026-10-20", "description": "FAST TRANSFER", "counterparty": "Tan Ah Kow", "amount": -250},
    {"date": "28 OCT", "description": "RENT", "counterparty": "Keppel Land", "amount": -1800},
]


def statement(rows: list[dict] | None = ROWS, period: dict | None = OCTOBER, *, document_id: str = "doc-1",
              document_type: str = "bank_statement") -> DocumentRecord:
    fields = {**(period or {}), **({"transactions": rows} if rows is not None else {})}
    return DocumentRecord(
        id=document_id, case_id="case-1", filename=f"{document_id}.pdf", status=DocumentStatus.DETECTED,
        uploaded_at=datetime(2026, 11, 1, tzinfo=timezone.utc), document_type=document_type, extracted_fields=fields,
    )


def toolkit(*documents: DocumentRecord) -> tuple[dict, ToolKit]:
    kit = ToolKit(context=build_context("case-1", list(documents) or [statement()], {}), calls=[])
    return {t.name: t for t in build_transaction_tools(kit)}, kit


async def call(tools: dict, name: str, **args) -> dict:
    return json.loads(await tools[name].ainvoke(args))


# --- transaction_stats -----------------------------------------------------------------------------------------


async def test_stats_count_and_total_one_direction_within_dates() -> None:
    tools, kit = toolkit()

    result = await call(tools, "transaction_stats", document=STATEMENT, direction="debit",
                        date_from="2026-10-06", date_to="2026-10-25")

    stats = result["stats"]
    assert (stats["count"], stats["debit_count"], stats["total_debits"], stats["max_size"]) == (2, 2, 2050.0, 1800.0)
    assert result["rows_scanned"] == 4
    # Logged with the fact's ref, so a finding citing it can be verified.
    [logged] = kit.calls
    assert (logged["tool"], logged["document"], logged["refs"]) == ("transaction_stats", STATEMENT, [result["ref"]])
    assert result["ref"] in kit.context.ledger.refs


async def test_stats_of_credits_are_the_salary() -> None:
    tools, _ = toolkit()

    stats = (await call(tools, "transaction_stats", document=STATEMENT, direction="credit"))["stats"]

    assert (stats["count"], stats["total_credits"], stats["total_debits"]) == (1, 5200.0, 0)


@pytest.mark.xfail(strict=True, reason=(
    "Known defect (found writing this test): transaction_summary takes the 90th percentile at index "
    "int(0.9 * (n - 1)), which rounds down to the smallest size for a small sample: two debits of 250 and 1,800 "
    "give a p90 of 250, below their median of 1,025. Remove this marker once the percentile is computed properly."
))
async def test_the_90th_percentile_is_never_below_the_median() -> None:
    tools, _ = toolkit()

    stats = (await call(tools, "transaction_stats", document=STATEMENT, direction="debit",
                        date_from="2026-10-06", date_to="2026-10-25"))["stats"]

    assert stats["p90_size"] >= stats["median_size"]


# --- query_transactions ----------------------------------------------------------------------------------------


async def test_query_by_counterparty_returns_the_true_count_and_each_row_with_a_ref() -> None:
    tools, kit = toolkit()

    result = await call(tools, "query_transactions", document=STATEMENT, counterparty="keppel")

    assert result["match_count"] == 2
    assert [(r["date"], r["amount"], r["counterparty"]) for r in result["rows"]] == [
        ("07 OCT", -1800.0, "Keppel Land"), ("28 OCT", -1800.0, "Keppel Land"),
    ]
    assert kit.calls[0]["refs"] == [r["ref"] for r in result["rows"]]


async def test_a_date_filter_places_a_row_with_no_year_in_the_statements_period() -> None:
    tools, _ = toolkit()

    result = await call(tools, "query_transactions", document=STATEMENT, date_from="2026-10-25")

    assert [r["date"] for r in result["rows"]] == ["28 OCT"]


async def test_a_row_with_no_year_on_a_statement_with_no_period_is_left_out_of_a_date_filter() -> None:
    tools, _ = toolkit(statement(period=None))

    result = await call(tools, "query_transactions", document=STATEMENT, date_from="2026-10-01")

    assert [r["date"] for r in result["rows"]] == ["2026-10-20"]  # the only row that prints its year


async def test_query_by_amount_range_and_description_returns_the_largest_first() -> None:
    tools, _ = toolkit()

    by_amount = await call(tools, "query_transactions", document=STATEMENT, min_amount=1000, max_amount=2000)
    by_text = await call(tools, "query_transactions", document=STATEMENT, description_contains="salary")
    limited = await call(tools, "query_transactions", document=STATEMENT, limit=1)

    assert (by_amount["match_count"], {r["amount"] for r in by_amount["rows"]}) == (2, {-1800.0})
    assert [r["amount"] for r in by_text["rows"]] == [5200.0]
    assert (limited["match_count"], [r["amount"] for r in limited["rows"]]) == (4, [5200.0])


# --- the other tools ---------------------------------------------------------------------------------------------


async def test_top_counterparties_rank_by_volume_with_their_share() -> None:
    tools, _ = toolkit()

    result = await call(tools, "top_counterparties", document=STATEMENT, direction="debit")

    assert result["counterparties"] == [
        {"counterparty": "Keppel Land", "count": 2, "total": 3600.0, "share_of_volume": 0.935},
        {"counterparty": "Tan Ah Kow", "count": 1, "total": 250.0, "share_of_volume": 0.065},
    ]


async def test_monthly_flow_takes_the_year_from_the_statements_period() -> None:
    tools, _ = toolkit()

    result = await call(tools, "monthly_flow", document=STATEMENT)

    assert result["months"] == {"2026-10": {"credits": 5200.0, "debits": 3850.0, "count": 4}}
    assert (result["undated_rows"], result["year_taken_from_statement_period"]) == (0, 3)


async def test_monthly_flow_says_why_rows_with_no_year_could_not_be_dated() -> None:
    tools, _ = toolkit(statement(period=None))

    result = await call(tools, "monthly_flow", document=STATEMENT)

    assert result["undated_rows"] == 3
    assert result["note"] == "Rows print no year and the statement has no period, so they could not be dated."


async def test_a_payee_paid_the_same_amount_twice_is_one_repeated_group() -> None:
    tools, _ = toolkit()

    [group] = (await call(tools, "find_repeated_transactions", document=STATEMENT))["groups"]

    assert (group["payee"], group["amount"], group["count"], group["transactions"]) == ("rent", -1800.0, 2, [2, 4])


# --- what the tools can't read -------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    "transaction_stats", "query_transactions", "top_counterparties", "monthly_flow",
    "find_repeated_transactions", "card_usage_tool", "recurring_payment_amounts",
])
async def test_every_tool_answers_unavailable_for_an_unknown_document_or_one_with_no_transactions(name) -> None:
    payslip = statement(rows=None, document_id="doc-2", document_type="payslip")
    tools, kit = toolkit(statement(), payslip)

    unknown = await call(tools, name, document="bank_statement_9")
    empty = await call(tools, name, document="payslip_1")

    assert (unknown["available"], unknown["reason"]) == (
        False, "Unknown document 'bank_statement_9'. Use list_documents for the valid names.")
    assert (empty["available"], empty["reason"]) == (False, "This document has no transactions.")
    assert [c["available"] for c in kit.calls] == [False, False]
