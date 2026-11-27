"""
The case analysis agent's reading and comparison tools (case_pipeline/cross_document/tools/reading.py and
comparisons.py): what it may read of each document, and the comparisons whose breach flags are the only way a
finding reaches High (verify.py). A personal attribute is never read or compared, whatever the model asks for.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.case_pipeline.cross_document.evidence import CaseContext, build_context, render_dossier
from app.case_pipeline.cross_document.tools import build_tools
from app.case_pipeline.cross_document.tools.common import ToolKit
from app.case_pipeline.cross_document.tools.comparisons import build_comparison_tools
from app.case_pipeline.cross_document.tools.consistency import build_consistency_tools
from app.case_pipeline.cross_document.tools.reading import build_reading_tools
from app.models import DocumentRecord, DocumentStatus


def document(document_type: str, document_id: str, **fields) -> DocumentRecord:
    return DocumentRecord(
        id=document_id, case_id="case-1", filename=f"{document_id}.pdf", status=DocumentStatus.DETECTED,
        uploaded_at=datetime(2026, 11, 1, tzinfo=timezone.utc), document_type=document_type, extracted_fields=fields,
    )


def toolkit(*documents: DocumentRecord, reports: dict | None = None) -> tuple[dict, ToolKit]:
    kit = ToolKit(context=build_context("case-1", list(documents), reports or {}), calls=[])
    return {t.name: t for t in [*build_reading_tools(kit), *build_comparison_tools(kit)]}, kit


async def call(tools: dict, name: str, **args) -> dict:
    return json.loads(await tools[name].ainvoke(args))


def flag_on(kit: ToolKit, ref: str) -> str | None:
    return kit.context.ledger.refs[ref]["breach_flag"]


# --- Reading ----------------------------------------------------------------------------------------------------


async def test_the_documents_are_listed_by_the_names_the_tools_take() -> None:
    reports = {"doc-coi": {"findings": [{"tool": "font_consistency_tool", "summary": "x", "severity": "low"}]}}
    tools, _ = toolkit(document("payslip", "doc-pay"), document("certificate_of_incumbency", "doc-coi"), reports=reports)

    listed = (await call(tools, "list_documents"))["documents"]

    # No file names: one can hold a person's name or a protected word (bias.py).
    assert listed == [
        {"document": "certificate_of_incumbency_1", "type": "certificate_of_incumbency", "detection_findings": 1},
        {"document": "payslip_1", "type": "payslip", "detection_findings": 0},
    ]


async def test_a_field_is_read_with_a_ref_and_its_identifiers_masked() -> None:
    tools, kit = toolkit(document("payslip", "doc-pay", employee_nric="S8812345F", net_pay="4160.00"))

    result = await call(tools, "get_fields", document="payslip_1", names=["employee_nric", "net_pay", "bonus"])

    nric, net_pay, bonus = result["fields"]
    # The NRIC masked; an amount read as the number it is.
    assert (nric["value"], net_pay["value"]) == ('"S****345F"', "4160.0")
    assert bonus == {"field": "bonus", "available": False}
    assert kit.calls[0]["refs"] == [nric["ref"], net_pay["ref"]]


@pytest.mark.parametrize("field", ["race", "date_of_birth", "marital_status", "nationality"])
async def test_a_personal_attribute_is_never_read(field) -> None:
    tools, kit = toolkit(document("passport", "doc-pp", **{field: "anything"}))

    [result] = (await call(tools, "get_fields", document="passport_1", names=[field]))["fields"]

    assert result == {"field": field, "available": False, "reason": "A personal attribute; never used."}
    assert kit.context.ledger.refs == {}


async def test_the_business_profile_names_where_documents_disagree() -> None:
    tools, _ = toolkit(
        document("certificate_of_incorporation", "doc-coi", registered_address="10 Anson Road #20-01",
                 date_of_incorporation="12 Mar 2019", director_name="Tan Wei Ming"),
        document("bank_statement", "doc-bs", company_address="10 ANSON ROAD, #20-01", director_name="Lim Wei Ming"),
        document("credit_report", "doc-cr", business_address="1 Raffles Place"),
    )

    result = await call(tools, "get_business_profile")

    assert {item["document"] for item in result["profile"]["registered_address"]} == {
        "certificate_of_incorporation_1", "bank_statement_1", "credit_report_1"}
    # Punctuation and case don't make a disagreement; another address does. Directors are listed, not compared.
    assert result["documents_disagree_on"] == ["registered_address"]
    assert len(result["profile"]["directors"]) == 2


async def test_with_no_profile_fields_the_profile_is_unavailable() -> None:
    tools, _ = toolkit(document("payslip", "doc-pay", net_pay="4160.00"))

    result = await call(tools, "get_business_profile")

    assert (result["available"], result["reason"]) == (False, "No profile fields were found on any document.")


# --- Statement periods --------------------------------------------------------------------------------------------


def statement(document_id: str, start: str, end: str, opening: str = "1000.00", closing: str = "1500.00") -> DocumentRecord:
    return document("bank_statement", document_id, statement_period_start=start, statement_period_end=end,
                    opening_balance=opening, closing_balance=closing)


async def test_consecutive_statements_whose_balances_carry_over_raise_nothing() -> None:
    tools, _ = toolkit(statement("doc-sep", "01 Sep 2026", "30 Sep 2026", closing="1500.00"),
                       statement("doc-oct", "01 Oct 2026", "31 Oct 2026", opening="1500.00"))

    result = await call(tools, "compare_periods", document_a="bank_statement_2", document_b="bank_statement_1")

    assert (result["overlap_days"], result["gap_days"], result["balances_continuous"], result["breach_flags"]) == (
        0, 0, True, [])


async def test_a_balance_that_does_not_carry_over_is_a_discontinuity() -> None:
    tools, kit = toolkit(statement("doc-sep", "01 Sep 2026", "30 Sep 2026", closing="1500.00"),
                         statement("doc-oct", "01 Oct 2026", "31 Oct 2026", opening="9500.00"))

    result = await call(tools, "compare_periods", document_a="bank_statement_1", document_b="bank_statement_2")

    assert result["breach_flags"] == ["BALANCE_DISCONTINUITY"]
    assert result["notes"] == ["bank_statement_2 closes at 1,500.00 but bank_statement_1 opens at 9,500.00."]
    assert all(flag_on(kit, ref) == "BALANCE_DISCONTINUITY" for ref in result["refs"])


async def test_overlapping_periods_and_a_period_ending_before_it_starts_are_flagged() -> None:
    tools, _ = toolkit(statement("doc-a", "01 Oct 2026", "31 Oct 2026"), statement("doc-b", "20 Oct 2026", "05 Oct 2026"))

    result = await call(tools, "compare_periods", document_a="bank_statement_1", document_b="bank_statement_2")

    assert result["breach_flags"][0] == "CHRONOLOGICAL_IMPOSSIBILITY"
    assert "bank_statement_2 ends before it starts." in result["notes"]


async def test_overlapping_months_are_a_period_overlap() -> None:
    tools, _ = toolkit(statement("doc-a", "01 Oct 2026", "31 Oct 2026"), statement("doc-b", "15 Oct 2026", "14 Nov 2026"))

    result = await call(tools, "compare_periods", document_a="bank_statement_1", document_b="bank_statement_2")

    assert (result["breach_flags"], result["overlap_days"], result["balances_continuous"]) == (["PERIOD_OVERLAP"], 17, None)


async def test_a_statement_with_no_period_cannot_be_compared() -> None:
    tools, _ = toolkit(statement("doc-a", "01 Oct 2026", "31 Oct 2026"), document("bank_statement", "doc-b"))

    result = await call(tools, "compare_periods", document_a="bank_statement_1", document_b="bank_statement_2")

    assert (result["available"], result["reason"]) == (False, "A statement period is missing on one of the documents.")


# --- Identifiers and people ---------------------------------------------------------------------------------------


async def test_the_same_uen_written_differently_is_the_same() -> None:
    tools, _ = toolkit(document("certificate_of_incorporation", "doc-coi", uen="201912345A"),
                       document("bank_statement", "doc-bs", company_uen="201912345-A"))

    result = await call(tools, "compare_identifiers", items=[
        {"document": "certificate_of_incorporation_1", "field": "uen"},
        {"document": "bank_statement_1", "field": "company_uen"},
    ])

    assert (result["same"], result["breach_flags"]) == (True, [])


async def test_a_different_uen_is_an_identifier_conflict_carried_on_its_refs() -> None:
    tools, kit = toolkit(document("certificate_of_incorporation", "doc-coi", uen="201912345A"),
                         document("bank_statement", "doc-bs", company_uen="201912346A"))

    result = await call(tools, "compare_identifiers", items=[
        {"document": "certificate_of_incorporation_1", "field": "uen"},
        {"document": "bank_statement_1", "field": "company_uen"},
    ])

    assert (result["same"], result["breach_flags"]) == (False, ["IDENTIFIER_CONFLICT"])
    assert [flag_on(kit, value["ref"]) for value in result["values"]] == ["IDENTIFIER_CONFLICT"] * 2


async def test_a_name_is_not_compared_as_an_identifier() -> None:
    tools, _ = toolkit(document("payslip", "doc-pay", employer_name="Brightwave"),
                       document("bank_statement", "doc-bs", company_name="Brightwave"))

    result = await call(tools, "compare_identifiers", items=[
        {"document": "payslip_1", "field": "employer_name"}, {"document": "bank_statement_1", "field": "company_name"},
    ])

    assert result["available"] is False
    assert result["reason"].startswith("employer_name, company_name is not an identifier field.")


async def test_a_comparison_needs_two_values() -> None:
    tools, _ = toolkit(document("payslip", "doc-pay", employee_nric="S8812345F"), document("bank_statement", "doc-bs"))

    result = await call(tools, "compare_identifiers", items=[
        {"document": "payslip_1", "field": "employee_nric"}, {"document": "bank_statement_1", "field": "account_nric"},
    ])

    assert (result["available"], result["missing"]) == (False, [{"document": "bank_statement_1", "field": "account_nric"}])


async def test_the_same_person_in_another_order_or_case_matches_and_another_person_does_not() -> None:
    tools, kit = toolkit(document("payslip", "doc-pay", employee_name="Tan Wei Ming"),
                         document("nric", "doc-id", full_name="TAN WEI MING"),
                         document("bank_statement", "doc-bs", account_holder="Lim Wei Ming"))
    payslip, nric, statement_holder = (
        {"document": "payslip_1", "field": "employee_name"}, {"document": "nric_1", "field": "full_name"},
        {"document": "bank_statement_1", "field": "account_holder"})

    same = await call(tools, "compare_people", items=[payslip, nric])
    different = await call(tools, "compare_people", items=[payslip, statement_holder])

    assert (same["same"], same["breach_flags"]) == (True, [])
    assert (different["same"], different["breach_flags"]) == (False, ["PERSON_MISMATCH"])


async def test_one_person_with_two_id_numbers_is_an_identifier_conflict() -> None:
    # Linked by the same name on both documents; the ID numbers then disagree.
    tools, kit = toolkit(document("nric", "doc-id", individual_name="Tan Wei Ming", nric_number="S1234567D"),
                         document("credit_report", "doc-cr", individual_name="TAN WEI MING", id_number="S7654321A"))

    ids = await call(tools, "compare_linked_people", document_a="nric_1", document_b="credit_report_1", check="id")
    names = await call(tools, "compare_linked_people", document_a="nric_1", document_b="credit_report_1", check="name")

    assert (ids["available"], ids["same"], ids["breach_flags"]) == (True, False, ["IDENTIFIER_CONFLICT"])
    [mismatch] = ids["mismatches"]
    assert mismatch["check"] == "individual_id"
    assert [flag_on(kit, ref) for ref in mismatch["refs"]] == ["IDENTIFIER_CONFLICT"] * 2
    assert (names["same"], names["breach_flags"]) == (True, [])


async def test_documents_naming_no_one_in_common_cannot_be_compared() -> None:
    tools, kit = toolkit(document("nric", "doc-id", individual_name="Tan Wei Ming", nric_number="S1234567D"),
                         document("credit_report", "doc-cr", individual_name="Lim Ah Kow", id_number="S7654321A"))

    result = await call(tools, "compare_linked_people", document_a="nric_1", document_b="credit_report_1")

    assert result["available"] is False
    assert kit.context.ledger.refs == {}


async def test_a_personal_attribute_is_never_compared() -> None:
    tools, _ = toolkit(document("passport", "doc-pp", nationality="Singaporean"),
                       document("nric", "doc-id", nationality="Malaysian"))

    result = await call(tools, "compare_people", items=[
        {"document": "passport_1", "field": "nationality"}, {"document": "nric_1", "field": "nationality"},
    ])

    assert result["available"] is False
    assert len(result["missing"]) == 2


# --- No file names reach the case model (PENDING_TESTS row 24) -----------------------------------------------------
# A file name can carry a person's name or a protected word ("Tan Ah Kow Malay payslip.pdf"), so the model sees
# document keys only: in the dossier, the document list and the consistency tools' summaries.

def _named(document_type: str, document_id: str, filename: str, **fields) -> DocumentRecord:
    return document(document_type, document_id, **fields).model_copy(update={"filename": filename})


def _without_names(text: str, *filenames: str) -> None:
    for filename in filenames:
        assert filename not in text and filename.rsplit(".", 1)[0] not in text, f"{filename!r} reached the model"


async def test_the_dossier_names_each_document_by_key_and_type_only() -> None:
    pay = _named("payslip", "doc-pay", "Tan Ah Kow Malay payslip.pdf", net_pay="4160.00")
    bank = _named("bank_statement", "doc-bank", "TanAhKow_DBS_Oct.pdf", closing_balance="9000.00")
    context = build_context("case-1", [pay, bank], {})

    dossier = render_dossier(context)

    _without_names(dossier, pay.filename, bank.filename)
    assert "[payslip_1] payslip" in dossier and "[bank_statement_1] bank_statement" in dossier


async def test_the_document_list_carries_no_file_names() -> None:
    pay = _named("payslip", "doc-pay", "Tan Ah Kow Malay payslip.pdf")
    tools, _ = toolkit(pay)

    listed = await call(tools, "list_documents")

    _without_names(json.dumps(listed), pay.filename)


async def test_a_company_name_mismatch_summary_names_documents_by_key() -> None:
    incorporation = _named("certificate_of_incorporation", "doc-inc", "Tan Ah Kow ACRA cert.pdf",
                           company_name="Greenlink Trading Pte Ltd")
    pay = _named("payslip", "doc-pay", "Tan Ah Kow Malay payslip.pdf", company_name="Bluewater Logistics Pte Ltd")
    kit = ToolKit(context=build_context("case-1", [incorporation, pay], {}), calls=[])
    tools = {t.name: t for t in build_consistency_tools(kit)}

    result = await call(tools, "company_name_consistency_tool", document="certificate_of_incorporation_1")

    (discrepancy,) = result["discrepancies"]
    assert discrepancy["documents"] == ["payslip_1"]
    # The summary is written with file names; the tool puts the key in their place.
    assert "payslip_1" in discrepancy["summary"]
    _without_names(json.dumps(result), incorporation.filename, pay.filename)
    _without_names(json.dumps(kit.calls, default=str), incorporation.filename, pay.filename)


# --- The cross-document evidence tools (PENDING_TESTS row 62) -------------------------------------------------------


def evidence_tools(*documents: DocumentRecord) -> tuple[dict, list[dict], CaseContext]:
    context = build_context("case-1", list(documents), {})
    calls: list[dict] = []
    return {t.name: t for t in build_tools(context, calls)}, calls, context


def company_statement(document_id: str, rows: list[dict], **extra) -> DocumentRecord:
    fields = {"entity_type": "corporate", "entity_name": "Brightwave Analytics Pte Ltd",
              "statement_period_start": "01 Oct 2026", "statement_period_end": "31 Oct 2026", **extra}
    return document("bank_statement", document_id, transactions=rows, **fields)


def personal_statement(document_id: str, rows: list[dict]) -> DocumentRecord:
    return document("bank_statement", document_id, entity_type="individual", individual_name="Tan Wei Ming",
                    statement_period_start="01 Oct 2026", statement_period_end="31 Oct 2026", transactions=rows)


def row(day: str, description: str, amount: float, counterparty: str | None = None) -> dict:
    return {"date": day, "description": description, "amount": amount, **({"counterparty": counterparty} if counterparty else {})}


def accounts(document_id: str = "doc-acc", **extra) -> DocumentRecord:
    return document("source_of_wealth", document_id, revenue="120000", financial_year_end="31 Dec 2025", **extra)


async def test_salary_credit_match_finds_the_credit_and_issues_a_ref_on_the_statement() -> None:
    tools, calls, context = evidence_tools(
        document("payslip", "doc-pay", net_pay="4160.00", pay_date="28 Oct 2026"),
        personal_statement("doc-bs", [row("28 OCT", "SALARY", 4160, "Brightwave Analytics")]))

    result = await call(tools, "salary_credit_match", payslip="payslip_1", statement="bank_statement_1")

    assert len(result["matching_credits"]) == 1 and result["nearest_difference"] == 0
    assert context.ledger.refs[result["ref"]]["doc_key"] == "bank_statement_1"
    # Logged against the payslip, with two refs: the salary result and the net pay.
    assert (calls[0]["tool"], calls[0]["document"], calls[0]["available"]) == ("salary_credit_match", "payslip_1", True)
    assert calls[0]["args"] == {"payslip": "payslip_1", "statement": "bank_statement_1"} and len(calls[0]["refs"]) == 2


async def test_salary_credit_match_reports_the_nearest_credit_when_none_matches() -> None:
    tools, _, _ = evidence_tools(
        document("payslip", "doc-pay", net_pay="4160.00", pay_date="28 Oct 2026"),
        personal_statement("doc-bs", [row("29 OCT", "SALARY", 3100)]))

    result = await call(tools, "salary_credit_match", payslip="payslip_1", statement="bank_statement_1")

    assert (result["matching_credits"], result["nearest_difference"]) == ([], -1060.0)


async def test_salary_credit_match_refuses_an_unknown_document_or_a_statement_without_transactions() -> None:
    tools, calls, context = evidence_tools(
        document("payslip", "doc-pay", net_pay="4160.00"), document("bank_statement", "doc-bs"))

    unknown = await call(tools, "salary_credit_match", payslip="payslip_9", statement="bank_statement_1")
    empty = await call(tools, "salary_credit_match", payslip="payslip_1", statement="bank_statement_1")

    assert unknown["available"] is False and "payslip_9" in unknown["reason"]
    assert (empty["available"], empty["reason"]) == (False, "The statement has no transactions.")
    assert [c["available"] for c in calls] == [False, False] and context.ledger.refs == {}


async def test_revenue_vs_bank_credits_annualises_the_credits_and_issues_a_ref() -> None:
    tools, calls, context = evidence_tools(
        accounts(), company_statement("doc-bs", [row("05 OCT", "RECEIPT", 10000, "Acme Pte Ltd")]))

    result = await call(tools, "revenue_vs_bank_credits", financial_statement="source_of_wealth_1",
                        statements=["bank_statement_1"])

    # One month of 10,000 is 120,000 a year: the revenue.
    assert (result["credits_annualised"], result["annualised_credits_to_revenue"]) == (120000.0, 1.0)
    assert result["ref"] in context.ledger.refs
    assert (calls[0]["tool"], calls[0]["document"], calls[0]["refs"]) == (
        "revenue_vs_bank_credits", "source_of_wealth_1", [result["ref"]])


async def test_revenue_vs_bank_credits_refuses_a_personal_statement_an_empty_list_and_an_unknown_name() -> None:
    tools, calls, context = evidence_tools(accounts(), personal_statement("doc-bs", [row("05 OCT", "SALARY", 4000)]))

    personal = await call(tools, "revenue_vs_bank_credits", financial_statement="source_of_wealth_1",
                          statements=["bank_statement_1"])
    none_named = await call(tools, "revenue_vs_bank_credits", financial_statement="source_of_wealth_1", statements=[])
    unknown = await call(tools, "revenue_vs_bank_credits", financial_statement="source_of_wealth_1",
                         statements=["bank_statement_7"])

    assert personal["available"] is False and "personal statement" in personal["reason"]
    assert (none_named["available"], none_named["reason"]) == (False, "Name at least one company bank statement.")
    assert unknown["available"] is False and "bank_statement_7" in unknown["reason"]
    assert [c["available"] for c in calls] == [False] * 3 and context.ledger.refs == {}


async def test_revenue_vs_bank_credits_with_an_unknown_financial_statement_is_unavailable() -> None:
    tools, calls, _ = evidence_tools(company_statement("doc-bs", [row("05 OCT", "RECEIPT", 10000)]))

    result = await call(tools, "revenue_vs_bank_credits", financial_statement="source_of_wealth_1",
                        statements=["bank_statement_1"])

    assert result["available"] is False and calls[0]["document"] is None


async def test_assessed_income_vs_profit_compares_the_eci_with_the_profit_and_issues_a_ref() -> None:
    tools, calls, context = evidence_tools(
        document("notice_of_assessment", "doc-noa", eci="90000", year_of_assessment="2026"),
        accounts(net_profit="60000"))

    result = await call(tools, "assessed_income_vs_profit", notice_of_assessment="notice_of_assessment_1",
                        financial_statement="source_of_wealth_1")

    assert (result["assessed_income"], result["profit_before_tax"], result["assessed_to_profit"]) == (90000.0, 60000.0, 1.5)
    assert result["ref"] in context.ledger.refs
    assert (calls[0]["document"], calls[0]["refs"]) == ("notice_of_assessment_1", [result["ref"]])


async def test_assessed_income_vs_profit_is_unavailable_without_an_eci_or_for_an_unknown_document() -> None:
    tools, calls, context = evidence_tools(document("notice_of_assessment", "doc-noa"), accounts(net_profit="60000"))

    no_eci = await call(tools, "assessed_income_vs_profit", notice_of_assessment="notice_of_assessment_1",
                        financial_statement="source_of_wealth_1")
    unknown = await call(tools, "assessed_income_vs_profit", notice_of_assessment="notice_of_assessment_4",
                         financial_statement="source_of_wealth_1")

    assert no_eci["available"] is False and unknown["available"] is False
    assert [c["available"] for c in calls] == [False, False] and context.ledger.refs == {}


async def test_counterparty_round_trips_lists_the_circular_counterparty_and_issues_a_ref() -> None:
    tools, calls, context = evidence_tools(company_statement("doc-bs", [
        row("03 OCT", "RECEIPT", 20000, "Orbit Trading Pte Ltd"), row("10 OCT", "PAYMENT", -15000, "Orbit Trading Pte Ltd"),
        row("12 OCT", "RECEIPT", 5000, "Solo Customer Pte Ltd")]))

    result = await call(tools, "counterparty_round_trips", document="bank_statement_1")

    [trip] = result["round_trips"]
    assert (trip["counterparty"], trip["paid_in"], trip["paid_out"]) == ("Orbit Trading Pte Ltd", 20000.0, 15000.0)
    assert result["ref"] in context.ledger.refs and calls[0]["refs"] == [result["ref"]]


async def test_counterparty_round_trips_refuses_an_unknown_document_and_a_statement_without_transactions() -> None:
    tools, calls, context = evidence_tools(document("bank_statement", "doc-bs"))

    unknown = await call(tools, "counterparty_round_trips", document="bank_statement_5")
    empty = await call(tools, "counterparty_round_trips", document="bank_statement_1")

    assert unknown["available"] is False
    assert (empty["available"], empty["reason"]) == (False, "This document has no transactions.")
    assert [c["available"] for c in calls] == [False, False] and context.ledger.refs == {}


async def test_customer_concentration_names_the_top_payer_share_and_issues_a_ref() -> None:
    tools, calls, context = evidence_tools(company_statement("doc-bs", [
        row("03 OCT", "RECEIPT", 9000, "Orbit Trading Pte Ltd"), row("10 OCT", "RECEIPT", 1000, "Other Pte Ltd")]))

    result = await call(tools, "customer_concentration", document="bank_statement_1")

    assert (result["top_counterparty"], result["top_share"]) == ("Orbit Trading Pte Ltd", 0.9)
    assert result["ref"] in context.ledger.refs and calls[0]["refs"] == [result["ref"]]


async def test_customer_concentration_is_unavailable_with_no_transactions_no_receipts_or_an_unknown_document() -> None:
    tools, calls, context = evidence_tools(
        document("bank_statement", "doc-a"), company_statement("doc-b", [row("03 OCT", "FEE", -50)]))

    empty = await call(tools, "customer_concentration", document="bank_statement_1")
    no_receipts = await call(tools, "customer_concentration", document="bank_statement_2")
    unknown = await call(tools, "customer_concentration", document="payslip_1")

    assert (empty["reason"], no_receipts["reason"]) == ("This document has no transactions.", "The statement has no receipts.")
    assert unknown["available"] is False
    assert [c["available"] for c in calls] == [False] * 3 and context.ledger.refs == {}


async def test_statement_coverage_lists_months_per_statement_and_the_gap_between_them() -> None:
    tools, calls, context = evidence_tools(
        company_statement("doc-a", [row("05 AUG", "RECEIPT", 100)], statement_period_start="01 Aug 2026",
                          statement_period_end="31 Aug 2026"),
        company_statement("doc-b", [row("05 OCT", "RECEIPT", 100)]))

    result = await call(tools, "statement_coverage")

    assert result["months_by_statement"] == {"bank_statement_1": ["2026-08"], "bank_statement_2": ["2026-10"]}
    assert result["missing_months"] == ["2026-09"]
    assert result["ref"] in context.ledger.refs and calls[0]["document"] is None and calls[0]["refs"] == [result["ref"]]


async def test_statement_coverage_without_bank_statements_is_unavailable() -> None:
    tools, calls, context = evidence_tools(document("payslip", "doc-pay"))

    result = await call(tools, "statement_coverage")

    assert (result["available"], result["reason"]) == (False, "The case has no bank statements.")
    assert calls[0]["available"] is False and context.ledger.refs == {}


async def test_a_refutation_probe_is_logged_as_one() -> None:
    tools, calls, _ = evidence_tools(company_statement("doc-bs", [row("03 OCT", "RECEIPT", 100, "Orbit Pte Ltd")]))

    await call(tools, "customer_concentration", document="bank_statement_1", is_refutation_probe=True)

    assert calls[0]["probe"] is True
