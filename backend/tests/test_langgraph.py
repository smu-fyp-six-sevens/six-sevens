"""
Tests for the langgraph pipeline (backend/app/langgraph).

Pure-logic tests (rule_classify, llm_classify parsing, classify_support,
extract parsing, tool-staging) and graph-shape tests. langgraph and
langchain-anthropic are hard requirements (app.main imports them), so a
broken install fails these tests instead of skipping them.
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


@pytest.mark.parametrize(("text", "expected"), [
    pytest.param(
        "registered office blk 123",
        ["Proof of address check: address present but appears to be a business/office address, not residential"],
        id="a business address cancels out a weak positive",
    ),
    pytest.param(
        "123 Jalan Road", ["Proof of address check: no residential address detected"],
        id="a weak positive alone is not enough",
    ),
])
def test_residential_address_evidence(text, expected):
    assert rc._residential_address_evidence(text) == expected


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

@pytest.mark.parametrize(("rule_result", "text_available", "expected"), [
    pytest.param(
        {"notes": ["Proof of address check: appears to show a residential address (x)"]}, True, "visible",
        id="a residential address is visible",
    ),
    pytest.param(
        {"notes": ["Proof of address check: address present but appears to be a business/office address, not residential"]},
        True, "not_visible", id="a business address is not visible",
    ),
    pytest.param({}, False, "unknown", id="unknown when there is no text"),
])
def test_address_check(rule_result, text_available, expected):
    assert classify_support.address_check(rule_result, text_available=text_available) == expected


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


def test_parse_extraction_output_malformed_json_fails_with_model_text_and_does_not_escalate():
    result = extract.parse_extraction_output("This is not a payslip at all.")
    assert result["extracted_fields"] == {}
    assert "This is not a payslip at all." in result["output_error"]
    assert result["escalation"] is None


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


# --- tool staging + graph shape ---------------------------------------------

def test_the_tool_order_is_enforced_llm_first_vision_last():
    """
    llm_classify_tool only on the agent's first turn; vision_llm from the second turn on, and only once. Enforced
    in code (classify_graph._refusal, applied by tools_node), not just prompted, since vision sends the whole
    document image and is by far the most expensive tool. Both tools are offered every turn, so a call out of
    turn gets an error result instead of running.
    """
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from app.langgraph.classify_graph import _NOT_RUN, _refusal

    start = [HumanMessage(content="go")]
    assert _refusal("llm_classify_tool", start) is None
    assert _refusal("vision_llm", start) is not None

    first_turn = start + [
        AIMessage(content="", tool_calls=[{"name": "llm_classify_tool", "args": {}, "id": "1"}]),
        ToolMessage(content="{}", tool_call_id="1", name="llm_classify_tool"),
    ]
    assert _refusal("llm_classify_tool", first_turn) is not None
    assert _refusal("vision_llm", first_turn) is None

    # A refused vision call didn't run, so vision is still available; one that ran isn't.
    refused = first_turn + [
        AIMessage(content="", tool_calls=[{"name": "vision_llm", "args": {}, "id": "2"}]),
        ToolMessage(content=f"{_NOT_RUN}no", tool_call_id="2", name="vision_llm"),
    ]
    assert _refusal("vision_llm", refused) is None
    used_vision = first_turn + [
        AIMessage(content="", tool_calls=[{"name": "vision_llm", "args": {}, "id": "2"}]),
        ToolMessage(content="{}", tool_call_id="2", name="vision_llm"),
    ]
    assert _refusal("vision_llm", used_vision) is not None


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


@pytest.mark.parametrize(("escalation", "basis", "expected"), [
    pytest.param(
        {"reason": "This is a Singapore Identity Card", "suggested_document_type": "nric"}, "certain_match",
        "analyzed", id="naming the same type is not a reclassify",
    ),
    pytest.param(
        {"reason": "NRIC number is absent from the OCR text"}, "certain_match",
        "analyzed", id="no other type on a certain classification is not a reclassify",
    ),
    pytest.param(
        {"reason": "This is a passport", "suggested_document_type": "passport"}, "certain_match",
        "reclassify", id="naming a different type still reclassifies",
    ),
    pytest.param(
        {"reason": "Doesn't look like an NRIC"}, "uncorroborated",
        "reclassify", id="no other type on an uncertain classification still reclassifies",
    ),
])
def test_an_escalation_decides_whether_to_reclassify(escalation, basis, expected):
    from app.langgraph.graph import _decide_after_extract

    assert _decide_after_extract(_redacted_ic_state(escalation, basis=basis)) == expected


def _classify_graph():
    from app.langgraph.classify_graph import build_classify_graph
    return build_classify_graph()


def _document_graph():
    from app.langgraph.graph import build_document_graph
    return build_document_graph()


def _detection_graph():
    from app.langgraph.detection.graph import build_detection_graph
    return build_detection_graph()


def _specialist_graph(name: str):
    from app.langgraph.detection.specialists import SPECIALISTS, build_specialist_graph
    return build_specialist_graph(SPECIALISTS[name])


_SPECIALIST_NODES = {"agent", "tools", "feedback", "collect"}
_GRAPHS = [
    pytest.param(_classify_graph, {"rule_classify", "agent", "tools", "validate", "repair"}, id="classify"),
    pytest.param(
        _document_graph,
        {"ingest", "classify", "extract", "check_text_available", "finalise_classification", "mark_analyzed", "fail"},
        id="document",
    ),
    pytest.param(
        _detection_graph,
        {"load_context", "lead_plan", "specialist", "lead_review", "run_second_look", "synthesize"},
        id="detection",
    ),
    *(
        pytest.param(lambda name=name: _specialist_graph(name), _SPECIALIST_NODES, id=f"specialist-{name}")
        for name in ("layout_rendering", "content_validation", "digital_authenticity")
    ),
]


@pytest.mark.parametrize(("build", "expected_nodes"), _GRAPHS)
def test_a_graph_compiles_with_its_expected_steps(build, expected_nodes):
    assert expected_nodes <= set(build().get_graph().nodes)


def test_every_specialist_has_a_compiled_graph_test():
    from app.langgraph.detection.specialists import SPECIALISTS

    assert set(SPECIALISTS) == {"layout_rendering", "content_validation", "digital_authenticity"}


def test_get_document_graph_builds_once_and_caches():
    from app.langgraph import graph as document_graph_module

    document_graph_module.document_graph = None
    first = document_graph_module.get_document_graph()
    second = document_graph_module.get_document_graph()
    assert first is second
    document_graph_module.document_graph = None


def test_forget_drops_the_in_memory_traces_of_deleted_documents() -> None:
    from app.langgraph import trace

    trace._traces["gone-1"] = {"document_id": "gone-1", "steps": []}
    trace._traces["kept-1"] = {"document_id": "kept-1", "steps": []}
    trace.forget(["gone-1", "never-seen"])

    assert "gone-1" not in trace._traces and "kept-1" in trace._traces
    trace._traces.pop("kept-1", None)
