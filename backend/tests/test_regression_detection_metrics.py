"""
Regression tests (SCRUM-323) for detection accuracy — "Test Cases - Sprint 5.xlsx":
  Case Management sheet, Overall Case Dashboard (SCRUM-297): CM-060 – CM-063
  Agentic Evaluation sheet, precision and recall (SCRUM-297): AE-013 – AE-015

The reviews are the sheet's own: on the Brightwave Analytics case, 10 spelling findings on clean documents, 4 marked
wrong and 6 correct; 2 reviewed font findings; a numbers check that caught 5 real issues and missed 1; and a metadata
check whose every finding was marked wrong, so it has no real issue to measure recall against. They go through the
real GET /detection-metrics (the dashboard's Accuracy tab) and GET /cases/{id}/finding-reviews (the case's Risk
section), which compute precision, recall and flags with the real detection_metrics. Only the database read is in
memory.

The Accuracy tab's Playwright tests (dashboard.spec.ts) are served this endpoint's response, from
fixtures/regression/detection-metrics-sprint5.json; a test below fails if it drifts. Regenerate with
scripts/regenerate_regression_fixtures.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import CaseRecord, FindingReview
from tests.conftest import TEST_USER
from tests.support.regression_fixtures import check_fixture

client = TestClient(app)
METRICS_FIXTURE = Path(__file__).parent / "fixtures" / "regression" / "detection-metrics-sprint5.json"

NOW = datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc)
CASE_ID = "b7a1e3c2-5d4f-4a60-9c1e-2f3d4b5a6c70"
BANK_STATEMENT, PAYSLIP, NOA = "doc-bank-statement", "doc-payslip", "doc-iras-noa"


def review(check: str, verdict: str | None, document_id: str, n: int, *, kind: str = "verdict") -> FindingReview:
    return FindingReview(
        id=f"{check}-{document_id}-{n}", case_id=CASE_ID, document_id=document_id, scope="document", kind=kind,
        fingerprint=None if kind == "missed" else f"{check}:{document_id}:{n}", check_name=check,
        agent_decision="accepted", severity="medium", verdict=verdict, reviewer=TEST_USER,
        created_at=NOW, updated_at=NOW,
    )


def brightwave_reviews() -> list[FindingReview]:
    spelling = (
        [review("spelling_tool", "wrong", BANK_STATEMENT, n) for n in range(4)]
        + [review("spelling_tool", "correct", PAYSLIP, n) for n in range(6)]
    )
    font = [review("font_consistency_tool", "correct", NOA, n) for n in range(2)]
    numbers = (
        [review("numerical_checks_tool", "correct", BANK_STATEMENT, n) for n in range(5)]
        + [review("numerical_checks_tool", None, PAYSLIP, 5, kind="missed")]
    )
    metadata = [review("metadata_tool", "wrong", NOA, n) for n in range(5)]
    return spelling + font + numbers + metadata


def dashboard_metrics(reviews: list[FindingReview]) -> dict:
    with patch("app.routers.finding_reviews.findings_repository.list_reviews", return_value=reviews):
        response = client.get("/detection-metrics")
    assert response.status_code == 200
    return response.json()


def by_check(summary: dict) -> dict[str, dict]:
    """A summary's checks (a MetricsSummary: the dashboard's overall, or one document's), by name."""
    return {c["check_name"]: c for c in summary["by_check"]}


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-297", "CM-060")
def test_each_check_shows_its_counts_and_its_false_positive_rate():
    spelling = by_check(dashboard_metrics(brightwave_reviews())["overall"])["spelling_tool"]

    assert (spelling["correct"], spelling["wrong"], spelling["missed"]) == (6, 4, 0)
    assert spelling["precision"] == 0.6  # 4 wrong of 10: a 40% false-positive rate


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-297", "CM-061 / AE-015")
def test_a_check_under_its_target_is_flagged_and_one_meeting_both_is_not():
    metrics = dashboard_metrics(brightwave_reviews())
    checks = by_check(metrics["overall"])

    assert (metrics["target_precision"], metrics["target_recall"]) == (0.8, 0.8)
    assert checks["spelling_tool"]["flags"] == ["Precision below target"]
    assert checks["metadata_tool"]["flags"] == ["Precision below target"]
    # Precision 5 of 5 and recall 5 of 6 (83%): both over 80%.
    assert (checks["numerical_checks_tool"]["precision"], checks["numerical_checks_tool"]["recall"]) == (1.0, 0.8333)
    assert checks["numerical_checks_tool"]["flags"] == []


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-297", "CM-061 / AE-015")
def test_a_check_that_misses_too_many_real_issues_is_flagged_on_recall():
    reviews = [review("date_consistency_tool", "correct", PAYSLIP, n) for n in range(3)] + [
        review("date_consistency_tool", None, PAYSLIP, n, kind="missed") for n in range(3, 5)
    ]

    [dates] = dashboard_metrics(reviews)["overall"]["by_check"]

    assert (dates["precision"], dates["recall"]) == (1.0, 0.6)
    assert dates["flags"] == ["Recall below target"]


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-297", "CM-062")
def test_a_check_with_fewer_than_five_reviews_is_marked_as_not_yet_reliable():
    metrics = dashboard_metrics(brightwave_reviews())
    font = by_check(metrics["overall"])["font_consistency_tool"]

    assert metrics["min_reviews"] == 5
    assert (font["correct"], font["wrong"], font["missed"]) == (2, 0, 0)
    assert font["flags"] == ["Few reviews"]  # its rates (both 100%) aren't under target, just not yet meaningful


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-297", "CM-063")
def test_the_case_page_shows_each_documents_own_results_for_a_check():
    # The dashboard counts the documents reviewed; the per-document results are on the case's Risk section.
    reviews = brightwave_reviews()
    case = CaseRecord(id=CASE_ID, greenlink_case_id="GLDB-UAT2", company_name="Brightwave Analytics Pte Ltd",
                      created_by=TEST_USER, created_at=NOW, updated_at=NOW)
    with (
        patch("app.routers._dependencies.case_service.get_case", return_value=case),
        patch("app.routers.finding_reviews.findings_repository.list_reviews_for_case", return_value=reviews),
        patch("app.routers.finding_reviews.findings_repository.list_sign_offs", return_value=[]),
    ):
        response = client.get(f"/cases/{CASE_ID}/finding-reviews")
    assert response.status_code == 200
    documents = response.json()["documents"]

    spelling_on = {d: by_check(documents[d]).get("spelling_tool") for d in documents}
    assert (spelling_on[BANK_STATEMENT]["wrong"], spelling_on[BANK_STATEMENT]["precision"]) == (4, 0.0)
    assert (spelling_on[PAYSLIP]["correct"], spelling_on[PAYSLIP]["precision"]) == (6, 1.0)
    assert spelling_on[NOA] is None
    assert dashboard_metrics(reviews)["documents_reviewed"] == 3


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-297", "AE-013")
def test_precision_and_recall_are_given_for_each_check_and_the_whole_system():
    metrics = dashboard_metrics(brightwave_reviews())
    overall = metrics["overall"]

    assert set(by_check(overall)) == {"spelling_tool", "font_consistency_tool", "numerical_checks_tool", "metadata_tool"}
    # 13 correct of 22 reviewed findings; 13 caught of 14 real issues.
    assert (overall["correct"], overall["wrong"], overall["missed"]) == (13, 9, 1)
    assert (overall["precision"], overall["recall"]) == (0.5909, 0.9286)
    assert (metrics["cases_reviewed"], metrics["documents_reviewed"]) == (1, 3)


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-297", "AE-014")
def test_a_check_with_no_real_issues_has_no_recall_rather_than_zero_or_one_hundred():
    metadata = by_check(dashboard_metrics(brightwave_reviews())["overall"])["metadata_tool"]

    assert (metadata["correct"], metadata["missed"]) == (0, 0)
    assert metadata["recall"] is None  # shown as "Not measured"
    assert metadata["precision"] == 0.0


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-297", "AE-013 / AE-014")
def test_with_no_reviews_neither_rate_is_given():
    metrics = dashboard_metrics([])

    assert metrics["overall"]["by_check"] == []
    assert (metrics["overall"]["precision"], metrics["overall"]["recall"]) == (None, None)


def test_the_playwright_suites_detection_metrics_match_the_backend():
    metrics = dashboard_metrics(brightwave_reviews())

    check_fixture(METRICS_FIXTURE, metrics)
