"""
Regression tests (SCRUM-206) for SCRUM-109 Classifier Agent Tools to Return
Top 3 Results (Sprint 2): each classifier tool returns up to 3 candidate
types with reasoning, and their confidences sum to 1 whenever there is at least
one candidate. No candidates is an empty list; the document is then routed to
manual review as unrecognised rather than given an invented confidence.

The normalisation is tested directly with fixed candidates, because a real
document does not reliably produce exactly three matches.
"""
from __future__ import annotations

import pytest

from app.langgraph import llm_classify, rule_classify


def _candidate(document_type: str, match_strength: float) -> dict:
    return {
        "document_type": document_type,
        "match_strength": match_strength,
        "evidence": [f"{document_type} wording"],
        "reasoning": f"Matches {document_type} layout",
    }


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-109", "DI-others")
@pytest.mark.parametrize("normalise", [rule_classify._normalise, llm_classify._normalise], ids=["rules", "llm"])
def test_three_candidates_are_ranked_with_reasoning_and_sum_to_one(normalise) -> None:
    result = normalise([_candidate("payslip", 0.6), _candidate("bank_statement", 0.3), _candidate("credit_report", 0.1)])

    assert [c["rank"] for c in result] == [1, 2, 3]
    assert [c["document_type"] for c in result] == ["payslip", "bank_statement", "credit_report"]
    assert all(c["reasoning"] for c in result)
    assert sum(c["confidence"] for c in result) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-109", "DI-others")
@pytest.mark.parametrize("normalise", [rule_classify._normalise, llm_classify._normalise], ids=["rules", "llm"])
def test_rounding_never_leaves_the_confidences_short_of_one(normalise) -> None:
    result = normalise([_candidate("payslip", 1.0), _candidate("bank_statement", 1.0), _candidate("credit_report", 1.0)])

    assert sum(c["confidence"] for c in result) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-109", "DI-others")
@pytest.mark.parametrize("normalise", [rule_classify._normalise, llm_classify._normalise], ids=["rules", "llm"])
def test_fewer_than_three_candidates_still_sum_to_one(normalise) -> None:
    result = normalise([_candidate("payslip", 0.7), _candidate("bank_statement", 0.3)])

    assert len(result) == 2
    assert sum(c["confidence"] for c in result) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-109", "DI-others")
def test_a_document_that_matches_nothing_returns_no_candidates_and_needs_review() -> None:
    # Nothing to normalise: the sum-to-1 rule applies only when candidates exist.
    result = rule_classify.classify_by_rules("scan_0042.pdf", "Lorem ipsum dolor sit amet")

    assert result["top_candidates"] == []
    assert result["needs_manual_review"] is True


@pytest.mark.sprint2
@pytest.mark.story("SCRUM-109", "DI-others")
def test_a_real_match_returns_at_most_three_candidates_summing_to_one() -> None:
    result = rule_classify.classify_by_rules(
        "C001-P2_Payslip.pdf", "PAYSLIP Employee Net Pay 5,200.00 Employer CPF Contribution"
    )

    candidates = result["top_candidates"]
    assert 1 <= len(candidates) <= 3
    assert all(c["reasoning"] for c in candidates)
    assert round(sum(c["confidence"] for c in candidates), 4) == 1.0
