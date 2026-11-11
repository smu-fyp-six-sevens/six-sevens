from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app import detection_metrics
from app.langgraph.detection.specialists import SPECIALISTS
from app.main import app
from app.models import CaseRecord, DocumentRecord, DocumentStatus, FindingReview, ReviewSignOff
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
        "fingerprint": "numerical_checks_tool:PAY.NET:1", "check_name": "numerical_checks_tool",
        "verdict": "correct", "reviewer": TEST_USER, "created_at": _TIMESTAMP, "updated_at": _TIMESTAMP,
        **overrides,
    })


# --- metrics -------------------------------------------------------------------------------------------------


def test_precision_counts_correct_against_wrong_and_recall_against_missed() -> None:
    reviews = [
        _review(verdict="correct"), _review(verdict="correct"), _review(verdict="wrong"),
        _review(kind="missed", verdict=None, fingerprint=None),
    ]
    summary = detection_metrics.summarize(reviews)
    [check] = summary.by_check
    assert (check.correct, check.wrong, check.missed) == (2, 1, 1)
    assert check.precision == pytest.approx(2 / 3, abs=1e-4)
    assert check.recall == pytest.approx(2 / 3, abs=1e-4)


@pytest.mark.parametrize("set_aside", [{"agent_decision": "dismissed"}, {"agent_decision": "accepted", "severity": "info"}])
def test_a_set_aside_finding_is_a_miss_when_real_and_counts_for_neither_when_not(set_aside) -> None:
    [check] = detection_metrics.summarize([_review(verdict="correct", **set_aside), _review(verdict="wrong", **set_aside)]).by_check
    assert (check.correct, check.wrong, check.missed) == (0, 0, 1)


def test_a_check_with_nothing_to_measure_has_no_rate_not_zero_or_one_hundred() -> None:
    # Only misses: nothing it raised was reviewed (no precision), and it caught none of them (recall 0).
    [missed_only] = detection_metrics.summarize([_review(kind="missed", verdict=None, fingerprint=None)]).by_check
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
        _review(document_id=OTHER_DOCUMENT_ID, fingerprint="spelling_tool:1", check_name="spelling_tool",
                verdict="wrong"),
        _review(scope="case", document_id=None, fingerprint="xd:1", check_name="cross_document_variance"),
    ]
    result = detection_metrics.case_metrics(reviews)
    assert set(result.documents) == {DOCUMENT_ID, OTHER_DOCUMENT_ID}
    assert result.documents[OTHER_DOCUMENT_ID].precision == 0.0
    assert result.cross_document.correct == 1 and result.cross_document.by_check[0].scope == "case"
    assert (result.case.correct, result.case.wrong) == (2, 1)
    assert (result.target_precision, result.target_recall) == (
        detection_metrics.TARGET_PRECISION, detection_metrics.TARGET_RECALL
    )


def test_the_checks_are_the_specialists_tools_and_the_finding_categories() -> None:
    tools = {tool.name for spec in SPECIALISTS.values() for tool in spec.tools}
    assert set(detection_metrics.document_checks()) == tools | {"other"}
    assert "cross_document_variance" in detection_metrics.CASE_CHECKS
    # The catch-all comes last in both lists.
    assert detection_metrics.document_checks()[-1] == detection_metrics.CASE_CHECKS[-1] == "other"


# --- endpoints -----------------------------------------------------------------------------------------------


def _case(case_id: str = CASE_ID, *, reviews_required: bool = True) -> CaseRecord:
    return CaseRecord(
        id=case_id, greenlink_case_id="GLDB-1", created_at=_TIMESTAMP, updated_at=_TIMESTAMP,
        reviews_required=reviews_required,
    )


def _document(case_id: str = CASE_ID) -> DocumentRecord:
    return DocumentRecord(
        id=DOCUMENT_ID, case_id=case_id, filename="CBR.pdf", storage_path="x/CBR.pdf",
        status=DocumentStatus.DETECTED, uploaded_at=_TIMESTAMP,
    )


@pytest.fixture
def repos():
    with (
        patch("app.routers._dependencies.case_service") as case_service,
        patch("app.routers.finding_reviews.findings_repository") as findings_repo,
        patch("app.routers.finding_reviews.document_repository") as document_repo,
        patch("app.routers.finding_reviews.feedback_repository") as feedback_repo,
        patch("app.routers.finding_reviews.push_case_snapshot", new=AsyncMock()),
    ):
        feedback_repo.verdict_key.side_effect = lambda case_id, document_id, fingerprint: f"v:{fingerprint}"
        feedback_repo.missed_key.side_effect = lambda row_id: f"m:{row_id}"
        case_service.get_case.side_effect = lambda case_id: _case(case_id) if case_id in (CASE_ID, OTHER_CASE_ID) else None
        document_repo.get_document.return_value = _document()
        findings_repo.list_reviews_for_case.return_value = [_review()]
        findings_repo.list_sign_offs.return_value = []
        # Every fingerprint names a current finding, whose row id is "row:<fingerprint>".
        findings_repo.find_current.side_effect = lambda case_id, scope, document_id, fingerprint: {
            "id": f"row:{fingerprint}", "check_name": fingerprint.split(":")[0], "summary": "A finding",
        }
        yield findings_repo, document_repo


_VERDICT = {
    "scope": "document", "document_id": DOCUMENT_ID, "fingerprint": "numerical_checks_tool:PAY.NET:1",
    "verdict": "correct",
}
_VERDICTS = f"/cases/{CASE_ID}/finding-reviews/verdicts"


def test_a_group_review_saves_every_finding_signed_by_the_officer_and_returns_the_metrics(repos) -> None:
    findings_repo, _ = repos
    response = client.put(_VERDICTS, json={"verdicts": [
        _VERDICT, {**_VERDICT, "fingerprint": "spelling_tool:1", "verdict": "wrong", "reason": "tool_misread"},
    ]})
    assert response.status_code == 200
    assert [c.args for c in findings_repo.set_verdict.call_args_list] == [
        ("row:numerical_checks_tool:PAY.NET:1", "correct", TEST_USER, None, None),
        ("row:spelling_tool:1", "wrong", TEST_USER, "tool_misread", None),
    ]
    findings_repo.find_current.assert_any_call(CASE_ID, "document", DOCUMENT_ID, "spelling_tool:1")
    assert response.json()["case"]["precision"] == 1.0


def test_a_group_with_no_findings_is_signed_off_by_the_officer(repos) -> None:
    findings_repo, _ = repos
    findings_repo.list_sign_offs.return_value = [ReviewSignOff(
        scope="document", document_id=DOCUMENT_ID, reviewer=TEST_USER, updated_at=_TIMESTAMP,
    )]
    response = client.put(
        f"/cases/{CASE_ID}/finding-reviews/sign-offs", json={"scope": "document", "document_id": DOCUMENT_ID},
    )
    assert response.status_code == 200
    assert [s["document_id"] for s in response.json()["sign_offs"]] == [DOCUMENT_ID]
    findings_repo.sign_off.assert_called_once_with(CASE_ID, "document", DOCUMENT_ID, TEST_USER)


def test_a_null_verdict_clears_the_mark(repos) -> None:
    findings_repo, _ = repos
    response = client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": None}]})
    assert response.status_code == 200
    findings_repo.set_verdict.assert_called_once_with("row:numerical_checks_tool:PAY.NET:1", None, TEST_USER, None, None)


def test_a_review_must_name_a_document_in_the_case(repos) -> None:
    findings_repo, document_repo = repos
    # A document review without its document, or a case review with one, is malformed.
    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "document_id": None}]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "scope": "case"}]}).status_code == 422
    document_repo.get_document.return_value = _document(case_id=OTHER_CASE_ID)
    assert client.put(_VERDICTS, json={"verdicts": [_VERDICT]}).status_code == 404
    findings_repo.set_verdict.assert_not_called()


def test_a_group_with_a_finding_a_newer_run_replaced_saves_nothing(repos) -> None:
    findings_repo, _ = repos
    findings_repo.find_current.side_effect = (
        lambda case_id, scope, document_id, fingerprint: None if fingerprint == "gone:1" else {"id": "row"}
    )
    response = client.put(_VERDICTS, json={"verdicts": [_VERDICT, {**_VERDICT, "fingerprint": "gone:1"}]})
    assert response.status_code == 409
    assert "newer run" in response.json()["detail"]
    findings_repo.set_verdict.assert_not_called()
    assert client.put(_VERDICTS, json={"verdicts": []}).status_code == 422


def test_a_case_finding_is_looked_up_without_a_document(repos) -> None:
    findings_repo, document_repo = repos
    response = client.put(_VERDICTS, json={"verdicts": [{"scope": "case", "fingerprint": "xd:1", "verdict": "correct"}]})
    assert response.status_code == 200
    findings_repo.find_current.assert_called_once_with(CASE_ID, "case", None, "xd:1")
    document_repo.get_document.assert_not_called()


def test_a_missed_issue_is_recorded_against_the_check_that_should_have_caught_it(repos) -> None:
    findings_repo, _ = repos
    response = client.post(f"/cases/{CASE_ID}/finding-reviews/missed", json={
        "scope": "case", "check_name": "identity_entity_conflict", "note": "  Director's NRIC differs on the CBR  ",
    })
    assert response.status_code == 201
    kwargs = findings_repo.add_missed.call_args.kwargs
    assert kwargs["scope"] == "case" and kwargs["document_id"] is None
    assert kwargs["note"] == "Director's NRIC differs on the CBR" and kwargs["reviewer"] == TEST_USER
    # A check from the other scope is refused.
    assert client.post(f"/cases/{CASE_ID}/finding-reviews/missed", json={
        "scope": "case", "check_name": "metadata_tool",
    }).status_code == 400
    assert findings_repo.add_missed.call_count == 1


@pytest.mark.parametrize("scope", ["document", "case"])
def test_an_issue_no_check_covers_is_filed_under_other_with_its_description(repos, scope) -> None:
    findings_repo, _ = repos
    place = {"scope": scope, "check_name": "other", **({"document_id": DOCUMENT_ID} if scope == "document" else {})}

    assert client.post(f"/cases/{CASE_ID}/finding-reviews/missed", json=place).status_code == 400
    assert client.post(f"/cases/{CASE_ID}/finding-reviews/missed", json={**place, "note": "   "}).status_code == 400
    response = client.post(f"/cases/{CASE_ID}/finding-reviews/missed", json={**place, "note": "Stamp looks scanned in"})

    assert response.status_code == 201
    assert findings_repo.add_missed.call_args.kwargs["check_name"] == "other"
    assert findings_repo.add_missed.call_count == 1


def test_a_missed_issue_can_be_corrected_but_a_verdict_cannot(repos) -> None:
    findings_repo, _ = repos
    findings_repo.get_review.return_value = _review(kind="missed", verdict=None, fingerprint=None)
    response = client.patch(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}", json={
        "check_name": "metadata_tool", "note": "  Author was edited  ",
    })
    assert response.status_code == 200
    findings_repo.update_missed.assert_called_once_with(
        REVIEW_ID, check_name="metadata_tool", note="Author was edited", reviewer=TEST_USER,
    )
    # A check from the wrong scope, or a verdict row, can't be edited this way.
    assert client.patch(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}", json={"check_name": "cross_document_variance"}).status_code == 400
    findings_repo.get_review.return_value = _review()
    assert client.patch(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}", json={"check_name": "metadata_tool"}).status_code == 400
    findings_repo.get_review.return_value = _review(case_id=OTHER_CASE_ID, kind="missed", verdict=None, fingerprint=None)
    assert client.patch(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}", json={"check_name": "metadata_tool"}).status_code == 404
    assert findings_repo.update_missed.call_count == 1


def test_removing_a_missed_issue_deletes_it_and_removing_a_verdict_clears_it(repos) -> None:
    findings_repo, _ = repos
    findings_repo.get_review.return_value = _review(case_id=OTHER_CASE_ID)
    assert client.delete(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}").status_code == 404
    findings_repo.get_review.return_value = _review(kind="missed", verdict=None, fingerprint=None)
    assert client.delete(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}").status_code == 200
    findings_repo.delete_missed.assert_called_once_with(REVIEW_ID)
    # The finding stays; only the officer's mark goes.
    findings_repo.get_review.return_value = _review()
    assert client.delete(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}").status_code == 200
    findings_repo.set_verdict.assert_called_once_with(REVIEW_ID, None, TEST_USER)
    assert findings_repo.delete_missed.call_count == 1


def test_detection_metrics_cover_every_reviewed_case(repos) -> None:
    findings_repo, _ = repos
    findings_repo.list_reviews.return_value = [
        _review(), _review(case_id=OTHER_CASE_ID, document_id=OTHER_DOCUMENT_ID, verdict="wrong"),
    ]
    body = client.get("/detection-metrics").json()
    assert body["cases_reviewed"] == 2 and body["documents_reviewed"] == 2
    assert body["overall"]["precision"] == 0.5
    assert body["target_precision"] == detection_metrics.TARGET_PRECISION


def test_detection_metrics_pass_the_date_window_on(repos) -> None:
    # October in Singapore: the dashboard sends the start of the officer's 1 Oct and of 1 Nov, in UTC.
    findings_repo, _ = repos
    findings_repo.list_reviews.return_value = []
    # As the browser's toISOString() writes them, URL-encoded.
    response = client.get("/detection-metrics?since=2026-09-30T16%3A00%3A00.000Z&before=2026-10-31T16%3A00%3A00.000Z")
    assert response.status_code == 200
    findings_repo.list_reviews.assert_called_once_with(
        datetime(2026, 9, 30, 16, tzinfo=timezone.utc), [], datetime(2026, 10, 31, 16, tzinfo=timezone.utc),
    )


# --- audit trail ---------------------------------------------------------------------------------------------


def test_a_saved_review_is_recorded_per_document_with_the_officer(repos, published_events) -> None:
    response = client.put(_VERDICTS, json={"verdicts": [
        _VERDICT,
        {**_VERDICT, "fingerprint": "spelling_tool:1", "verdict": "wrong", "reason": "not_relevant"},
        {**_VERDICT, "fingerprint": "metadata_tool:1", "verdict": "wrong", "reason": "within_tolerance"},
        {"scope": "case", "fingerprint": "xd:1", "verdict": "correct"},
    ]})
    assert response.status_code == 200
    saved = [event for event in published_events if event.type == "finding_review.saved"]
    assert [(event.document_id, event.actor, event.actor_type) for event in saved] == [
        (DOCUMENT_ID, TEST_USER, "user"), (None, TEST_USER, "user"),
    ]
    detail = saved[0].detail
    assert (detail["scope"], detail["real"], detail["false_alarms"], detail["cleared"]) == ("document", 1, 2, 0)
    assert (saved[1].detail["scope"], saved[1].detail["real"]) == ("case", 1)


def test_a_review_that_fails_to_save_records_nothing(repos, published_events) -> None:
    findings_repo, _ = repos
    findings_repo.set_verdict.side_effect = RuntimeError("supabase down")
    assert client.put(_VERDICTS, json={"verdicts": [_VERDICT]}).status_code == 502
    assert published_events == []


def test_missed_issues_reported_edited_and_removed_are_recorded(repos, published_events) -> None:
    findings_repo, _ = repos
    client.post(f"/cases/{CASE_ID}/finding-reviews/missed", json={
        "scope": "document", "document_id": DOCUMENT_ID, "check_name": "metadata_tool", "note": "Author was edited",
    })
    findings_repo.get_review.return_value = _review(kind="missed", verdict=None, fingerprint=None, check_name="metadata_tool")
    client.patch(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}", json={
        "check_name": "font_consistency_tool", "note": "Author was edited",
    })
    client.delete(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}")
    assert [(event.type, event.document_id, event.actor) for event in published_events] == [
        ("finding_review.missed_reported", DOCUMENT_ID, TEST_USER),
        ("finding_review.missed_edited", DOCUMENT_ID, TEST_USER),
        ("finding_review.removed", DOCUMENT_ID, TEST_USER),
    ]
    assert published_events[1].detail["from_check"] == "metadata_tool"
    assert published_events[1].detail["check_name"] == "font_consistency_tool"
    # Events carry ids, not the officer's free text.
    assert "note" not in published_events[0].detail


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("GET", f"/cases/{CASE_ID}/finding-reviews", None),
        ("PUT", _VERDICTS, {"verdicts": [_VERDICT]}),
        ("POST", f"/cases/{CASE_ID}/finding-reviews/missed", {"scope": "case", "check_name": "identity_entity_conflict"}),
    ],
)
def test_a_case_created_without_reviews_takes_none(repos, method, path, body) -> None:
    findings_repo, _ = repos
    with patch("app.routers._dependencies.case_service") as case_service:
        case_service.get_case.return_value = _case(reviews_required=False)
        response = client.request(method, path, json=body)

    assert response.status_code == 409
    assert response.json()["detail"] == "This case was created without finding reviews."
    findings_repo.list_reviews_for_case.assert_not_called()
    findings_repo.set_verdict.assert_not_called()
    findings_repo.add_missed.assert_not_called()


# --- feedback for the agents -----------------------------------------------------------------------------------


def test_every_verdict_is_saved_as_feedback_and_clearing_one_withdraws_it(repos) -> None:
    _, _ = repos
    with patch("app.routers.finding_reviews.feedback_repository") as feedback_repo:
        feedback_repo.verdict_key.side_effect = lambda case_id, document_id, fingerprint: f"v:{fingerprint}"
        response = client.put(_VERDICTS, json={"verdicts": [
            {**_VERDICT, "verdict": "wrong", "reason": "other", "note": "Stamp is a watermark"},
            {**_VERDICT, "fingerprint": "spelling_tool:1", "verdict": "correct"},
            {**_VERDICT, "fingerprint": "gone_tool:1", "verdict": None},
        ]})
    assert response.status_code == 200
    saved = [c.kwargs for c in feedback_repo.save.call_args_list]
    assert [(k["verdict"], k["reason"], k["note"], k["check_name"]) for k in saved] == [
        ("wrong", "other", "Stamp is a watermark", "numerical_checks_tool"), ("correct", None, None, "spelling_tool"),
    ]
    feedback_repo.withdraw.assert_called_once_with("v:gone_tool:1")


def test_a_reason_goes_with_a_false_alarm_only(repos) -> None:
    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "correct", "reason": "other"}]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "correct", "note": "x"}]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "wrong", "reason": "not_a_reason"}]}).status_code == 422


def test_a_false_alarm_must_say_why_and_other_needs_a_note(repos) -> None:
    findings_repo, _ = repos
    wrong = {**_VERDICT, "verdict": "wrong"}
    assert client.put(_VERDICTS, json={"verdicts": [wrong]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**wrong, "reason": "other"}]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**wrong, "reason": "other", "note": "   "}]}).status_code == 422
    findings_repo.set_verdict.assert_not_called()
    response = client.put(_VERDICTS, json={"verdicts": [{**wrong, "reason": "other", "note": "  Stamp is a watermark  "}]})
    assert response.status_code == 200
    assert findings_repo.set_verdict.call_args.args[3:] == ("other", "Stamp is a watermark")


@pytest.mark.parametrize("set_aside", [{"agent_decision": "dismissed"}, {"agent_decision": "accepted", "severity": "info"}])
def test_agreeing_with_the_agents_that_a_finding_is_set_aside_needs_no_reason(repos, set_aside) -> None:
    """Unticking a set-aside finding saves "wrong" (a false alarm, as the agents said): nothing to give a reason for."""
    findings_repo, _ = repos
    findings_repo.find_current.side_effect = lambda case_id, scope, document_id, fingerprint: {
        "id": f"row:{fingerprint}", "check_name": "spelling_tool", "summary": "A finding", **set_aside,
    }
    response = client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "wrong"}]})

    assert response.status_code == 200
    assert findings_repo.set_verdict.call_args.args[1:] == ("wrong", TEST_USER, None, None)


def test_a_false_alarm_on_a_finding_the_agents_kept_still_needs_a_reason(repos) -> None:
    findings_repo, _ = repos
    findings_repo.find_current.side_effect = lambda case_id, scope, document_id, fingerprint: {
        "id": f"row:{fingerprint}", "check_name": "spelling_tool", "summary": "A finding",
        "agent_decision": "accepted", "severity": "medium",
    }
    response = client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "wrong"}]})

    assert response.status_code == 422 and "needs a reason" in response.json()["detail"]
    findings_repo.set_verdict.assert_not_called()


def _set_aside_rows(findings_repo) -> None:
    findings_repo.find_current.side_effect = lambda case_id, scope, document_id, fingerprint: {
        "id": f"row:{fingerprint}", "check_name": "spelling_tool", "summary": "A finding", "agent_decision": "dismissed",
    }


def test_saying_a_set_aside_finding_is_real_needs_a_reason_from_its_own_list(repos) -> None:
    """Ticking a set-aside finding: the agents were wrong to set it aside, so it is real (correct), and why."""
    findings_repo, _ = repos
    _set_aside_rows(findings_repo)
    real = {**_VERDICT, "verdict": "correct"}

    # No reason, or one that only fits a false alarm: refused, nothing saved.
    assert client.put(_VERDICTS, json={"verdicts": [real]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**real, "reason": "tool_misread"}]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**real, "reason": "other"}]}).status_code == 422  # other needs a note
    findings_repo.set_verdict.assert_not_called()

    response = client.put(_VERDICTS, json={"verdicts": [{**real, "reason": "explanation_not_valid"}]})
    assert response.status_code == 200
    assert findings_repo.set_verdict.call_args.args[1:] == ("correct", TEST_USER, "explanation_not_valid", None)

    response = client.put(_VERDICTS, json={"verdicts": [{**real, "reason": "other", "note": "Same payee on three documents"}]})
    assert response.status_code == 200
    assert findings_repo.set_verdict.call_args.args[3:] == ("other", "Same payee on three documents")


def test_a_set_aside_finding_left_alone_takes_no_reason(repos) -> None:
    findings_repo, _ = repos
    _set_aside_rows(findings_repo)

    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "wrong", "reason": "tool_misread"}]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "wrong"}]}).status_code == 200


def test_a_kept_finding_found_real_takes_no_reason_and_a_set_aside_reason_does_not_fit_a_false_alarm(repos) -> None:
    findings_repo, _ = repos
    findings_repo.find_current.side_effect = lambda case_id, scope, document_id, fingerprint: {
        "id": f"row:{fingerprint}", "check_name": "spelling_tool", "summary": "A finding", "agent_decision": "accepted",
    }

    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "correct"}]}).status_code == 200
    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "correct", "reason": "explanation_not_valid"}]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "wrong", "reason": "explanation_not_valid"}]}).status_code == 422
    assert client.put(_VERDICTS, json={"verdicts": [{**_VERDICT, "verdict": "wrong", "reason": "tool_misread"}]}).status_code == 200


def test_a_group_mixing_both_saves_nothing_when_one_kept_finding_lacks_a_reason(repos) -> None:
    findings_repo, _ = repos
    findings_repo.find_current.side_effect = lambda case_id, scope, document_id, fingerprint: {
        "id": f"row:{fingerprint}", "check_name": "spelling_tool", "summary": "A finding",
        "agent_decision": "dismissed" if fingerprint == "set-aside:1" else "accepted",
    }
    response = client.put(_VERDICTS, json={"verdicts": [
        {**_VERDICT, "fingerprint": "set-aside:1", "verdict": "wrong"},
        {**_VERDICT, "fingerprint": "kept:1", "verdict": "wrong"},
    ]})

    assert response.status_code == 422
    findings_repo.set_verdict.assert_not_called()


def test_a_missed_issue_needs_a_note_and_is_saved_as_feedback(repos) -> None:
    findings_repo, _ = repos
    findings_repo.add_missed.return_value = "row-1"
    place = {"scope": "case", "check_name": "identity_entity_conflict"}
    assert client.post(f"/cases/{CASE_ID}/finding-reviews/missed", json=place).status_code == 400
    with patch("app.routers.finding_reviews.feedback_repository") as feedback_repo:
        feedback_repo.missed_key.side_effect = lambda row_id: f"m:{row_id}"
        assert client.post(f"/cases/{CASE_ID}/finding-reviews/missed", json={**place, "note": "NRIC differs"}).status_code == 201
    [saved] = [c.kwargs for c in feedback_repo.save.call_args_list]
    assert (saved["source_key"], saved["kind"], saved["note"]) == ("m:row-1", "missed", "NRIC differs")


def test_deleting_a_missed_issue_or_clearing_a_verdict_withdraws_its_feedback(repos) -> None:
    findings_repo, _ = repos
    with patch("app.routers.finding_reviews.feedback_repository") as feedback_repo:
        feedback_repo.missed_key.side_effect = lambda row_id: f"m:{row_id}"
        feedback_repo.verdict_key.side_effect = lambda case_id, document_id, fingerprint: f"v:{fingerprint}"
        findings_repo.get_review.return_value = _review(kind="missed", verdict=None, fingerprint=None)
        client.delete(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}")
        findings_repo.get_review.return_value = _review()
        client.delete(f"/cases/{CASE_ID}/finding-reviews/{REVIEW_ID}")
    assert [c.args[0] for c in feedback_repo.withdraw.call_args_list] == [
        f"m:{REVIEW_ID}", "v:numerical_checks_tool:PAY.NET:1",
    ]


def test_feedback_that_fails_to_save_never_fails_the_review(repos) -> None:
    with patch("app.routers.finding_reviews.feedback_repository") as feedback_repo:
        feedback_repo.verdict_key.return_value = "v:x"
        feedback_repo.save.side_effect = RuntimeError("db down")
        assert client.put(_VERDICTS, json={"verdicts": [_VERDICT]}).status_code == 200
