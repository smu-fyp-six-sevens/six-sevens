"""
What a case analysis leaves on a case for the case page's Risk Summary and Additional Documents cards, built
with the real scoring (risk/case_risk.py), outlook (risk/outlook.py), additional documents
(risk/additional_documents.py) and supporting-document rules (risk/supporting_documents.py).

The browser suite (frontend/e2e/specs/case-management.spec.ts) renders these from
fixtures/regression/case-views.json instead of a hand-built risk, so it can only show states the backend
produces. A test below fails if any of them drifts; regenerate with REGENERATE_REGRESSION_FIXTURES=1.

Only the evidence requests are scripted: the agent writes them (propose_requests_with_status), and here its
reply is given.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.case_pipeline.models import EvidenceRequest
from app.case_pipeline.risk.additional_documents import build_additional_documents
from app.case_pipeline.risk.case_risk import compute_case_risk
from app.case_pipeline.risk.outlook import build_outlook
from app.case_pipeline.risk.supporting_documents import recommend_supporting_documents
from app.models import DocumentRecord, DocumentStatus, RoutingStatus

CASE_VIEWS_FIXTURE = Path(__file__).parent / "fixtures" / "regression" / "case-views.json"
_UPLOADED = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _document(document_id: str, filename: str, document_type: str, fields: dict | None = None) -> DocumentRecord:
    return DocumentRecord(
        id=document_id, case_id="case-1", filename=filename, document_type=document_type,
        classification_confidence=0.95, routing_status=RoutingStatus.ACCEPTED, status=DocumentStatus.DETECTED,
        uploaded_at=_UPLOADED, extracted_fields=fields,
    )


def _finding(number: int, tool: str, severity: str, summary: str, rationale: str, **extra) -> dict:
    return {"id": f"{tool}-{number}", "tool": tool, "severity": severity, "summary": summary, "rationale": rationale,
            **extra}


def _report(*findings: dict, unable: tuple[dict, ...] = ()) -> dict:
    return {"verdict": "flagged" if findings else "clean", "findings": list(findings),
            "unable_to_assess": list(unable), "consulted": []}


def scored_case_view() -> dict:
    """
    CM-007: a payslip edited twice (two font findings: the second from the same check scores a quarter), one
    offset repeated on three rows, and a statement with a low spelling finding whose metadata couldn't be read.
    """
    payslip = _document("0a11a11a-0000-0000-0000-000000000001", "C011_Payslip.pdf", "payslip")
    statement = _document("0a11a11a-0000-0000-0000-000000000002", "C011_BankStatement.pdf", "bank_statement")
    offset = ("This label and amount on the same row are vertically offset by 20 pt.",
              "Rows elsewhere on the payslip line up.")
    reports = {
        payslip.id: _report(
            _finding(1, "font_consistency_tool", "high", "Net pay is in a different font from the rest of the payslip.",
                     "Only the figure that matters differs."),
            _finding(2, "font_consistency_tool", "high", "Employer CPF is in a different font from the rest of the payslip.",
                     "Employer CPF is in a different font from the rest of the payslip. The edit hides an underpayment."),
            *(_finding(n, "layout_anomalies_tool", "medium", *offset) for n in (1, 2, 3)),
        ),
        statement.id: _report(
            _finding(1, "spelling_tool", "low", 'Possible misspelling "Statment".', "A bank's own template is proofread."),
            unable=({"tool": "metadata_tool", "reason": "No metadata"},),
        ),
    }
    return {"risk": compute_case_risk([payslip, statement], reports, set()).model_dump(mode="json")}


def _additional_documents_view(
    documents: list[DocumentRecord], reports: dict[str, dict], requests: list[EvidenceRequest], request_status: str,
) -> dict:
    """The risk, outlook and additional documents one analysis run leaves on the case (case_pipeline/service.py)."""
    risk = compute_case_risk(documents, reports, set())
    outlook = build_outlook(risk, requests)
    supporting = recommend_supporting_documents(documents)
    additional = build_additional_documents(risk, requests, outlook, supporting, request_status)
    return {
        "risk": risk.model_dump(mode="json"),
        "additional_documents": additional.model_dump(mode="json"),
        "supporting_document_recommendations": [s.model_dump(mode="json") for s in supporting],
    }


def nothing_needed_view() -> dict:
    """A clean case: no finding for further evidence to address."""
    incorp = _document("0b22b22b-0000-0000-0000-000000000001", "C003_CertIncorp.pdf", "certificate_of_incorporation")
    return _additional_documents_view([incorp], {incorp.id: _report()}, [], "not_asked")


def no_reducible_findings_view() -> dict:
    """A UEN ACRA has no record of: a registry discrepancy, which no further document can reduce."""
    incorp = _document("0c33c33c-0000-0000-0000-000000000001", "C058_CertIncorp.pdf", "certificate_of_incorporation")
    finding = _finding(1, "external_validation_tool", "high", "ACRA has no record of UEN 202736160G.",
                       "A registered company is always on ACRA's register.",
                       check_code="REG.UEN_EXISTS", stated="202736160G")
    requests = [EvidenceRequest(
        title="UEN Registration Confirmation Letter",
        what_it_shows="Confirms the UEN printed on the incorporation certificate.",
        addresses=[f"{incorp.id[:8]}:external_validation_tool-1"],
    )]
    return _additional_documents_view([incorp], {incorp.id: _report(finding)}, requests, "ok")


def recommendations_view() -> dict:
    """A large supplier payment and a 43.3% rise in bank borrowings: a purchase order and loan evidence to request."""
    statement = _document("0d44d44d-0000-0000-0000-000000000001", "C001_BankStatement.pdf", "bank_statement", {
        "company_name": "GOLDEN HARVEST TRADING PTE LTD",
        "transactions": [{
            "date": "11-Jun-26", "amount": -22506.48, "balance": 17990.65,
            "description": "REMITTANCE TRANSFER OF FUNDS RTF UNITED PAPER SUPPLY PTE. LTD. SGD 22,506",
        }],
    })
    financials = _document("0d44d44d-0000-0000-0000-000000000002", "C001_Financials.pdf", "source_of_wealth", {
        "company_name": "GOLDEN HARVEST TRADING PTE LTD", "uen": "201811234K",
        "balance_sheet_items": [
            {"section": "current_liabilities", "label": "Bank borrowings, current portion",
             "amount": 516_090, "amount_prior_year": 343_330},
            {"section": "non_current_liabilities", "label": "Bank borrowings, non-current portion",
             "amount": 1_334_700, "amount_prior_year": 948_200},
        ],
    })
    documents = [statement, financials]
    return _additional_documents_view(documents, {d.id: _report() for d in documents}, [], "not_asked")


VIEWS = {
    "scored": scored_case_view,
    "nothing needed": nothing_needed_view,
    "no reducible findings": no_reducible_findings_view,
    "recommendations": recommendations_view,
}


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-83", "CM-007")
def test_the_scored_case_lists_its_findings_by_severity_with_repeats_scored_once():
    risk = scored_case_view()["risk"]

    assert (risk["level"], risk["score"]) == ("High", 58)
    assert [(f["tool"], f["severity"], f["points"]) for f in risk["contributing_findings"]] == [
        ("font_consistency_tool", "high", 8.0),
        ("layout_anomalies_tool", "medium", 3.0),
        ("font_consistency_tool", "high", 2.0),  # the same check again on the same document scores a quarter
        ("spelling_tool", "low", 1.0),
        ("layout_anomalies_tool", "medium", 0.75),
        ("layout_anomalies_tool", "medium", 0.75),
    ]
    assert [c["tool"] for c in risk["unassessed_checks"]] == ["metadata_tool"]


def test_a_clean_case_needs_no_further_documents():
    additional = nothing_needed_view()["additional_documents"]

    assert (additional["reason"], additional["items"]) == ("none_needed", [])


def test_a_registry_discrepancy_cannot_be_reduced_by_a_further_document():
    view = no_reducible_findings_view()

    assert view["additional_documents"]["reason"] == "no_reducible_findings"
    [item] = view["additional_documents"]["items"]
    assert (item["title"], item["affects_score"]) == ("UEN Registration Confirmation Letter", False)
    assert view["additional_documents"]["best_case_score"] == view["risk"]["score"]


def test_a_large_payment_and_rising_borrowings_ask_for_a_purchase_order_and_loan_evidence():
    recommendations = recommendations_view()["supporting_document_recommendations"]

    assert [r["document_type"] for r in recommendations] == ["purchase_order", "bank_loan_evidence"]


def test_the_playwright_suites_case_views_match_the_backend():
    views = {name: view() for name, view in VIEWS.items()}

    if os.environ.get("REGENERATE_REGRESSION_FIXTURES"):
        CASE_VIEWS_FIXTURE.write_text(json.dumps(views, indent=2, sort_keys=True) + "\n")
    assert json.loads(CASE_VIEWS_FIXTURE.read_text()) == json.loads(json.dumps(views)), (
        "case-views.json is out of date with the case analysis — regenerate it (see above)"
    )
