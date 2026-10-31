"""The unified additional-documents list: matching ids, keeping non-scoring requests, and why a list is empty."""

from __future__ import annotations

from app.case_pipeline.models import EvidenceRequest
from app.case_pipeline.risk.additional_documents import build_additional_documents
from app.case_pipeline.risk.outlook import build_outlook, resolve_finding_id
from app.models import CaseRisk, ContributingFinding, SupportingDocumentRecommendation


def _finding(finding_id: str, category: str, points: float = 8) -> ContributingFinding:
    return ContributingFinding(
        document_id=finding_id.split(":")[0], filename="f.pdf", finding_id=finding_id, fingerprint=finding_id, severity="high",
        summary="s", rationale="r", points=points, category=category,
    )


def _risk(*findings: ContributingFinding) -> CaseRisk:
    return CaseRisk(state="scored", level="High", score=65, points=sum(f.points for f in findings), contributing_findings=list(findings))


def _rule() -> SupportingDocumentRecommendation:
    return SupportingDocumentRecommendation(
        document_type="bank_loan_evidence", title="Bank Loan Statements", reason="borrowing grew", request="Request", source_document_id="d", source_filename="f.pdf",
    )


def _build(risk, requests, rules=(), status="ok"):
    outlook = build_outlook(risk, requests)
    return build_additional_documents(risk, requests, outlook, list(rules), status)


def test_prefix_matches_only_a_single_finding() -> None:
    by_id = {f.finding_id: f for f in (_finding("60172ada:ext-1", "registry_discrepancy"), _finding("d75f5cff:ext-1", "registry_discrepancy"))}
    assert resolve_finding_id("60172ada", by_id) == "60172ada:ext-1"
    assert resolve_finding_id("60172ada:ext-1", by_id) == "60172ada:ext-1"
    assert resolve_finding_id("missing", by_id) is None
    assert resolve_finding_id("", by_id) is None


def test_prefix_shared_by_two_findings_is_not_guessed() -> None:
    by_id = {f.finding_id: f for f in (_finding("aaaa:x-1", "registry_discrepancy"), _finding("aaaa:x-2", "registry_discrepancy"))}
    assert resolve_finding_id("aaaa", by_id) is None


def test_case_with_only_non_reducible_findings_says_so_and_keeps_the_rule_item() -> None:
    risk = _risk(_finding("a:1", "registry_discrepancy"), _finding("b:1", "tampering_forensic"))

    result = _build(risk, [], [_rule()], status="ok")

    assert result.reason == "no_reducible_findings"
    assert [(i.title, i.source, i.affects_score) for i in result.items] == [("Bank Loan Statements", "checklist", False)]


def test_request_on_a_non_reducible_finding_is_kept_without_a_score() -> None:
    risk = _risk(_finding("60172ada:ext-1", "registry_discrepancy"), _finding("x:1", "cross_document_variance", 3))
    requests = [
        EvidenceRequest(title="UEN Registration Confirmation Letter", what_it_shows="confirms the UEN", addresses=["60172ada"]),
        EvidenceRequest(title="Lease Agreement", what_it_shows="explains the variance", addresses=["x:1"]),
    ]

    result = _build(risk, requests)

    by_title = {i.title: i for i in result.items}
    assert by_title["UEN Registration Confirmation Letter"].affects_score is False
    assert by_title["UEN Registration Confirmation Letter"].addresses == ["60172ada:ext-1"]
    assert by_title["Lease Agreement"].affects_score is True
    assert by_title["Lease Agreement"].score_if_supports is not None
    assert result.reason is None


def test_duplicate_titles_are_merged_agent_first() -> None:
    risk = _risk(_finding("x:1", "cross_document_variance", 3))
    request = EvidenceRequest(title="bank loan statements", what_it_shows="why", addresses=["x:1"])

    result = _build(risk, [request], [_rule()])

    assert [(i.title, i.source) for i in result.items] == [("bank loan statements", "finding")]


def test_empty_reasons() -> None:
    clean = CaseRisk(state="scored", level="Low", score=0, points=0, contributing_findings=[])
    reducible = _risk(_finding("x:1", "cross_document_variance", 3))

    assert _build(clean, [], status="not_asked").reason == "none_needed"
    assert _build(reducible, [], status="failed").reason == "model_failed"
    assert _build(reducible, [], status="budget").reason == "budget"
