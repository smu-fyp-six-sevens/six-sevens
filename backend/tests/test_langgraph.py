"""
Tests for the langgraph pipeline (backend/app/langgraph).

Split in two tiers:
- Pure-logic tests (rule_classify, llm_classify parsing, classify_support,
  extract parsing, tool-staging) have no dependency on the
  langgraph/langchain-anthropic packages and always run.
- Graph-shape tests need `langgraph` installed — see requirements.txt /
  pyproject.toml. They're skipped, not failed, if it isn't (`importorskip`),
  matching this repo's CI/CD stance of no network calls in tests.
"""

from __future__ import annotations

import pytest

from app.langgraph import classify_support, extract
from app.langgraph import rule_classify as rc
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


def test_residential_address_evidence_business_address_cancels_out_a_weak_positive():
    notes = rc._residential_address_evidence("registered office blk 123")
    assert notes == [
        "Proof of address check: address present but appears to be a "
        "business/office address, not residential"
    ]


def test_residential_address_evidence_weak_positive_alone_is_not_enough():
    notes = rc._residential_address_evidence("123 Jalan Road")
    assert notes == ["Proof of address check: no residential address detected"]


def test_certain_signal_returns_the_one_matching_document_type():
    doc_type, matched = rc._certain_signal("Business Profile (Company) of Acme Pte Ltd")
    assert doc_type == "certificate_of_incumbency"
    assert matched


def test_certain_signal_none_when_nothing_matches():
    assert rc._certain_signal("just some ordinary prose") is None


def test_classify_by_rules_sets_certain_type_when_a_certain_pattern_fires():
    result = classify_by_rules("doc.pdf", "Business Profile (Company) of Acme Pte Ltd")
    assert result["certain_type"] == "certificate_of_incumbency"
    assert result["certain_evidence"]


def test_reasoning_filename_only_with_no_extracted_text():
    text = rc._reasoning(0.5, 0.0, 0.5, has_text=False)
    assert "no extracted text was supplied" in text


def test_normalise_all_zero_scores_splits_confidence_evenly():
    candidates = [
        {"document_type": "a", "match_strength": 0.0, "reasoning": "r", "evidence": []},
        {"document_type": "b", "match_strength": 0.0, "reasoning": "r", "evidence": []},
    ]
    out = rc._normalise(candidates)
    assert [c["confidence"] for c in out] == [0.5, 0.5]


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


# --- extract parsing ---------------------------------------------------

def test_parse_extraction_output_splits_value_and_confidence_per_field():
    raw = (
        '{"extracted_fields": {"name": {"value": "Tan", "confidence": 0.9}, '
        '"id": {"value": "S123", "confidence": 0.7}}}'
    )
    result = extract.parse_extraction_output(raw)
    assert result["extracted_fields"] == {"name": "Tan", "id": "S123"}
    assert result["field_confidences"] == {"name": 0.9, "id": 0.7}
    assert result["confidence"] == pytest.approx(0.8)


def test_parse_extraction_output_strips_code_fence_and_think_block():
    raw = (
        "<think>hmm</think>\n```json\n"
        '{"extracted_fields": {"a": {"value": 1, "confidence": 1.0}}}\n```'
    )
    assert extract.parse_extraction_output(raw)["extracted_fields"] == {"a": 1}


def test_parse_extraction_output_unwrapped_field_has_unknown_confidence():
    result = extract.parse_extraction_output('{"extracted_fields": {"a": "bare"}}')
    assert result["extracted_fields"] == {"a": "bare"}
    assert result["field_confidences"] == {"a": None}
    assert result["confidence"] == 0.0


def test_parse_extraction_output_malformed_json_escalates_with_model_text():
    result = extract.parse_extraction_output("This is not a payslip at all.")
    assert result["extracted_fields"] == {}
    assert "This is not a payslip at all." in result["escalation"]["reason"]


def test_drop_unprinted_numbers_removes_a_figure_the_llm_calculated():
    # Three per-month totals printed; the LLM summed them into total_credits.
    text = "Total 36,182.85 70,506.33\nTotal 54,836.12 51,736.01\nTotal 42,639.19 47,306.64"
    result = {
        "extracted_fields": {"total_credits": 169548.98, "bank_name": "DBS"},
        "field_confidences": {"total_credits": 0.85, "bank_name": 0.95},
        "confidence": 0.9,
    }
    out = extract._drop_unprinted_numbers(result, text)
    assert out["extracted_fields"] == {"bank_name": "DBS"}
    assert out["field_confidences"] == {"bank_name": 0.95}
    assert out["confidence"] == pytest.approx(0.95)
    assert out["dropped_unprinted_fields"] == ["total_credits"]


def test_drop_unprinted_numbers_keeps_printed_figures_in_any_format():
    text = "Total Credits SGD 169,548.98\nWithdrawal 35.00\nProcessing Fee 1.00%"
    fields = {
        "total_credits": "SGD 169,548.98",
        "total_debits": -35,
        "fee_percentage": "1%",
        "statement_period_end": "31-Aug-2026",
        "transactions": [{"amount": 999.99}],
    }
    out = extract._drop_unprinted_numbers(
        {"extracted_fields": dict(fields), "field_confidences": {}, "confidence": 0.0}, text
    )
    assert out["extracted_fields"] == fields
    assert out["dropped_unprinted_fields"] == []


def test_drop_unprinted_numbers_skips_when_text_has_no_figures():
    result = {"extracted_fields": {"net_pay": 5000}, "field_confidences": {}, "confidence": 0.0}
    assert extract._drop_unprinted_numbers(result, "")["extracted_fields"] == {"net_pay": 5000}


def test_drop_empty_fields_leaves_out_optional_fields_the_document_does_not_print():
    # A statement with per-month totals only: the LLM returns the
    # whole-statement totals empty and flags them as uncertain.
    result = {
        "extracted_fields": {
            "bank_name": None,
            "account_number": "123-456",
            "transactions": [],
            "holds": [],
            "total_credits": None,
            "total_debits": "  ",
            "company_name": "ACME PTE LTD",
        },
        "field_confidences": {"total_credits": 0.3, "total_debits": 0.3, "company_name": 0.95},
        "field_reasons": {"total_credits": "not printed", "total_debits": "not printed"},
        "confidence": 0.5,
    }
    out = extract._drop_empty_fields(result, "bank_statement")
    # Required fields stay even when empty, so they still count as missing;
    # an empty list is kept as "none".
    assert out["extracted_fields"] == {
        "bank_name": None,
        "account_number": "123-456",
        "transactions": [],
        "holds": [],
        "company_name": "ACME PTE LTD",
    }
    assert out["field_confidences"] == {"company_name": 0.95}
    assert out["field_reasons"] == {}
    assert out["confidence"] == pytest.approx(0.95)
    assert out["dropped_empty_fields"] == ["total_credits", "total_debits"]


def test_drop_empty_fields_keeps_the_document_confidence_when_no_field_confidences_remain():
    result = {"extracted_fields": {"total_credits": None}, "field_confidences": {"total_credits": 0.2}, "confidence": 0.8}
    out = extract._drop_empty_fields(result, "bank_statement")
    assert out["extracted_fields"] == {}
    assert out["confidence"] == 0.8


_THREE_MONTH_TEXT = (
    "Total   36,182.85   70,506.33   Balance Carried Forward   109,129.65\n"
    "Total   54,836.12   51,736.01\n"
    "Total   42,639.19   47,306.64"
)


def test_drop_period_totals_removes_a_month_copied_into_the_statement_totals():
    # AcctStatement_MWS: the LLM put June's totals in total_credits/total_debits.
    result = {
        "extracted_fields": {
            "total_credits": "70,506.33", "total_debits": "36,182.85",
            "june_total_deposits": "70,506.33", "june_total_withdrawals": "36,182.85",
        },
        "field_confidences": {"total_credits": 0.85, "total_debits": 0.85},
    }
    out = extract._drop_period_totals(result, _THREE_MONTH_TEXT)
    assert out["dropped_period_totals"] == ["total_credits", "total_debits"]
    assert set(out["extracted_fields"]) == {"june_total_deposits", "june_total_withdrawals"}
    assert out["field_confidences"] == {}


def test_drop_period_totals_catches_a_copy_from_the_printed_total_lines_alone():
    result = {"extracted_fields": {"total_credits": "51,736.01"}, "field_confidences": {}}
    assert extract._drop_period_totals(result, _THREE_MONTH_TEXT)["extracted_fields"] == {}


def test_drop_period_totals_keeps_a_single_whole_statement_total():
    text = "Total Credits SGD 169,548.98\nTotal Debits SGD 35.00"
    fields = {"total_credits": "169,548.98", "total_deposits": "169,548.98", "total_debits": "35.00"}
    out = extract._drop_period_totals({"extracted_fields": dict(fields), "field_confidences": {}}, text)
    assert out["extracted_fields"] == fields
    assert out["dropped_period_totals"] == []


def test_split_prompt_for_caching_keeps_raw_text_separate_from_the_schema_prefix():
    prefix, text = extract._split_prompt_for_caching("payslip", "Net pay: 5000")
    assert text == "Net pay: 5000"
    assert "Net pay: 5000" not in prefix
    assert prefix  # the schema/instructions prefix is never empty


async def test_run_extraction_calls_the_model_and_parses_its_response():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    pytest.importorskip("langchain_anthropic", reason="langchain-anthropic not installed")

    fake_response = SimpleNamespace(
        content='{"extracted_fields": {"net_pay": {"value": 5000, "confidence": 0.9}}}'
    )
    mock_model = MagicMock()
    mock_model.ainvoke = AsyncMock(return_value=fake_response)

    with patch("langchain_anthropic.ChatAnthropic", return_value=mock_model):
        result = await extract.run_extraction("payslip", "Net pay: 5000")

    assert result["extracted_fields"] == {"net_pay": 5000}
    assert result["field_confidences"] == {"net_pay": 0.9}
    mock_model.ainvoke.assert_awaited_once()


# --- tool staging + graph shape (needs langgraph installed) -----------------

def test_available_tools_enforces_llm_first_vision_last():
    """
    The agent's first turn is only ever offered llm_classify_tool; vision_llm
    only becomes available from the second turn onward, and only until it's
    been called once — enforced in code (classify_graph._available_tools),
    not just prompted, since vision sends the whole document image and is by
    far the most expensive tool. See classify_graph.py's module docstring.
    """
    pytest.importorskip("langgraph", reason="langgraph not installed — see requirements.txt")
    from langchain_core.messages import AIMessage, HumanMessage

    from app.langgraph.classify_graph import _available_tools

    assert [t.name for t in _available_tools([])] == ["llm_classify_tool"]

    first_turn = [
        HumanMessage(content="go"),
        AIMessage(content="", tool_calls=[{"name": "llm_classify_tool", "args": {}, "id": "1"}]),
    ]
    assert [t.name for t in _available_tools(first_turn)] == ["vision_llm"]

    used_vision = first_turn + [
        AIMessage(content="", tool_calls=[{"name": "vision_llm", "args": {}, "id": "2"}])
    ]
    assert _available_tools(used_vision) == []


def test_classify_graph_compiles():
    pytest.importorskip("langgraph", reason="langgraph not installed — see requirements.txt")
    from app.langgraph.classify_graph import build_classify_graph

    graph = build_classify_graph()
    assert graph is not None


def test_document_graph_compiles():
    pytest.importorskip("langgraph", reason="langgraph not installed — see requirements.txt")
    from app.langgraph.graph import build_document_graph

    graph = build_document_graph()
    assert graph is not None


def test_route_after_ingest():
    from app.langgraph.graph import _route_after_ingest

    assert _route_after_ingest({"status": "failed"}) == "fail"
    assert _route_after_ingest({"status": "uploaded"}) == "classify"


def test_route_after_check_text():
    from app.langgraph.graph import _route_after_check_text

    assert _route_after_check_text({"status": "failed"}) == "fail"
    assert _route_after_check_text({"status": "extracting"}) == "extract"


def test_decide_after_extract_no_escalation_is_analyzed():
    from app.langgraph.graph import _decide_after_extract

    assert _decide_after_extract({"extraction": {}}) == "analyzed"


def test_decide_after_extract_escalation_within_budget_reclassifies():
    from app.config import settings
    from app.langgraph.graph import _decide_after_extract

    state = {"extraction": {"escalation": True}, "attempts": 0}
    assert settings.MAX_RECLASSIFICATION_ATTEMPTS > 0
    assert _decide_after_extract(state) == "reclassify"


def test_decide_after_extract_escalation_past_budget_is_unresolved():
    from app.config import settings
    from app.langgraph.graph import _decide_after_extract

    state = {"extraction": {"escalation": True}, "attempts": settings.MAX_RECLASSIFICATION_ATTEMPTS}
    assert _decide_after_extract(state) == "unresolved"


def _redacted_ic_state(escalation: dict, *, basis: str = "certain_match") -> dict:
    # redacted_ic.jpg: a rule trigger made it certainly an NRIC; its number is blacked out.
    return {
        "classification": {"document_type": "nric", "basis": basis},
        "extraction": {"escalation": escalation},
        "attempts": 0,
    }


def test_escalation_naming_the_same_type_is_not_a_reclassify():
    from app.langgraph.graph import _decide_after_extract

    escalation = {"reason": "This is a Singapore Identity Card", "suggested_document_type": "nric"}
    assert _decide_after_extract(_redacted_ic_state(escalation)) == "analyzed"


def test_escalation_without_another_type_on_a_certain_classification_is_not_a_reclassify():
    from app.langgraph.graph import _decide_after_extract

    escalation = {"reason": "NRIC number is absent from the OCR text"}
    assert _decide_after_extract(_redacted_ic_state(escalation)) == "analyzed"


def test_escalation_naming_a_different_type_still_reclassifies():
    from app.langgraph.graph import _decide_after_extract

    escalation = {"reason": "This is a passport", "suggested_document_type": "passport"}
    assert _decide_after_extract(_redacted_ic_state(escalation)) == "reclassify"


def test_escalation_without_another_type_on_an_uncertain_classification_still_reclassifies():
    from app.langgraph.graph import _decide_after_extract

    escalation = {"reason": "Doesn't look like an NRIC"}
    assert _decide_after_extract(_redacted_ic_state(escalation, basis="uncorroborated")) == "reclassify"


def test_get_document_graph_builds_once_and_caches():
    pytest.importorskip("langgraph", reason="langgraph not installed — see requirements.txt")
    from app.langgraph import graph as document_graph_module

    document_graph_module.document_graph = None
    first = document_graph_module.get_document_graph()
    second = document_graph_module.get_document_graph()
    assert first is second
    document_graph_module.document_graph = None


def test_detection_graph_compiles():
    pytest.importorskip("langgraph", reason="langgraph not installed — see requirements.txt")
    from app.langgraph.detection.graph import build_detection_graph

    graph = build_detection_graph()
    assert graph is not None


def test_specialist_graphs_compile():
    pytest.importorskip("langgraph", reason="langgraph not installed — see requirements.txt")
    from app.langgraph.detection.specialists import SPECIALISTS, build_specialist_graph

    for spec in SPECIALISTS.values():
        assert build_specialist_graph(spec) is not None
