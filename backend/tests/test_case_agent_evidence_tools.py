"""
The case analysis agent's reading and comparison tools (case_pipeline/cross_document/tools/reading.py and
comparisons.py): what it may read of each document, and the comparisons whose breach flags are the only way a
finding reaches High (verify.py). A personal attribute is never read or compared, whatever the model asks for.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.case_pipeline.cross_document.evidence import build_context
from app.case_pipeline.cross_document.tools.common import ToolKit
from app.case_pipeline.cross_document.tools.comparisons import build_comparison_tools
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

    assert listed == [
        {"document": "certificate_of_incumbency_1", "type": "certificate_of_incumbency", "filename": "doc-coi.pdf",
         "detection_findings": 1},
        {"document": "payslip_1", "type": "payslip", "filename": "doc-pay.pdf", "detection_findings": 0},
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


async def test_a_personal_attribute_is_never_compared() -> None:
    tools, _ = toolkit(document("passport", "doc-pp", nationality="Singaporean"),
                       document("nric", "doc-id", nationality="Malaysian"))

    result = await call(tools, "compare_people", items=[
        {"document": "passport_1", "field": "nationality"}, {"document": "nric_1", "field": "nationality"},
    ])

    assert result["available"] is False
    assert len(result["missing"]) == 2
