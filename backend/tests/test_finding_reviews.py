from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import detection_metrics
from app.langgraph.detection.specialists import SPECIALISTS
from app.main import app
from app.models import CaseRecord, DocumentRecord, DocumentStatus, FindingReview
from tests.conftest import TEST_USER

client = TestClient(app)

_TIMESTAMP = datetime.fromisoformat("2026-10-23T02:00:00+00:00")
CASE_ID = "11111111-1111-1111-1111-111111111111"
OTHER_CASE_ID = "22222222-2222-2222-2222-222222222222"
DOCUMENT_ID = "33333333-3333-3333-3333-333333333333"
OTHER_DOCUMENT_ID = "66666666-6666-6666-6666-666666666666"
REVIEW_ID = "44444444-4444-4444-4444-444444444444"


def _review(**overrides) -> FindingReview:
    return FindingReview(**{
        "id": REVIEW_ID, "case_id": CASE_ID, "document_id": DOCUMENT_ID, "scope": "document", "kind": "verdict",
        "finding_id": "33333333:numerical_checks_tool-1", "check_name": "numerical_checks_tool",
        "verdict": "correct", "reviewer": TEST_USER, "created_at": _TIMESTAMP, "updated_at": _TIMESTAMP,
        **overrides,
    })


# --- metrics -------------------------------------------------------------------------------------------------


def test_precision_counts_correct_against_wrong_and_recall_against_missed() -> None:
    reviews = [
        _review(verdict="correct"), _review(verdict="correct"), _review(verdict="wrong"),
        _review(kind="missed", verdict=None, finding_id=None),
    ]
    summary = detection_metrics.summarize(reviews)
    [check] = summary.by_check
    assert (check.correct, check.wrong, check.missed) == (2, 1, 1)
    assert check.precision == pytest.approx(2 / 3, abs=1e-4)
    assert check.recall == pytest.approx(2 / 3, abs=1e-4)


def test_a_check_with_nothing_to_measure_has_no_rate_not_zero_or_one_hundred() -> None:
    # Only misses: nothing it raised was reviewed (no precision), and it caught none of them (recall 0).
    [missed_only] = detection_metrics.summarize([_review(kind="missed", verdict=None, finding_id=None)]).by_check
    assert missed_only.precision is None and missed_only.recall == 0.0
    # Only wrong findings: precision 0, and nothing real it should have caught (no recall).
    [wrong_only] = detection_metrics.summarize([_review(verdict="wrong")]).by_check
    assert wrong_only.precision == 0.0 and wrong_only.recall is None
    assert detection_metrics.summarize([]).precision is None


def test_checks_below_target_or_with_few_reviews_are_flagged() -> None:
    reviews = [_review(verdict="wrong")] * 3 + [_review(verdict="correct")] * 3
    [check] = detection_metrics.summarize(reviews).by_check
    assert "Precision below target" in check.flags
    assert "Few reviews" not in check.flags
    [small] = detection_metrics.summarize([_review()]).by_check
    assert small.flags == ["Few reviews"]


def test_case_metrics_split_per_document_and_across_documents() -> None:
    reviews = [
        _review(),
        _review(document_id=OTHER_DOCUMENT_ID, finding_id="66666666:spelling_tool-1", check_name="spelling_tool",
                verdict="wrong"),
        _review(scope="case", document_id=None, finding_id="xd-1", check_name="cross_document_variance"),
    ]
    result = detection_metrics.case_metrics(reviews)
    assert set(result.documents) == {DOCUMENT_ID, OTHER_DOCUMENT_ID}
    assert result.documents[OTHER_DOCUMENT_ID].precision == 0.0
    assert result.cross_document.correct == 1 and result.cross_document.by_check[0].scope == "case"
    assert (result.case.correct, result.case.wrong) == (2, 1)


def test_the_document_checks_are_the_specialists_tools() -> None:
    tools = {tool.name for spec in SPECIALISTS.values() for tool in spec.tools}
    assert set(detection_metrics.DOCUMENT_CHECKS) == tools


# --- endpoints -----------------------------------------------------------------------------------------------


def _case(case_id: str = CASE_ID) -> CaseRecord:
    return CaseRecord(id=case_id, greenlink_case_id="GLDB-1", created_at=_TIMESTAMP, updated_at=_TIMESTAMP)


def _document(case_id: str = CASE_ID) -> DocumentRecord:
    return DocumentRecord(
        id=DOCUMENT_ID, case_id=case_id, filename="CBR.pdf", storage_path="x/CBR.pdf",
        status=DocumentStatus.DETECTED, uploaded_at=_TIMESTAMP,
    )


@pytest.fixture
def repos():
    with (
        patch("app.routers._dependencies.case_service") as case_service,
        patch("app.routers.finding_reviews.finding_reviews_repository") as reviews_repo,
        patch("app.routers.finding_reviews.document_repository") as document_repo,
    ):
        case_service.get_case.side_effect = lambda case_id: _case(case_id) if case_id in (CASE_ID, OTHER_CASE_ID) else None
        document_repo.get_document.return_value = _document()
        reviews_repo.list_for_case.return_value = [_review()]
        yield reviews_repo, document_repo


_VERDICT = {
    "scope": "document", "document_id": DOCUMENT_ID, "finding_id": "33333333:numerical_checks_tool-1",
    "check_name": "numerical_checks_tool", "check_code": "PAY.NET", "summary": "Net pay doesn't add up",
    "verdict": "correct",
}


def test_a_verdict_is_signed_by_the_officer_and_returns_the_metrics(repos) -> None:
    reviews_repo, _ = repos
    response = client.put(f"/cases/{CASE_ID}/finding-reviews/verdict", json=_VERDICT)
    assert response.status_code == 200
    kwargs = reviews_repo.set_verdict.call_args.kwargs
    assert kwargs["reviewer"] == TEST_USER and kwargs["verdict"] == "correct" and kwargs["check_code"] == "PAY.NET"
    assert response.json()["case"]["precision"] == 1.0
    assert response.json()["checks"]["document"] == list(detection_metrics.DOCUMENT_CHECKS)


def test_a_null_verdict_clears_the_mark(repos) -> None:
    reviews_repo, _ = repos
    response = client.put(f"/cases/{CASE_ID}/finding-reviews/verdict", json={**_VERDICT, "verdict": None})
    assert response.status_code == 200
    reviews_repo.clear_verdict.assert_called_once_with(CASE_ID, _VERDICT["finding_id"])
    reviews_repo.set_verdict.assert_not_called()


def test_a_review_must_name_a_check_of_its_scope_and_a_document_in_the_case(repos) -> None:
    reviews_repo, document_repo = repos
    wrong_check = client.put(f"/cases/{CASE_ID}/finding-reviews/verdict", json={**_VERDICT, "check_name": "cross_document_variance"})
    assert wrong_check.status_code == 400
    # A document review without its document, or a case review with one, is malformed.
    assert client.put(f"/cases/{CASE_ID}/finding-reviews/verdict", json={**_VERDICT, "document_id": None}).status_code == 422
    document_repo.get_document.return_value = _document(case_id=OTHER_CASE_ID)
    assert client.put(f"/cases/{CASE_ID}/finding-reviews/verdict", json=_VERDICT).status_code == 404
    reviews_repo.set_verdict.assert_not_called()


def test_a_missed_issue_is_recorded_against_the_check_that_should_have_caught_it(repos) -> None:
    reviews_repo, _ = repos
    response = client.post(f"/cases/{CASE_ID}/finding-reviews/missed", json={
        "scope": "case", "check_name": "identity_entity_conflict", "note": "  Director's NRIC differs on the CBR  ",
    })
    assert response.status_code == 201
    kwargs = reviews_repo.add_missed.call_args.kwargs
    assert kwargs["scope"] == "case" and kwargs["document_id"] is None
    assert kwargs["note"] == "Director's NRIC differs on the CBR" and kwargs["reviewer"] == TEST_USER


def test_a_review_can_only_be_deleted_from_its_own_case(repos) -> None:
    reviews_repo, _ = repos
    reviews_repo.get.return_value = _review(case_id=OTHER_CASE_ID)
    assert client.delete(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}").status_code == 404
    reviews_repo.delete.assert_not_called()
    reviews_repo.get.return_value = _review()
    assert client.delete(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}").status_code == 200
    reviews_repo.delete.assert_called_once_with(REVIEW_ID)


def test_detection_metrics_cover_every_reviewed_case(repos) -> None:
    reviews_repo, _ = repos
    reviews_repo.list_all.return_value = [
        _review(), _review(case_id=OTHER_CASE_ID, document_id=OTHER_DOCUMENT_ID, verdict="wrong"),
    ]
    body = client.get("/detection-metrics").json()
    assert body["cases_reviewed"] == 2 and body["documents_reviewed"] == 2
    assert body["overall"]["precision"] == 0.5
    assert body["target_precision"] == detection_metrics.TARGET_PRECISION
