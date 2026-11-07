"""
The risk_scores history (case_pipeline/risk/store.py), on an in-memory table (tests/support/fake_supabase.py): what a
finished analysis saves, the rank the case list sorts by, and the latest score per case the dashboard reads.
"""
from __future__ import annotations

import pytest

from app.case_pipeline.risk import store
from app.models import CaseRisk
from tests.support.fake_supabase import FakeSupabase


class Ranks:
    """case_repository.set_risk_rank, recorded."""

    def __init__(self) -> None:
        self.by_case: dict[str, int] = {}

    def set_risk_rank(self, case_id: str, rank: int) -> None:
        self.by_case[case_id] = rank


@pytest.fixture
def db(monkeypatch) -> FakeSupabase:
    fake = FakeSupabase()
    monkeypatch.setattr(store, "supabase_client", fake)
    return fake


@pytest.fixture
def ranks(monkeypatch) -> Ranks:
    recorded = Ranks()
    monkeypatch.setattr(store, "case_repository", recorded)
    return recorded


def latest_view(db: FakeSupabase) -> None:
    """latest_risk_scores: each case's newest row, as the database view selects it."""
    newest: dict[str, dict] = {}
    for row in db.rows("risk_scores"):
        newest[row["case_id"]] = row
    db.tables["latest_risk_scores"] = list(newest.values())


def test_a_scored_risk_is_saved_with_its_rank(db, ranks) -> None:
    store.save("case-1", CaseRisk(state="scored", level="High", score=58, reasons=["Two high findings."]))

    [row] = db.rows("risk_scores")
    assert (row["overall_score"], row["risk_level"], row["reasoning"]) == (58, "High", "Two high findings.")
    assert ranks.by_case == {"case-1": 3}


def test_an_unscored_risk_is_saved_under_its_states_label_and_ranks_below_low(db, ranks) -> None:
    store.save("case-1", CaseRisk(state="failed", reasons=[]))

    [row] = db.rows("risk_scores")
    assert (row["overall_score"], row["risk_level"], row["reasoning"]) == (0, "Failed", None)
    assert ranks.by_case == {"case-1": 0}


def test_no_risk_saves_nothing(db, ranks) -> None:
    store.save("case-1", None)

    assert db.rows("risk_scores") == []
    assert ranks.by_case == {}


@pytest.mark.usefixtures("db")
def test_a_failed_save_is_logged_not_raised(ranks, monkeypatch, caplog) -> None:
    def refuse(case_id: str, rank: int) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(ranks, "set_risk_rank", refuse)

    store.save("case-1", CaseRisk(state="scored", level="Low", score=5))

    assert "Failed to save the risk score of case case-1" in caplog.text


@pytest.mark.usefixtures("ranks")
def test_the_latest_risk_of_each_case_is_read_back_as_saved(db) -> None:
    store.save("case-1", CaseRisk(state="scored", level="Medium", score=25))
    store.save("case-1", CaseRisk(state="scored", level="Critical", score=89, reasons=["Highs on 5 documents."]))
    store.save("case-2", CaseRisk(state="pending"))
    latest_view(db)

    latest = store.latest_many(["case-1", "case-2", "case-3"])

    assert set(latest) == {"case-1", "case-2"}
    assert (latest["case-1"].level, latest["case-1"].score, latest["case-1"].reasons) == (
        "Critical", 89, ["Highs on 5 documents."])
    assert (latest["case-2"].state, latest["case-2"].level, latest["case-2"].score) == ("pending", None, None)


@pytest.mark.usefixtures("ranks")
def test_the_latest_risks_are_read_for_more_than_a_hundred_cases(db) -> None:
    case_ids = [f"case-{n}" for n in range(150)]
    for case_id in case_ids:
        store.save(case_id, CaseRisk(state="scored", level="Low", score=10))
    latest_view(db)

    assert len(store.latest_many(case_ids)) == 150
