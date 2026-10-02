"""
Deterministic classification confidence (app/langgraph/classification_confidence.py):
the agent picks the type, these rules turn the sources it consulted into the
recorded confidence.
"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.langgraph.classification_confidence import (
    AGREEMENT_CAP,
    CERTAIN_CAP,
    DISAGREEMENT_CAP,
    UNCORROBORATED_CAP,
    compute_classification_confidence,
    tool_results,
)


def _result(*candidates: tuple[str, float], certain_type: str | None = None) -> dict:
    return {
        "top_candidates": [{"document_type": t, "match_strength": s} for t, s in candidates],
        "certain_type": certain_type,
    }


def test_same_inputs_always_give_the_same_confidence():
    rule = _result(("payslip", 0.9))
    tools = {"llm": _result(("payslip", 0.8))}
    first = compute_classification_confidence("payslip", rule, tools)
    assert all(compute_classification_confidence("payslip", rule, tools) == first for _ in range(5))


def test_agreeing_sources_average_their_strength():
    out = compute_classification_confidence(
        "payslip", _result(("payslip", 0.9)), {"llm": _result(("payslip", 0.8))}
    )
    assert out["confidence"] == pytest.approx(0.85)
    assert out["basis"] == "sources_agree"
    assert out["needs_manual_review"] is False


def test_agreeing_sources_are_capped():
    out = compute_classification_confidence(
        "payslip", _result(("payslip", 1.0)), {"llm": _result(("payslip", 1.0)), "vision": _result(("payslip", 1.0))}
    )
    assert out["confidence"] == AGREEMENT_CAP


def test_a_certain_rule_match_scores_the_certain_cap():
    out = compute_classification_confidence("passport", _result(("nric", 0.4), certain_type="passport"), {})
    assert out["confidence"] == CERTAIN_CAP
    assert out["basis"] == "certain_match"
    assert out["needs_manual_review"] is False


def test_a_lone_uncorroborated_source_is_capped():
    out = compute_classification_confidence("bank_statement", _result(("bank_statement", 1.0)), {})
    assert out["confidence"] == UNCORROBORATED_CAP
    assert out["basis"] == "uncorroborated"
    assert out["needs_manual_review"] is False


def test_a_lone_source_with_a_close_runner_up_goes_to_manual_review():
    out = compute_classification_confidence(
        "nric", _result(("nric", 0.8), ("passport", 0.75)), {}
    )
    assert out["confidence"] == DISAGREEMENT_CAP
    assert out["basis"] == "uncorroborated_close"
    assert out["needs_manual_review"] is True


def test_disagreeing_sources_go_to_manual_review():
    out = compute_classification_confidence(
        "payslip", _result(("payslip", 0.9)), {"llm": _result(("bank_statement", 0.9))}
    )
    assert out["confidence"] == DISAGREEMENT_CAP
    assert out["basis"] == "sources_disagree"
    assert out["needs_manual_review"] is True


def test_a_type_no_source_picked_scores_zero():
    out = compute_classification_confidence(
        "credit_report", _result(("payslip", 0.9)), {"llm": _result(("bank_statement", 0.9))}
    )
    assert out["confidence"] == 0.0
    assert out["needs_manual_review"] is True


@pytest.mark.parametrize("document_type", [None, "", "unrecognised"])
def test_an_unrecognised_type_scores_zero(document_type):
    out = compute_classification_confidence(document_type, _result(("payslip", 0.9)), {})
    assert out["confidence"] == 0.0
    assert out["needs_manual_review"] is True


def test_no_usable_source_scores_zero():
    out = compute_classification_confidence("payslip", {"top_candidates": []}, {"llm": {"top_candidates": []}})
    assert out["confidence"] == 0.0
    assert out["basis"] == "no_usable_source"


def test_tool_results_reads_called_tools_and_skips_errors():
    messages = [
        AIMessage(content="", tool_calls=[{"name": "llm_classify_tool", "args": {}, "id": "c1"}]),
        ToolMessage(content=json.dumps(_result(("payslip", 0.9))), name="llm_classify_tool", tool_call_id="c1"),
        ToolMessage(content="Error: boom", name="vision_llm", tool_call_id="c2", status="error"),
    ]
    assert tool_results(messages) == {"llm": _result(("payslip", 0.9))}


def test_tool_results_latest_call_wins():
    messages = [
        ToolMessage(content=json.dumps(_result(("payslip", 0.5))), name="vision_llm", tool_call_id="c1"),
        ToolMessage(content=json.dumps(_result(("nric", 0.9))), name="vision_llm", tool_call_id="c2"),
    ]
    assert tool_results(messages)["vision"] == _result(("nric", 0.9))
