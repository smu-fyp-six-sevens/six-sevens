"""
Tests for the langgraph experiment (backend/app/langgraph, phases 0-3).

Split in two tiers:
- Pure-logic tests (rule_classify, llm_classify parsing, classify_support,
  router) have no dependency on the langgraph/langchain-anthropic packages
  and always run.
- Graph-shape/agent tests need `langgraph` (and, for the fake-model smoke
  test, `langchain-anthropic`/`langchain-core`) installed — see
  requirements.txt. They're skipped, not failed, until those are installed
  (`pip install -r requirements.txt`), matching this repo's CI/CD stance of
  no network calls in tests.
"""

from __future__ import annotations

import pytest

from app.langgraph import classify_support, router
from app.langgraph.llm_classify import parse_llm_classification
from app.langgraph.rule_classify import classify_by_rules


# --- rule_classify -----------------------------------------------------

def test_rule_classify_filename_and_text_agree_boosts_strength():
    result = classify_by_rules(
        "bank_statement_jan.pdf",
        "This is a Bank Statement showing opening balance and closing balance.",
    )
    top = result["top_candidates"][0]
    assert top["document_type"] == "bank_statement"
    assert top["match_strength"] > 0.9
    assert result["needs_manual_review"] is False


def test_rule_classify_no_match_returns_empty_candidates():
    result = classify_by_rules("scan001.pdf", "")
    assert result["top_candidates"] == []
    assert result["needs_manual_review"] is True
    assert "No filename or text rule matched" in result["notes"][0]


def test_rule_classify_residential_address_note():
    result = classify_by_rules(
        "nric.pdf",
        "IDENTITY CARD NO. S1234567A residential address blk 123 singapore 123456",
    )
    assert any("residential address" in note for note in result["notes"])


# --- llm_classify parsing ------------------------------------------------

def test_parse_llm_classification_happy_path():
    raw = (
        '{"top_candidates": [{"document_type": "payslip", "match_strength": 0.9, '
        '"evidence": ["net pay"], "reasoning": "clear payslip"}]}'
    )
    result = parse_llm_classification(raw)
    assert result["top_candidates"][0]["document_type"] == "payslip"
    assert result["top_candidates"][0]["confidence"] == 1.0


def test_parse_llm_classification_drops_unknown_types_and_dedupes():
    raw = (
        '{"top_candidates": ['
        '{"document_type": "id_document", "match_strength": 0.9}, '
        '{"document_type": "nric", "match_strength": 0.8}, '
        '{"document_type": "nric", "match_strength": 0.5}'
        "]}"
    )
    result = parse_llm_classification(raw)
    types = [c["document_type"] for c in result["top_candidates"]]
    assert types == ["nric"]
    assert any("Dropped unknown category" in note for note in result["notes"])


def test_parse_llm_classification_invalid_json_never_raises():
    result = parse_llm_classification("not json at all")
    assert result["top_candidates"] == []
    assert "could not be parsed" in result["notes"][0]


# --- classify_support ----------------------------------------------------

def test_address_check_visible():
    rule_result = {"notes": ["Proof of address check: appears to show a residential address (x)"]}
    assert classify_support.address_check(rule_result, text_available=True) == "visible"


def test_address_check_not_visible_business_address():
    rule_result = {"notes": ["Proof of address check: address present but appears to be a business/office address, not residential"]}
    assert classify_support.address_check(rule_result, text_available=True) == "not_visible"


def test_address_check_unknown_when_no_text():
    assert classify_support.address_check({}, text_available=False) == "unknown"


def test_validate_agent_output_happy_path():
    raw = (
        '{"document_type": "payslip", "confidence": 0.9, "evidence": ["net pay"], '
        '"reasoning": "clear", "needs_manual_review": false, "fulfilled_requirements": ["payslip"]}'
    )
    result = classify_support.validate_agent_output(raw)
    assert result["document_type"] == "payslip"
    assert result["needs_manual_review"] is False


def test_validate_agent_output_unparseable_is_manual_review_not_a_crash():
    result = classify_support.validate_agent_output("not json")
    assert result["document_type"] is None
    assert result["needs_manual_review"] is True


def test_validate_agent_output_aliases_other_to_unrecognised():
    raw = '{"document_type": "other", "confidence": 0.2}'
    result = classify_support.validate_agent_output(raw)
    assert result["document_type"] == "unrecognised"


def test_validate_agent_output_passport_self_fulfils_id_document():
    raw = '{"document_type": "passport", "confidence": 0.9, "needs_manual_review": false}'
    result = classify_support.validate_agent_output(raw)
    assert result["fulfilled_requirements"] == ["id_document"]


# --- router ----------------------------------------------------------------

def _candidate(document_type, match_strength, confidence=None):
    return {
        "document_type": document_type,
        "match_strength": match_strength,
        "confidence": confidence if confidence is not None else match_strength,
        "reasoning": "",
        "evidence": [],
    }


def test_router_fast_path_when_sources_agree_strongly():
    rule_result = {"top_candidates": [_candidate("payslip", 0.9, 1.0)], "notes": []}
    llm_result = {"top_candidates": [_candidate("payslip", 0.9, 1.0)], "notes": []}
    assert router.decide_route(rule_result, llm_result, "some payslip text" * 10) == router.FAST


def test_router_agent_path_when_sources_disagree():
    rule_result = {"top_candidates": [_candidate("payslip", 0.9)], "notes": []}
    llm_result = {"top_candidates": [_candidate("bank_statement", 0.9)], "notes": []}
    assert router.decide_route(rule_result, llm_result, "text") == router.AGENT


def test_router_agent_path_when_below_threshold():
    rule_result = {"top_candidates": [_candidate("payslip", 0.5, 1.0)], "notes": []}
    llm_result = {"top_candidates": [_candidate("payslip", 0.5, 1.0)], "notes": []}
    assert router.decide_route(rule_result, llm_result, "text") == router.AGENT


def test_router_agent_path_when_no_candidates_at_all():
    empty = {"top_candidates": [], "notes": []}
    assert router.decide_route(empty, empty, "") == router.AGENT


def test_router_agent_path_for_nric_with_unknown_address_check():
    rule_result = {"top_candidates": [_candidate("nric", 0.95, 1.0)], "notes": []}
    llm_result = {"top_candidates": [_candidate("nric", 0.95, 1.0)], "notes": []}
    # No address-related note at all -> address_check == "unknown" -> escalate.
    assert router.decide_route(rule_result, llm_result, "some nric text" * 10) == router.AGENT


# --- graph shape (needs langgraph installed) --------------------------------

def test_classify_graph_compiles():
    pytest.importorskip("langgraph", reason="langgraph not installed yet — see requirements.txt")
    from app.langgraph.classify_graph import build_classify_graph

    graph = build_classify_graph()
    assert graph is not None


def test_document_graph_compiles():
    pytest.importorskip("langgraph", reason="langgraph not installed yet — see requirements.txt")
    from app.langgraph.graph import build_document_graph

    graph = build_document_graph()
    assert graph is not None
