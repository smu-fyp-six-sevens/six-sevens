"""
Regression tests (SCRUM-296) for the case risk score — "Test Cases - Sprint
4.xlsx", Risk Scoring sheet:

  SCRUM-84  Calculate a case risk score from detection findings   RS-001 – RS-005
  SCRUM-90  Show the score and its reasons together               RS-006 – RS-008

Runs the real scoring (case_pipeline/risk/case_risk.py) over detection
reports shaped like the ones the detection graph stores, using the documents
and findings from the Sprint 4 live run (C002 / AisyahRazak, TanAhKow,
LimCG). Scoring is pure code — no Claude — so nothing is scripted here.

Each scenario's result is also the shared fixture the Playwright suite
renders (fixtures/regression/case-risk-sprint4.json, used by
frontend/e2e/specs/case-analysis.spec.ts). A test below fails if scoring
drifts from it; regenerate it with REGENERATE_REGRESSION_FIXTURES=1.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.case_pipeline.models import CrossDocFinding, FindingCategory
from app.case_pipeline.risk.case_risk import compute_case_risk, scoring_breakdown
from app.models import CaseRisk, DocumentRecord, DocumentStatus

FIXTURES = Path(__file__).parent / "fixtures" / "regression"
CASE_RISK_FIXTURE = FIXTURES / "case-risk-sprint4.json"
_UPLOADED = datetime(2026, 10, 20, tzinfo=timezone.utc)


def _document(document_id: str, filename: str, document_type: str,
              status: DocumentStatus = DocumentStatus.DETECTED) -> DocumentRecord:
    return DocumentRecord(id=document_id, case_id="case-1", filename=filename, document_type=document_type,
                          status=status, uploaded_at=_UPLOADED)


def _finding(number: int, tool: str, severity: str, summary: str, rationale: str = "", **extra) -> dict:
    """One finding as merge_reports stores it: the specialist's final severity and rationale."""
    return {"id": f"{tool}-{number}", "tool": tool, "severity": severity, "summary": summary,
            "rationale": rationale or f"Rated {severity} by the specialist.", **extra}


def _report(*findings: dict) -> dict:
    return {"verdict": "flagged" if findings else "clean", "findings": list(findings),
            "unable_to_assess": [], "consulted": []}


# --- the scenarios -----------------------------------------------------------------
# Each returns (documents, reports, failed_ids, cross-document findings).


def clean_case():
    """RS-001: three clean documents, no findings."""
    documents = [
        _document("a1a1a1a1-0000-0000-0000-000000000001", "C003_CertIncorp.pdf", "certificate_of_incorporation"),
        _document("a1a1a1a1-0000-0000-0000-000000000002", "C003_NOA.pdf", "notice_of_assessment"),
        _document("a1a1a1a1-0000-0000-0000-000000000003", "C003_BankStatement_2026-06_to_2026-08.pdf", "bank_statement"),
    ]
    return documents, {d.id: _report() for d in documents}, set(), []


def failed_detection_case():
    """RS-002: three documents, one whose last detection run failed and stored no report."""
    documents, reports, _, _ = clean_case()
    failed = documents[2]
    del reports[failed.id]
    failed.status = DocumentStatus.APPROVED  # a crashed run puts the document back to approved
    return documents, reports, {failed.id}, []


def critical_case():
    """RS-003: the live run's C002 set — high findings on 4 of 5 documents, plus a cross-document finding."""
    payslip = _document("c002c002-0000-0000-0000-000000000001", "AisyahRazak_Payslip_CpfOverCeiling.pdf", "payslip")
    nric = _document("c002c002-0000-0000-0000-000000000002", "C002-P1_NRICPassport.pdf", "nric")
    incorp = _document("c002c002-0000-0000-0000-000000000003", "C002_CertIncorp.pdf", "certificate_of_incorporation")
    noa = _document("c002c002-0000-0000-0000-000000000004", "C002_NOA.pdf", "notice_of_assessment")
    statement = _document("c002c002-0000-0000-0000-000000000005", "C002_BankStatement_2026-06_to_2026-08.pdf",
                          "bank_statement")
    reports = {
        payslip.id: _report(_finding(
            1, "external_validation_tool", "high",
            "Employee CPF of $1,920.00 is above the most any band allows at the $8,000 ceiling",
            check_code="PS.CPF_RATE", stated="1920.00")),
        nric.id: _report(
            _finding(1, "numerical_checks_tool", "high", "NRIC S7534567X fails its checksum: the check letter should be D"),
            _finding(1, "external_validation_tool", "high", "The passport's issuing authority is not one Singapore uses",
                     check_code="REG.ISSUER", stated="OFSINGAPORE")),
        incorp.id: _report(
            _finding(1, "date_consistency_tool", "high", "incorporation_date (2027-04-18) is after today's date"),
            _finding(1, "external_validation_tool", "high", "ACRA has no record of UEN 202736160G",
                     check_code="REG.UEN_EXISTS", stated="202736160G")),
        noa.id: _report(_finding(
            1, "external_validation_tool", "high", "IRAS's assessment year does not match the year of income",
            check_code="TAX.YEAR_OF_ASSESSMENT", stated="YA 2027")),
        statement.id: _report(),
    }
    cross = CrossDocFinding(
        id="deterministic-1", hypothesis_id="deterministic", severity="high",
        category=FindingCategory.CROSS_DOCUMENT_VARIANCE,
        summary="The employer's CPF contributions on the payslip don't appear in the company's bank statement.",
        rationale="Three documents disagree on the same salary payment.",
        document_ids=[payslip.id, statement.id, noa.id],
    )
    return [payslip, nric, incorp, noa, statement], reports, set(), [cross]


def single_medium_case():
    """RS-004: one medium finding on one document (3 points)."""
    nric = _document("7a4a7a4a-0000-0000-0000-000000000001", "TanAhKow_NRIC.pdf", "nric")
    reports = {nric.id: _report(_finding(
        1, "layout_anomalies_tool", "medium", "The PHOTO label sits 83.8 pt from where the NRIC layout places it"))}
    return [nric], reports, set(), []


def single_high_case():
    """RS-005: one high finding on one document (8 points)."""
    passport = _document("11c911c9-0000-0000-0000-000000000001", "LimCG_Passport_scan.jpeg", "passport")
    reports = {passport.id: _report(_finding(
        1, "numerical_checks_tool", "high", "Passport number K5690850X fails its checksum"))}
    return [passport], reports, set(), []


def one_of_each_severity_case():
    """RS-006 / RS-007: one high, one medium and one low finding, each on a different document."""
    payslip = _document("e5e5e5e5-0000-0000-0000-000000000001", "C011_Payslip.pdf", "payslip")
    statement = _document("e5e5e5e5-0000-0000-0000-000000000002", "C011_BankStatement.pdf", "bank_statement")
    incorp = _document("e5e5e5e5-0000-0000-0000-000000000003", "C011_CertIncorp.pdf", "certificate_of_incorporation")
    reports = {
        # Listed low first, so the order shown can only come from the scoring.
        incorp.id: _report(_finding(1, "spelling_tool", "low", 'Possible misspelling "Controling".',
                                    "Minor, but formal certificates are proofread.")),
        statement.id: _report(_finding(1, "numerical_checks_tool", "medium",
                                       "Available balance does not equal ledger balance minus holds.",
                                       "A difference of SGD 1.00 points to an edited figure.")),
        payslip.id: _report(_finding(1, "font_consistency_tool", "high",
                                     "Net pay is in a different font from the rest of the payslip.",
                                     "Only the figure that matters differs, which is how a pasted value looks.")),
    }
    return [payslip, statement, incorp], reports, set(), []


def half_scanned_case():
    """RS-008: one of two documents still waiting for detection."""
    scanned = _document("08080808-0000-0000-0000-000000000001", "C001_CertIncorp.pdf", "certificate_of_incorporation")
    waiting = _document("08080808-0000-0000-0000-000000000002", "C001_NOA.pdf", "notice_of_assessment",
                        status=DocumentStatus.DETECTING)
    return [scanned, waiting], {scanned.id: _report()}, set(), []


SCENARIOS = {
    "RS-001": clean_case,
    "RS-002": failed_detection_case,
    "RS-003": critical_case,
    "RS-004": single_medium_case,
    "RS-005": single_high_case,
    "RS-006": one_of_each_severity_case,
    "RS-008": half_scanned_case,
}


def score(scenario) -> CaseRisk:
    documents, reports, failed_ids, cross = scenario()
    return compute_case_risk(documents, reports, failed_ids, cross)


# --- SCRUM-84 Calculate a case risk score ------------------------------------------


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-84", "RS-001")
def test_a_clean_case_is_scored_low_with_no_findings():
    risk = score(clean_case)

    assert risk.state == "scored"
    assert (risk.level, risk.score, risk.points) == ("Low", 0, 0)
    assert risk.contributing_findings == []
    assert risk.reasons == ["Rated Low: detection found nothing worth flagging."]
    assert (risk.documents_scanned, risk.documents_total) == (3, 3)


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-84", "RS-002")
def test_a_failed_detection_run_leaves_the_case_failed_with_no_score():
    risk = score(failed_detection_case)

    assert risk.state == "failed"
    assert risk.level is None and risk.score is None
    assert risk.contributing_findings == []
    assert (risk.documents_scanned, risk.documents_total) == (2, 3)


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-84", "RS-003")
def test_high_findings_on_three_or_more_documents_are_critical():
    risk = score(critical_case)

    assert risk.level == "Critical"
    assert 80 <= risk.score <= 100
    assert risk.reasons == ["Rated Critical: high-severity findings on 4 documents."]
    # The live run's figures: 60 points from 7 findings on 5/5 documents, 89/100.
    assert risk.points == 60
    assert risk.score == 89
    assert len(risk.contributing_findings) == 7
    assert (risk.documents_scanned, risk.documents_total) == (5, 5)


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-84", "RS-003")
def test_high_findings_on_two_documents_are_not_critical():
    documents, reports, failed_ids, _ = critical_case()
    kept = documents[:2]

    risk = compute_case_risk(kept, {d.id: reports[d.id] for d in kept}, failed_ids)

    assert risk.level == "High"
    assert risk.score < 80


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-84", "RS-004")
def test_a_single_medium_finding_is_medium():
    risk = score(single_medium_case)

    assert risk.level == "Medium"
    assert 25 <= risk.score <= 49
    [finding] = risk.contributing_findings
    assert (finding.severity, finding.points, finding.filename) == ("medium", 3, "TanAhKow_NRIC.pdf")


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-84", "RS-005")
def test_a_single_high_finding_is_high_at_50():
    risk = score(single_high_case)

    assert (risk.level, risk.score, risk.points) == ("High", 50, 8)
    assert risk.reasons == ["Rated High from the combined severity of 1 finding(s)."]
    [finding] = risk.contributing_findings
    assert (finding.tool, finding.filename) == ("numerical_checks_tool", "LimCG_Passport_scan.jpeg")


# --- SCRUM-90 Show the score and its reasons together ------------------------------


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-90", "RS-006")
def test_each_contributing_finding_carries_its_document_check_severity_and_reasons():
    risk = score(one_of_each_severity_case)

    for finding in risk.contributing_findings:
        assert finding.filename and finding.document_type and finding.tool
        assert finding.severity in ("high", "medium", "low")
        assert finding.summary and finding.rationale
        assert finding.points > 0
    high = risk.contributing_findings[0]
    assert (high.filename, high.document_type, high.tool) == ("C011_Payslip.pdf", "payslip", "font_consistency_tool")
    # The trace's scoring table (the debug panel) lists each finding's points too.
    breakdown = scoring_breakdown(risk)
    assert [(f["document"], f["severity"], f["points"]) for f in breakdown["findings"]] == [
        ("C011_Payslip.pdf", "high", 8), ("C011_BankStatement.pdf", "medium", 3), ("C011_CertIncorp.pdf", "low", 1),
    ]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-90", "RS-007")
def test_findings_are_listed_high_then_medium_then_low():
    risk = score(one_of_each_severity_case)

    assert [(f.severity, f.points) for f in risk.contributing_findings] == [("high", 8), ("medium", 3), ("low", 1)]


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-90", "RS-008")
def test_a_case_with_a_document_still_to_scan_has_no_score_and_no_findings():
    risk = score(half_scanned_case)

    assert risk.state == "pending"
    assert risk.level is None and risk.score is None
    assert risk.contributing_findings == []
    assert (risk.documents_scanned, risk.documents_total) == (1, 2)


# --- the Playwright suite's fixture ------------------------------------------------


@pytest.mark.sprint4
@pytest.mark.story("SCRUM-84 / SCRUM-90", "RS-001 – RS-008")
def test_the_playwright_suites_case_risk_fixture_matches_the_scoring():
    fixture = {}
    for test_case, scenario in SCENARIOS.items():
        documents, reports, failed_ids, cross = scenario()
        risk = compute_case_risk(documents, reports, failed_ids, cross)
        document_only = compute_case_risk(documents, reports, failed_ids)
        # The run trace's "scoring" step, as case_pipeline/service.py records it (the debug panel reads it).
        scoring = {
            **scoring_breakdown(risk), "cross_document_findings": len(cross),
            "score_without_cross_document": document_only.score, "level_without_cross_document": document_only.level,
        }
        fixture[test_case] = {"risk": risk.model_dump(mode="json"), "scoring": scoring if risk.state == "scored" else None}

    if os.environ.get("REGENERATE_REGRESSION_FIXTURES"):
        CASE_RISK_FIXTURE.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    assert json.loads(CASE_RISK_FIXTURE.read_text()) == json.loads(json.dumps(fixture)), (
        "case-risk-sprint4.json is out of date with the scoring — regenerate it (see above)"
    )
