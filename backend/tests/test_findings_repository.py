"""
The officer's review record (findings_repository): verdicts, missed issues and sign-offs, written to an in-memory
findings table (tests/support/fake_supabase.py) and read back, so each test checks the stored rows. These rows are
what an officer decided, and what the Accuracy tab and the audit answer from.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import findings_repository
from app.supabase_rows import PAGE
from tests.support.fake_supabase import FakeSupabase

CASE, OTHER_CASE = "case-1", "case-2"
DOCUMENT, OTHER_DOCUMENT = "doc-1", "doc-2"
OFFICER = "demo"


@pytest.fixture
def db(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase(
        keys={"review_sign_offs": ("case_id", "group_key")},
        defaults={"findings": {"source": "detection", "summary": "", "evidence": {}, "verdict": None,
                               "reviewer": None, "review_note": None, "reviewed_at": None}},
    )
    fake.tables["cases"] = [
        {"id": CASE, "created_at": "2026-10-20T00:00:00Z", "status": "case_analysed"},
        {"id": OTHER_CASE, "created_at": "2026-10-28T00:00:00Z", "status": "finalised"},
    ]
    monkeypatch.setattr(findings_repository, "service_supabase_client", lambda: fake)
    return fake


def finding(db: FakeSupabase, *, case_id: str = CASE, document_id: str | None = DOCUMENT, scope: str = "document",
            fingerprint: str = "spelling_tool:1", check_name: str = "spelling_tool", **columns) -> str:
    """A detection finding as sync_findings stores it; returns its id."""
    return db.add("findings", {
        "case_id": case_id, "document_id": document_id, "scope": scope, "fingerprint": fingerprint,
        "check_name": check_name, "agent_decision": "accepted", "severity": "medium", **columns,
    })["id"]


# --- Verdicts ------------------------------------------------------------------------------------------------


def test_a_verdict_is_stored_with_its_officer_and_time(db) -> None:
    row_id = finding(db)

    findings_repository.set_verdict(row_id, "wrong", OFFICER)

    [row] = db.rows("findings", id=row_id)
    assert (row["verdict"], row["reviewer"]) == ("wrong", OFFICER)
    assert row["reviewed_at"] is not None
    [review] = findings_repository.list_reviews_for_case(CASE)
    assert (review.kind, review.verdict, review.reviewer, review.fingerprint) == ("verdict", "wrong", OFFICER, "spelling_tool:1")


def test_a_new_verdict_replaces_the_earlier_one(db) -> None:
    row_id = finding(db)
    findings_repository.set_verdict(row_id, "wrong", "first-officer")

    findings_repository.set_verdict(row_id, "correct", OFFICER)

    [review] = findings_repository.list_reviews_for_case(CASE)
    assert (review.verdict, review.reviewer) == ("correct", OFFICER)


def test_clearing_a_verdict_removes_the_review_and_its_note(db) -> None:
    row_id = finding(db, review_note="Checked the original.")
    findings_repository.set_verdict(row_id, "correct", OFFICER)

    findings_repository.set_verdict(row_id, None, OFFICER)

    [row] = db.rows("findings", id=row_id)
    assert (row["verdict"], row["reviewer"], row["review_note"], row["reviewed_at"]) == (None, None, None, None)
    assert findings_repository.list_reviews_for_case(CASE) == []


@pytest.mark.usefixtures("db")
def test_a_verdict_on_a_finding_that_is_gone_raises() -> None:
    with pytest.raises(RuntimeError, match="was not updated"):
        findings_repository.set_verdict("no-such-finding", "correct", OFFICER)


def test_unreviewed_findings_are_not_reviews(db) -> None:
    finding(db)

    assert findings_repository.list_reviews_for_case(CASE) == []
    assert findings_repository.get_review(db.rows("findings")[0]["id"]) is None


# --- Missed issues -------------------------------------------------------------------------------------------


def test_a_missed_issue_is_stored_as_the_officers_own_row(db) -> None:
    findings_repository.add_missed(
        case_id=CASE, scope="document", document_id=DOCUMENT, check_name="font_consistency_tool",
        note="Net pay is in a different font.", reviewer=OFFICER,
    )

    [row] = db.rows("findings", source="officer")
    assert (row["check_name"], row["review_note"], row.get("fingerprint")) == (
        "font_consistency_tool", "Net pay is in a different font.", None)
    [review] = findings_repository.list_reviews_for_case(CASE)
    assert (review.kind, review.note, review.document_id) == ("missed", "Net pay is in a different font.", DOCUMENT)


def test_editing_a_missed_issue_changes_its_check_note_and_reviewer(db) -> None:
    findings_repository.add_missed(case_id=CASE, scope="case", document_id=None, check_name="other",
                                   note="Director differs.", reviewer="first-officer")
    [row] = db.rows("findings", source="officer")

    findings_repository.update_missed(row["id"], check_name="identity_entity_conflict", note=None, reviewer=OFFICER)

    [review] = findings_repository.list_reviews_for_case(CASE)
    assert (review.check_name, review.note, review.reviewer) == ("identity_entity_conflict", None, OFFICER)


def test_only_an_officers_row_can_be_edited_or_deleted_as_a_missed_issue(db) -> None:
    detection_row = finding(db)

    with pytest.raises(RuntimeError, match="was not updated"):
        findings_repository.update_missed(detection_row, check_name="other", note="x", reviewer=OFFICER)
    findings_repository.delete_missed(detection_row)

    [row] = db.rows("findings", id=detection_row)
    assert row["check_name"] == "spelling_tool"


def test_deleting_a_missed_issue_removes_it(db) -> None:
    findings_repository.add_missed(case_id=CASE, scope="document", document_id=DOCUMENT, check_name="other",
                                   note="Seen by eye.", reviewer=OFFICER)
    [row] = db.rows("findings", source="officer")

    findings_repository.delete_missed(row["id"])

    assert db.rows("findings") == []


def test_a_cases_reviews_read_in_the_order_they_were_made(db) -> None:
    later = finding(db, fingerprint="spelling_tool:2")
    earlier = finding(db, fingerprint="spelling_tool:1")
    findings_repository.set_verdict(earlier, "correct", OFFICER)
    findings_repository.set_verdict(later, "wrong", OFFICER)
    db.rows("findings", id=earlier)[0]["reviewed_at"] = "2026-10-21T01:00:00+00:00"
    db.rows("findings", id=later)[0]["reviewed_at"] = "2026-10-21T02:00:00+00:00"

    assert [r.fingerprint for r in findings_repository.list_reviews_for_case(CASE)] == ["spelling_tool:1", "spelling_tool:2"]


# --- Sign-offs -----------------------------------------------------------------------------------------------


def test_signing_off_a_group_with_no_findings_is_kept_once_per_group(db) -> None:
    findings_repository.sign_off(CASE, "document", DOCUMENT, "first-officer")
    findings_repository.sign_off(CASE, "document", DOCUMENT, OFFICER)
    findings_repository.sign_off(CASE, "case", None, OFFICER)

    sign_offs = findings_repository.list_sign_offs(CASE)

    assert sorted((s.scope, s.document_id, s.reviewer) for s in sign_offs) == [
        ("case", None, OFFICER), ("document", DOCUMENT, OFFICER),
    ]
    assert {row["group_key"] for row in db.rows("review_sign_offs")} == {DOCUMENT, "case"}
    assert findings_repository.list_sign_offs(OTHER_CASE) == []


# --- The Accuracy tab's reviews --------------------------------------------------------------------------------


def reviewed(db: FakeSupabase, case_id: str, verdict: str = "correct") -> None:
    findings_repository.set_verdict(finding(db, case_id=case_id), verdict, OFFICER)


def test_with_no_filter_every_review_is_read(db) -> None:
    reviewed(db, CASE)
    reviewed(db, OTHER_CASE)
    finding(db)  # not reviewed

    assert sorted(r.case_id for r in findings_repository.list_reviews()) == [CASE, OTHER_CASE]


def test_the_date_filter_keeps_reviews_on_cases_created_in_the_range(db) -> None:
    reviewed(db, CASE)  # created 20 Oct
    reviewed(db, OTHER_CASE)  # created 28 Oct

    since = findings_repository.list_reviews(since=datetime(2026, 10, 25, tzinfo=timezone.utc))
    before = findings_repository.list_reviews(before=datetime(2026, 10, 25, tzinfo=timezone.utc))

    assert [r.case_id for r in since] == [OTHER_CASE]
    assert [r.case_id for r in before] == [CASE]


def test_a_date_without_a_zone_is_taken_as_utc(db) -> None:
    reviewed(db, OTHER_CASE)  # created 28 Oct 00:00 UTC

    assert findings_repository.list_reviews(since=datetime(2026, 10, 28)) != []
    assert findings_repository.list_reviews(since=datetime(2026, 10, 28) + timedelta(seconds=1)) == []


def test_the_stage_filter_keeps_reviews_on_cases_at_those_stages(db) -> None:
    reviewed(db, CASE)
    reviewed(db, OTHER_CASE)

    assert [r.case_id for r in findings_repository.list_reviews(stages=["finalised"])] == [OTHER_CASE]
    assert findings_repository.list_reviews(stages=["open"]) == []


def test_every_review_is_read_past_a_page(db) -> None:
    for n in range(PAGE + 1):
        db.add("findings", {"case_id": CASE, "document_id": DOCUMENT, "scope": "document", "fingerprint": f"f:{n}",
                            "check_name": "spelling_tool", "agent_decision": "accepted", "verdict": "correct",
                            "reviewer": OFFICER, "reviewed_at": "2026-10-21T01:00:00+00:00"})

    assert len(findings_repository.list_reviews()) == PAGE + 1
    assert len(findings_repository.list_reviews(stages=["case_analysed"])) == PAGE + 1


# --- Keeping verdicts on the right finding ---------------------------------------------------------------------


def test_the_current_finding_is_found_by_its_fingerprint_in_its_group(db) -> None:
    on_document = finding(db, fingerprint="f:1")
    on_case = finding(db, document_id=None, scope="case", fingerprint="f:1", check_name="cross_document_variance")

    assert findings_repository.find_current(CASE, "document", DOCUMENT, "f:1")["id"] == on_document
    assert findings_repository.find_current(CASE, "case", None, "f:1")["id"] == on_case
    assert findings_repository.find_current(CASE, "document", OTHER_DOCUMENT, "f:1") is None
    assert findings_repository.find_current(CASE, "document", DOCUMENT, "f:2") is None  # a later run replaced it


def test_moved_documents_take_their_findings_and_missed_issues_to_the_new_case(db) -> None:
    moved = finding(db, document_id=DOCUMENT)
    stays = finding(db, document_id=OTHER_DOCUMENT)
    findings_repository.add_missed(case_id=CASE, scope="document", document_id=DOCUMENT, check_name="other",
                                   note=None, reviewer=OFFICER)

    findings_repository.move_with_documents([DOCUMENT], OTHER_CASE)

    assert {row["case_id"] for row in db.rows("findings", document_id=DOCUMENT)} == {OTHER_CASE}
    assert db.rows("findings", id=stays)[0]["case_id"] == CASE
    assert db.rows("findings", id=moved)[0]["case_id"] == OTHER_CASE


def test_deleting_a_document_removes_the_case_findings_citing_it(db) -> None:
    finding(db, document_id=None, scope="case", check_name="cross_document_variance",
                     evidence={"document_ids": [DOCUMENT, OTHER_DOCUMENT]})
    other = finding(db, document_id=None, scope="case", check_name="cross_document_variance",
                    evidence={"document_ids": [OTHER_DOCUMENT]})
    own = finding(db, document_id=DOCUMENT)  # removed with its document by the foreign key, not here

    findings_repository.delete_case_findings_citing({CASE: [DOCUMENT]})

    assert {row["id"] for row in db.rows("findings")} == {other, own}


# --- Writing a run's findings ------------------------------------------------------------------------------------


def test_a_reports_kept_and_dismissed_findings_are_written_in_one_call(db) -> None:
    db.functions["sync_findings"] = lambda params: len(params["p_rows"])
    report = {
        "findings": [{
            "id": "font-1", "tool": "font_consistency_tool", "fingerprint": "font:1", "summary": "Net pay restyled",
            "severity": "high", "rationale": "Rated high: one field.", "ai_accepted": True, "confidence": 0.82,
            "specialists": ["layout_rendering"], "check_code": "FONT.SIZE", "page": 1,
        }],
        "consulted": [{"dismissed": [{
            "id": "meta-1", "tool": "metadata_tool", "fingerprint": "meta:1", "summary": "Producer is Word",
            "severity": "low", "reason": "Common for payslips.",
        }]}],
    }

    assert findings_repository.sync_document(DOCUMENT, CASE, report) == 2

    [(name, params)] = db.rpc_calls
    assert (name, params["p_case_id"], params["p_document_id"]) == ("sync_findings", CASE, DOCUMENT)
    kept, dismissed = params["p_rows"]
    assert (kept["agent_decision"], kept["severity"], kept["rationale"], kept["confidence"]) == (
        "accepted", "high", "Rated high: one field.", 0.82)
    assert kept["evidence"]["details"] == {"page": 1}
    # A dismissed finding has no specialist severity, and its reason is the rationale kept.
    assert (dismissed["agent_decision"], dismissed["severity"], dismissed["rationale"]) == (
        "dismissed", None, "Common for payslips.")


def test_an_out_of_range_confidence_is_not_stored(db) -> None:
    db.functions["sync_findings"] = lambda params: len(params["p_rows"])
    report = {"findings": [{"tool": "spelling_tool", "fingerprint": "s:1", "confidence": 1.4, "ai_accepted": True}]}

    findings_repository.sync_document(DOCUMENT, CASE, report)

    [(_, params)] = db.rpc_calls
    assert params["p_rows"][0]["confidence"] is None


# --- The case list's flags -------------------------------------------------------------------------------------


def test_the_case_list_counts_only_accepted_document_findings(db) -> None:
    counted = finding(db, category="tampering_forensic")
    finding(db, agent_decision="dismissed", category="tampering_forensic")
    finding(db, category="cross_document_variance")  # case-level: the case analysis covers it
    findings_repository.add_missed(case_id=CASE, scope="document", document_id=DOCUMENT, check_name="other",
                                   note=None, reviewer=OFFICER)

    assert [row["id"] for row in findings_repository.list_flags_for_cases([CASE])] == [counted]


def test_the_case_list_reads_flags_for_more_than_a_hundred_cases(db) -> None:
    case_ids = [f"case-{n}" for n in range(150)]
    for case_id in case_ids:
        finding(db, case_id=case_id, category="tampering_forensic")

    assert len(findings_repository.list_flags_for_cases(case_ids)) == 150
