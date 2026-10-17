from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage

from app.case_pipeline.cross_document.company_identity import (
    check_company_name_consistency,
    cross_document_company_names_match,
    legal_entity_type,
    normalize_company_name,
)
from app.case_pipeline.cross_document.evidence import build_context
from app.case_pipeline.cross_document.tools.common import ToolKit
from app.case_pipeline.cross_document.tools.consistency import build_consistency_tools
from app.langgraph.detection import specialists
from app.main import app
from app.models import DocumentRecord, DocumentStatus

client = TestClient(app)

_counter = 0


def _doc(
    document_type: str,
    fields: dict | None,
    doc_id: str | None = None,
    status: DocumentStatus = DocumentStatus.APPROVED,
) -> DocumentRecord:
    global _counter
    _counter += 1
    return DocumentRecord(
        id=doc_id or f"doc-{_counter}",
        case_id="case-1",
        filename=f"{doc_id or document_type}.pdf",
        document_type=document_type,
        status=status,
        uploaded_at="2026-10-01T00:00:00Z",
        extracted_fields=fields,
    )


def _company(document_type: str, name: str, doc_id: str | None = None, **extra) -> DocumentRecord:
    return _doc(document_type, {"company_name": name, **extra}, doc_id)


# --- name matching ------------------------------------------------------------


@pytest.mark.parametrize(
    "a, b, expected",
    [
        # AC1: legal suffix differences are ignored...
        ("ACME Trading Pte Ltd", "ACME Trading Pte. Ltd.", True),
        ("ACME Trading Pte Ltd", "ACME Trading Private Limited", True),
        ("ACME Trading Pte Ltd", "ACME Trading", True),
        ("ACME Trading PTE. LTD.", "ACME Trading Co., Ltd.", True),
        # ...and so are letter case and "&" vs "AND".
        ("ACME Trading Pte Ltd", "acme trading pte. ltd.", True),
        ("ACME TRADING PTE. LTD.", "Acme Trading Private Limited", True),
        ("A&B Logistics Pte Ltd", "A AND B Logistics Pte Ltd", True),
        ("A & B Logistics Pte Ltd", "A and B Logistics Pte Ltd", True),
        # AC2: everything else is flagged — punctuation, spacing, words.
        ("A.B.C. Holdings Pte Ltd", "ABC Holdings Pte Ltd", False),
        ("ACME Trading Pte Ltd", "ACMETrading Pte Ltd", False),
        ("ACME Trading Pte Ltd", "ACME Pte Ltd", False),
        ("ACME Trading Pte Ltd", "ACNE Trading Pte Ltd", False),
        ("ACME Trading Pte Ltd", "Global ACME Trading Pte Ltd", False),
        ("ACME Co Trading Pte Ltd", "ACME Trading Pte Ltd", False),  # "Co" mid-name is part of the name
        ("", "ACME Pte Ltd", False),
        (None, None, False),
        # A suffix stating a different legal entity type is a different company...
        ("ACME Trading Pte Ltd", "ACME Trading LLP", False),
        ("ACME Trading Pte Ltd", "ACME Trading Limited Liability Partnership", False),
        ("ACME Trading Pte Ltd", "ACME Trading Ltd", False),
        ("ACME Trading Pte Ltd", "ACME Trading Sdn Bhd", False),
        ("ACME Trading Inc", "ACME Trading Corp", False),
        # ...but spellings of the same type match, and so does a name with no
        # suffix or one cut short before "LTD" (a bank's account-name field).
        ("ACME Trading LLP", "ACME Trading Limited Liability Partnership", True),
        ("ACME Trading Sdn. Bhd.", "ACME Trading Sendirian Berhad", True),
        ("ACME Trading LLP", "ACME Trading", True),
        ("ACME TRADING PTE", "ACME Trading Pte Ltd", True),
    ],
)
def test_company_names_match(a, b, expected) -> None:
    assert cross_document_company_names_match(a, b) is expected


@pytest.mark.parametrize(
    "name, entity_type",
    [
        ("ACME Pte. Ltd.", "Pte Ltd"),
        ("ACME PRIVATE LIMITED", "Pte Ltd"),
        ("ACME Co., Ltd.", "Pte Ltd"),
        ("ACME LLP", "LLP"),
        ("ACME Limited Partnership", "LP"),
        ("ACME Sdn Bhd", "Sdn Bhd"),
        ("ACME", None),
        (None, None),
    ],
)
def test_legal_entity_type(name, entity_type) -> None:
    assert legal_entity_type(name) == entity_type


def test_names_differing_only_by_entity_type_say_so() -> None:
    document = _company("certificate_of_incumbency", "ACME Trading Pte Ltd", "inc")
    llp = _company("bank_statement", "ACME Trading LLP", "llp")
    result = check_company_name_consistency(document, [document, llp])
    assert len(result.discrepancies) == 1
    assert "The legal entity type differs (Pte Ltd vs LLP)." in result.discrepancies[0].summary


@pytest.mark.parametrize(
    "account_name, matches",
    [
        # OCBC prints one line of the account name, cutting it at a word.
        ("CRIMSON TEXTILE", True),
        ("CRIMSON TEXTILE MANUFACTURING", True),
        ("Crimson  textile", True),
        # Not a cut at a word boundary, or not the start of the name.
        ("CRIMSON TEX", False),
        ("CRIMSON TEXTILES", False),
        ("TEXTILE MANUFACTURING", False),
        # One word is too little to tell the company apart.
        ("CRIMSON", False),
        # Still carries a suffix, so it wasn't cut off: a shorter, different name.
        ("CRIMSON TEXTILE PTE LTD", False),
    ],
)
def test_bank_account_name_cut_off_at_a_word_matches(account_name, matches) -> None:
    incumbency = _company("certificate_of_incumbency", "CRIMSON TEXTILE MANUFACTURING PTE LTD", "inc")
    bank = _company("bank_statement", account_name, "bank")
    # Both directions: checked from the statement, and from the incumbency.
    assert (check_company_name_consistency(bank, [incumbency, bank]).discrepancies == []) is matches
    assert (check_company_name_consistency(incumbency, [incumbency, bank]).discrepancies == []) is matches


def test_a_cut_off_name_only_matches_on_a_bank_statement() -> None:
    incumbency = _company("certificate_of_incumbency", "CRIMSON TEXTILE MANUFACTURING PTE LTD", "inc")
    loan_letter = _company("list_of_loans", "CRIMSON TEXTILE", "loans")
    assert len(check_company_name_consistency(loan_letter, [incumbency, loan_letter]).discrepancies) == 1


def test_normalize_company_name_none_when_only_a_suffix() -> None:
    assert normalize_company_name("Pte. Ltd.") is None


# --- cross-document check -----------------------------------------------------


def test_names_differing_only_by_suffix_produce_no_discrepancy() -> None:
    doc = _company("bank_statement", "ACME Trading Pte. Ltd.", "bs")
    case = [
        doc,
        _company("certificate_of_incumbency", "ACME Trading PTE LTD"),
        _company("fatca_crs_declaration", "ACME Trading Private Limited"),
    ]
    result = check_company_name_consistency(doc, case)

    assert result.skipped_reason is None
    assert result.discrepancies == []
    assert len(result.compared_documents) == 2
    assert all(c.matches for c in result.compared_documents)


def test_every_conflicting_name_is_flagged_with_the_documents_carrying_it() -> None:
    doc = _company("bank_statement", "ACME Trading Pte Ltd", "bs")
    case = [
        doc,
        _company("certificate_of_incumbency", "ACME Trading Pte. Ltd.", "inc"),
        _company("certificate_of_incorporation", "ACME Holdings Pte Ltd", "coi"),
        _company("list_of_loans", "ACME Holdings Pte. Ltd.", "loan"),
        _company("fatca_crs_declaration", "ACNE Trading Pte Ltd", "fatca"),
    ]
    result = check_company_name_consistency(doc, case)

    assert [(d.conflicting, d.document_ids) for d in result.discrepancies] == [
        ("ACME Holdings Pte Ltd", ["coi", "loan"]),
        ("ACNE Trading Pte Ltd", ["fatca"]),
    ]
    first = result.discrepancies[0]
    assert first.stated == "ACME Trading Pte Ltd"
    assert "coi.pdf" in first.summary and "loan.pdf" in first.summary


def test_payslips_credit_reports_and_unrecognised_documents_are_compared() -> None:
    doc = _company("bank_statement", "ACME Trading Pte Ltd", "bs")
    case = [
        doc,
        _doc("payslip", {"company_name": "Other Employer Pte Ltd", "individual_name": "Tan"}, "payslip"),
        _doc("credit_report", {"company_name": "ACME Trading Pte Ltd", "individual_name": "Tan"}, "credit"),
        _doc("unrecognised", {"company_name": "Supplier Pte Ltd", "document_title": "Purchase Order"}, "po"),
        _doc("source_of_wealth", {"company_name": "ACME Trading Pte Ltd", "uen": "201912345K"}, "sow"),
    ]
    result = check_company_name_consistency(doc, case)

    assert {c.document_id for c in result.compared_documents} == {"payslip", "credit", "po", "sow"}
    assert {d.conflicting for d in result.discrepancies} == {"Other Employer Pte Ltd", "Supplier Pte Ltd"}


def test_payslip_without_a_company_name_is_ignored() -> None:
    doc = _company("bank_statement", "ACME Trading Pte Ltd", "bs")
    payslip = _doc("payslip", {"individual_name": "Tan", "net_pay": "5000"}, "payslip")
    result = check_company_name_consistency(doc, [doc, payslip])

    assert result.compared_documents == [] and result.discrepancies == []


@pytest.mark.parametrize(
    "fields, matches",
    [
        # Names another company, but the case's company is on it too.
        ({"company_name": "Supplier Pte Ltd", "bill_to": "acme trading pte. ltd."}, True),
        ({"company_name": "Supplier Pte Ltd", "parties": ["Supplier Pte Ltd", "ACME Trading Pte Ltd"]}, True),
        ({"company_name": "Supplier Pte Ltd", "address": "Attn: ACME TRADING, 1 Road"}, True),
        # The case's company isn't on it anywhere.
        ({"company_name": "Supplier Pte Ltd", "bill_to": "Other Buyer Pte Ltd"}, False),
        ({"company_name": "Supplier Pte Ltd", "bill_to": "ACME Tradings Pte Ltd"}, False),
    ],
)
def test_unrecognised_document_matches_when_the_company_is_named_anywhere_on_it(fields, matches) -> None:
    doc = _company("bank_statement", "ACME Trading Pte Ltd", "bs")
    po = _doc("unrecognised", fields, "po")
    result = check_company_name_consistency(doc, [doc, po])

    assert result.compared_documents[0].matches is matches
    assert (result.discrepancies == []) is matches


def test_only_unrecognised_documents_get_the_anywhere_on_it_match() -> None:
    doc = _company("bank_statement", "ACME Trading Pte Ltd", "bs")
    loan = _doc("list_of_loans", {"company_name": "Lender Pte Ltd", "borrower": "ACME Trading Pte Ltd"}, "loan")
    result = check_company_name_consistency(doc, [doc, loan])

    assert [d.document_ids for d in result.discrepancies] == [["loan"]]


def test_ids_proof_of_address_rejected_and_unextracted_documents_are_not_compared() -> None:
    doc = _company("bank_statement", "ACME Trading Pte Ltd", "bs")
    case = [
        doc,
        _company("certificate_of_incumbency", "ACME Trading Pte Ltd", "inc"),
        _doc("nric", {"company_name": "Wrong Pte Ltd"}),
        _doc("passport", {"company_name": "Wrong Pte Ltd"}),
        _doc("proof_of_address", {"company_name": "Utility Co Pte Ltd"}),
        # Personal bank statement: an individual_name and no company_name — nothing to compare anyway.
        _doc("bank_statement", {"individual_name": "Tan"}),
        _doc("fatca_crs_declaration", {"company_name": "Wrong Pte Ltd"}, status=DocumentStatus.REJECTED),
        _doc("list_of_loans", {"company_name": "Wrong Pte Ltd"}, status=DocumentStatus.EXTRACTING),
        _doc("list_of_loans", {"company_name": "Wrong Pte Ltd"}, status=DocumentStatus.QUEUED),
        # No company name: fine, just nothing to compare.
        _doc("company_constitution", {"uen": "201912345K"}),
        _doc("company_constitution", None),
    ]
    result = check_company_name_consistency(doc, case)

    assert [c.document_id for c in result.compared_documents] == ["inc"]
    assert result.discrepancies == []


@pytest.mark.parametrize(
    "doc, reason",
    [
        (_doc("nric", {"company_name": "ACME Pte Ltd"}), "not compared"),
        (_doc("passport", {"company_name": "ACME Pte Ltd"}), "not compared"),
        (_doc("proof_of_address", {"company_name": "Power Co Pte Ltd"}), "not compared"),
        (_doc("bank_statement", {"individual_name": "Tan"}), "not compared"),
        (_doc("certificate_of_incorporation", {"uen": "201912345K"}), "No company name"),
    ],
)
def test_nothing_compared_for_excluded_or_nameless_documents(doc, reason) -> None:
    other = _company("certificate_of_incumbency", "Different Pte Ltd")
    result = check_company_name_consistency(doc, [doc, other])

    assert result.discrepancies == []
    assert reason in (result.skipped_reason or "")


def test_nothing_compared_when_no_other_document_has_a_name() -> None:
    doc = _company("certificate_of_incumbency", "ACME Pte Ltd")
    result = check_company_name_consistency(doc, [doc])

    assert result.discrepancies == []
    assert "No other document" in (result.skipped_reason or "")


# --- case-pipeline tool ---------------------------------------------------------
# The tool moved out of the per-document detection agent: comparing a document
# with the rest of its case is cross-document work.


def _consistency_tool(documents: list[DocumentRecord]):
    context = build_context("case-1", documents, {})
    kit = ToolKit(context=context, calls=[])
    return next(t for t in build_consistency_tools(kit) if t.name == "company_name_consistency_tool")


@pytest.mark.asyncio
async def test_tool_reports_each_discrepancy() -> None:
    doc = _company("bank_statement", "ACME Trading Pte Ltd", "bs")
    other = _company("certificate_of_incumbency", "ACME Holdings Pte Ltd", "inc")

    output = json.loads(await _consistency_tool([doc, other]).ainvoke({"document": "bs"}))

    assert output["available"] and not output["same"]
    assert output["breach_flags"] == ["NAME_MISMATCH"]
    [discrepancy] = output["discrepancies"]
    assert discrepancy["stated"] == "ACME Trading Pte Ltd"
    assert discrepancy["conflicting"] == "ACME Holdings Pte Ltd"


@pytest.mark.asyncio
async def test_tool_says_same_when_names_differ_only_by_legal_suffix() -> None:
    doc = _company("bank_statement", "ACME Trading Pte Ltd", "bs")
    other = _company("certificate_of_incumbency", "ACME Trading Pte. Ltd.", "inc")

    output = json.loads(await _consistency_tool([doc, other]).ainvoke({"document": "bs"}))

    assert output["same"] and output["discrepancies"] == []


@pytest.mark.asyncio
async def test_tool_has_nothing_to_compare_when_no_other_document_has_a_name() -> None:
    doc = _company("bank_statement", "ACME Pte Ltd", "bs")

    output = json.loads(await _consistency_tool([doc]).ainvoke({"document": "bs"}))

    assert output["available"] is False


def test_detection_agent_no_longer_owns_the_company_name_tool() -> None:
    # The case pipeline owns it now; a second copy would report each mismatch twice.
    owned = {t.name for spec in specialists.SPECIALISTS.values() for t in spec.tools}
    assert "company_name_consistency_tool" not in owned


def _collect(tool_name: str, assessment: dict) -> dict:
    """Runs a specialist's collect step over one tool call returning one finding."""
    finding = {"id": f"{tool_name}-1", "summary": "Company name differs"}
    messages = [
        AIMessage(content="", tool_calls=[{"name": tool_name, "args": {}, "id": "call-1"}]),
        ToolMessage(
            content=json.dumps({"tool": tool_name, "unable_to_assess": False, "findings": [finding]}),
            tool_call_id="call-1", name=tool_name,
        ),
        AIMessage(content=json.dumps({"summary": "done", "assessments": [{"finding_id": finding["id"], **assessment}]})),
    ]
    return specialists._collect_node({"messages": messages, "specialist": "content_validation"})["report"]


@pytest.mark.parametrize(
    "assessment, severity",
    [
        ({"dismiss": True, "rationale": "Looks like a trading name"}, "medium"),
        ({"severity": "info", "rationale": "Probably fine"}, "medium"),
        ({"severity": "low", "rationale": "Minor"}, "medium"),
        ({"severity": "high", "rationale": "Different company"}, "high"),
    ],
)
def test_specialist_cannot_dismiss_or_downgrade_a_company_name_discrepancy(assessment, severity) -> None:
    report = _collect("company_name_consistency_tool", assessment)

    assert report["dismissed"] == []
    [finding] = report["findings"]
    assert finding["severity"] == severity


def test_other_tools_findings_can_still_be_dismissed() -> None:
    report = _collect("spelling_tool", {"dismiss": True, "rationale": "Proper noun"})

    assert report["findings"] == [] and len(report["dismissed"]) == 1


# --- on-demand endpoint -------------------------------------------------------


def test_endpoint_compares_against_the_case_documents() -> None:
    doc = _company("bank_statement", "ACME Trading Pte Ltd", "doc-1")
    other = _company("certificate_of_incumbency", "ACNE Trading Pte Ltd", "inc")
    with (
        patch("app.routers._dependencies.document_repository") as dep_repo,
        patch("app.routers.detection.document_repository") as repo,
        patch("app.routers.detection.langgraph_trace") as trace,
    ):
        dep_repo.get_document.return_value = doc
        repo.list_documents.return_value = [doc, other]
        trace.load.return_value = {"detection_report": {"verdict": "clean"}}

        response = client.get("/documents/doc-1/detection/company-name-consistency")

    assert response.status_code == 200
    body = response.json()
    assert body["discrepancies"][0]["conflicting"] == "ACNE Trading Pte Ltd"
    repo.list_documents.assert_called_once_with(case_id="case-1")


def test_endpoint_requires_full_detection_first() -> None:
    doc = _company("bank_statement", "ACME Pte Ltd", "doc-1")
    with (
        patch("app.routers._dependencies.document_repository") as dep_repo,
        patch("app.routers.detection.langgraph_trace") as trace,
    ):
        dep_repo.get_document.return_value = doc
        trace.load.return_value = None

        response = client.get("/documents/doc-1/detection/company-name-consistency")

    assert response.status_code == 409
