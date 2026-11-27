"""
The case_analysis_runs table (case_pipeline/repository.py), on an in-memory table (tests/support/fake_supabase.py):
the writes that must only happen in one state, which the stuck-run sweeper and a duplicate analysis request rely on.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.case_pipeline import repository
from app.case_pipeline.models import CaseAnalysis, CrossDocFinding, FindingCategory
from app.models import CaseRisk
from tests.support.fake_supabase import FakeSupabase, Query

CASE = "case-1"
NOW = datetime(2026, 11, 5, 4, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase(keys={"case_analysis_runs": ("run_id",)})
    monkeypatch.setattr(repository, "supabase_client", fake)
    return fake


def run(db: FakeSupabase, run_id: str, *, status: str = "running", minutes_ago: int = 0, case_id: str = CASE) -> None:
    db.add("case_analysis_runs", {
        "run_id": run_id, "case_id": case_id, "status": status, "fingerprint": "f",
        "started_at": (NOW - timedelta(minutes=minutes_ago)).isoformat(),
    })


def finished(run_id: str, *, score: int = 50) -> CaseAnalysis:
    return CaseAnalysis(
        run_id=run_id, case_id=CASE, status="complete", fingerprint="f", started_at=NOW,
        risk=CaseRisk(state="scored", level="High", score=score),
    )


def stored(db: FakeSupabase, run_id: str) -> dict:
    [row] = db.rows("case_analysis_runs", run_id=run_id)
    return row


def test_a_new_run_is_stored_as_running(db) -> None:
    repository.insert_run("run-1", CASE, "fingerprint-1")

    row = stored(db, "run-1")
    assert (row["status"], row["case_id"], row["fingerprint"]) == ("running", CASE, "fingerprint-1")


def test_failing_a_run_only_takes_effect_while_it_is_running(db) -> None:
    run(db, "run-1")
    run(db, "run-2", status="complete")

    assert repository.fail_if_running("run-1", "Timed out") is True
    assert repository.fail_if_running("run-1", "Timed out again") is False  # already failed
    assert repository.fail_if_running("run-2", "Timed out") is False

    assert (stored(db, "run-1")["status"], stored(db, "run-1")["error"]) == ("failed", "Timed out")
    assert stored(db, "run-2")["status"] == "complete"


def test_the_newest_run_finishing_writes_its_result(db) -> None:
    run(db, "run-1")

    assert repository.finish_run(finished("run-1", score=58)) is True

    row = stored(db, "run-1")
    assert row["status"] == "complete"
    assert row["result"]["risk"]["score"] == 58
    assert repository.get_latest(CASE).risk.score == 58


def test_a_run_replaced_while_it_ran_is_superseded_and_its_result_dropped(db) -> None:
    run(db, "run-1", minutes_ago=5)
    run(db, "run-2")  # a newer request started meanwhile

    assert repository.finish_run(finished("run-1")) is False

    assert (stored(db, "run-1")["status"], stored(db, "run-1")["result"]) == ("superseded", {})
    assert repository.get_latest(CASE).run_id == "run-2"


def test_only_runs_still_running_from_before_the_cutoff_are_stuck(db) -> None:
    run(db, "stuck", minutes_ago=30)
    run(db, "recent", minutes_ago=2)
    run(db, "done", status="complete", minutes_ago=30)

    stuck = repository.running_started_before(NOW - timedelta(minutes=10))

    assert [(run_id, case_id) for run_id, case_id, _ in stuck] == [("stuck", CASE)]
    assert stuck[0][2] == NOW - timedelta(minutes=30)


def test_the_latest_run_state_is_the_newest_runs(db) -> None:
    run(db, "old", status="complete", minutes_ago=20)
    run(db, "new", minutes_ago=1)

    assert repository.latest_run_state(CASE) == ("running", NOW - timedelta(minutes=1))
    assert repository.latest_run_id(CASE) == "new"
    assert repository.latest_run_state("case-without-runs") is None


def test_the_latest_complete_run_is_read_while_a_newer_one_runs(db) -> None:
    run(db, "done", minutes_ago=20)
    repository.finish_run(finished("done", score=40))
    run(db, "running", minutes_ago=1)

    latest = repository.get_latest_complete(CASE)

    assert (latest.run_id, latest.risk.score) == ("done", 40)
    assert repository.get_latest_summary(CASE).run_id == "running"


# --- A failed run stays failed ---------------------------------------------------------------

def test_a_run_the_sweeper_already_failed_is_not_overwritten_by_its_late_result(db) -> None:
    run(db, "run-1")
    repository.fail_if_running("run-1", "Timed out")

    assert repository.finish_run(finished("run-1", score=90)) is False

    row = stored(db, "run-1")
    assert (row["status"], row["error"]) == ("failed", "Timed out")
    assert not row.get("result")


def test_a_run_inserted_between_the_check_and_the_write_supersedes_the_result(db, monkeypatch) -> None:
    run(db, "run-1", minutes_ago=5)
    real_latest = repository.latest_run_id
    reads = iter(["run-1"])  # the first read sees run-1 as newest; run-2 lands before the second

    def latest_run_id(case_id: str) -> str | None:
        answer = next(reads, None)
        if answer is not None:
            run(db, "run-2")
            return answer
        return real_latest(case_id)

    monkeypatch.setattr(repository, "latest_run_id", latest_run_id)

    assert repository.finish_run(finished("run-1")) is False

    assert (stored(db, "run-1")["status"], stored(db, "run-1")["result"]) == ("superseded", {})
    assert stored(db, "run-2")["status"] == "running"


def test_a_published_result_carries_its_finding_count(db) -> None:
    run(db, "run-1")
    analysis = finished("run-1")
    analysis.findings = [
        CrossDocFinding(id=f"f{i}", hypothesis_id="h1", summary="Addresses differ", rationale="r", severity="medium",
                        category=FindingCategory.CROSS_DOCUMENT_VARIANCE)
        for i in range(2)
    ]

    assert repository.finish_run(analysis) is True

    assert stored(db, "run-1")["result"]["finding_count"] == 2


# --- Counts past 1,000 rows and the runs list ------------------------------------------

class _Capped(Query):
    def execute(self):
        result = super().execute()
        if isinstance(result.data, list):
            result.data = result.data[:1000]  # PostgREST's cap on one read
        return result


class _CappedSupabase(FakeSupabase):
    def table(self, name: str) -> Query:
        return _Capped(self, name)


@pytest.fixture
def big_db(monkeypatch) -> FakeSupabase:
    fake = _CappedSupabase(keys={"case_analysis_runs": ("run_id",)})
    monkeypatch.setattr(repository, "supabase_client", fake)
    return fake


def test_runs_since_counts_past_the_thousand_row_cap(big_db) -> None:
    for i in range(1200):
        run(big_db, f"run-{i:04d}", status="complete")

    assert repository.count_runs_since(CASE, NOW - timedelta(hours=1)) == 1200


def test_stuck_runs_are_all_found_past_the_thousand_row_cap(big_db) -> None:
    for i in range(1200):
        run(big_db, f"run-{i:04d}", minutes_ago=30)

    assert len(repository.running_started_before(NOW - timedelta(minutes=10))) == 1200


def test_the_runs_list_pages_past_the_thousand_row_cap(big_db) -> None:
    for i in range(1200):
        run(big_db, f"run-{i:04d}", status="failed", minutes_ago=i)

    runs = repository.list_runs(CASE)

    assert len(runs) == 1200
    assert runs[0]["run_id"] == "run-0000" and runs[-1]["run_id"] == "run-1199"


def test_the_runs_list_reads_the_stored_finding_count(db) -> None:
    run(db, "run-1", status="complete")
    db.rows("case_analysis_runs")[0]["result"] = {"finding_count": 4, "findings": []}

    [listed] = repository.list_runs(CASE)

    assert listed["finding_count"] == 4


def test_a_complete_run_stored_before_the_count_is_counted_from_its_findings(db, monkeypatch) -> None:
    run(db, "old", status="complete", minutes_ago=10)
    run(db, "new", status="complete")
    run(db, "failed", status="failed", minutes_ago=5)
    old, new, _ = db.rows("case_analysis_runs")
    old["result"] = {"findings": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}
    new["result"] = {"finding_count": 1, "findings": [{"id": "a"}]}
    reads: list[str] = []
    real_in = Query.in_

    def counting_in(self, column, values):
        reads.append(column)
        return real_in(self, column, values)

    monkeypatch.setattr(Query, "in_", counting_in)
    listed = {r["run_id"]: r["finding_count"] for r in repository.list_runs(CASE)}

    assert listed == {"old": 3, "new": 1, "failed": None}
    assert reads == ["run_id"]  # one extra read, for the uncounted run only
