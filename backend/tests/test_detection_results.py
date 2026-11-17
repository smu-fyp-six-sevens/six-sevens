"""
The detection_results table (detection_results.py), on an in-memory table (tests/support/fake_supabase.py): one row
per document, where a report always wins over a later error, and a new run clears the last error.
"""
from __future__ import annotations

import pytest

from app import detection_results
from app.langgraph import detection_report
from tests.support.fake_supabase import FakeSupabase

REPORT = {"verdict": "flagged", "summary": "Net pay restyled.", "findings": []}
# Every test runs against the in-memory table.
pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def db(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase(keys={"detection_results": ("document_id",)})
    monkeypatch.setattr(detection_results, "service_supabase_client", lambda: fake)
    return fake


def test_a_saved_report_is_read_back_with_no_error() -> None:
    detection_results.save_report("doc-1", REPORT)

    report, error = detection_results.get("doc-1")

    assert (report["verdict"], report["summary"], error) == ("flagged", "Net pay restyled.", None)
    assert detection_results.has_report("doc-1")


def test_an_error_after_a_report_keeps_the_report(db) -> None:
    detection_results.save_report("doc-1", REPORT)

    detection_results.save_error("doc-1", "Cancelled by loan officer")

    report, error = detection_results.get("doc-1")
    assert (report["verdict"], error) == ("flagged", "Cancelled by loan officer")
    assert len(db.rows("detection_results")) == 1


def test_starting_a_run_clears_the_last_error_but_not_the_report() -> None:
    detection_results.save_report("doc-1", REPORT)
    detection_results.save_error("doc-1", "Timed out")

    detection_results.mark_started("doc-1")

    report, error = detection_results.get("doc-1")
    assert (report["verdict"], error) == ("flagged", None)


def test_a_document_never_scanned_has_neither() -> None:
    assert detection_results.get("doc-1") == (None, None)
    assert not detection_results.has_report("doc-1")


def test_states_split_reports_from_documents_whose_run_failed_with_none() -> None:
    detection_results.save_report("scanned", REPORT)
    detection_results.save_error("failed", "Timed out")
    detection_results.save_report("failed-after-report", REPORT)
    detection_results.save_error("failed-after-report", "Timed out")

    reports, failed = detection_results.get_states(["scanned", "failed", "failed-after-report", "never"])

    assert set(reports) == {"scanned", "failed-after-report"}
    assert failed == {"failed"}


def test_slim_states_carry_only_the_verdict_and_counts_for_more_than_a_hundred_documents() -> None:
    ids = [f"doc-{n}" for n in range(150)]
    for document_id in ids:
        detection_results.save_report(document_id, REPORT)

    reports, failed = detection_results.get_states(ids, slim=True)

    assert len(reports) == 150
    assert reports["doc-149"] == {
        "verdict": "flagged", "severity_counts": detection_report.severity_counts(REPORT.get("findings")),
    }
    assert failed == set()


def test_a_report_stored_before_the_counts_reads_back_with_the_verdict_only(db) -> None:
    detection_results.save_report("old", REPORT)
    [row] = db.rows("detection_results", document_id="old")
    row["report"].pop("severity_counts")

    reports, _ = detection_results.get_states(["old"], slim=True)

    assert reports["old"] == {"verdict": "flagged"}
