"""
What officers' corrections teach the classifier and the extractor (corrections_repository, correction_hints): read
from audit_events through type_corrections and field_edit_counts (answered by the fake), and shown as short hints.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage, ToolMessage

from app import corrections_repository
from app.corrections_repository import FieldEdit, TypeCorrection
from app.langgraph import classify_graph, correction_hints, extract
from app.langgraph.classification_confidence import tool_results
from tests.support.fake_supabase import FakeSupabase


def _fake(**functions) -> FakeSupabase:
    fake = FakeSupabase()
    for name, rows in functions.items():
        fake.functions[name] = lambda params, rows=rows: rows
    return fake


def _pair(from_type: str = "certificate_of_incorporation", to_type: str = "certificate_of_incumbency", cases: int = 2):
    return TypeCorrection(from_type=from_type, to_type=to_type, cases=cases)


# --- the classifier -------------------------------------------------------------------------------------------------


def test_a_correction_counts_once_two_cases_agree() -> None:
    fake = _fake(type_corrections=[
        {"from_type": "payslip", "to_type": "notice_of_assessment", "cases": 1},
        {"from_type": "certificate_of_incorporation", "to_type": "certificate_of_incumbency", "cases": 2},
    ])
    with patch.object(corrections_repository, "service_supabase_client", return_value=fake):
        pairs = corrections_repository.type_corrections()
    assert [(p.from_type, p.to_type) for p in pairs] == [("certificate_of_incorporation", "certificate_of_incumbency")]
    assert fake.rpc_calls == [("type_corrections", {"p_since": corrections_repository.LEARNING_SINCE.isoformat()})]


def test_a_recurring_pair_gives_its_guess_a_hint_with_the_signal() -> None:
    hint = correction_hints.type_hint("certificate_of_incorporation", [_pair()])
    assert "corrected a certificate of incorporation to a certificate of incumbency in 2 cases" in hint
    assert "lists the company's current directors" in hint
    assert "decide from the document itself" in hint


def test_a_pair_with_no_signal_just_names_the_confusion() -> None:
    hint = correction_hints.type_hint("source_of_wealth", [_pair("source_of_wealth", "proof_of_address")])
    assert "Check it isn't a proof of address." in hint


def test_a_guess_with_no_recurring_pair_gets_no_hint() -> None:
    assert correction_hints.type_hint("payslip", [_pair()]) == ""
    assert correction_hints.type_hint(None, [_pair()]) == ""
    assert correction_hints.type_hint("certificate_of_incorporation", [_pair()], shown=["certificate_of_incorporation"]) == ""


async def test_a_failed_read_gives_no_hint() -> None:
    with patch.object(corrections_repository, "type_corrections", side_effect=RuntimeError("down")):
        assert await correction_hints.load_type_corrections() == []


def _llm_result(guess: str) -> ToolMessage:
    content = json.dumps({"top_candidates": [{"document_type": guess, "match_strength": 0.8}]})
    return ToolMessage(content=content, tool_call_id="c1", name="llm_classify_tool")


def test_a_classify_tools_guess_gets_its_hint_once_and_still_reads_as_its_result() -> None:
    state = {"document_id": "doc-1", "type_corrections": [_pair().model_dump()], "type_hints_shown": []}
    [message], shown = classify_graph._with_type_hints([_llm_result("certificate_of_incorporation")], state)  # type: ignore[arg-type]

    assert isinstance(message.content, list) and "certificate of incumbency" in message.content[1]["text"]
    assert shown == ["certificate_of_incorporation"]
    # The confidence still reads the tool's own output.
    assert tool_results([message])["llm"]["top_candidates"][0]["document_type"] == "certificate_of_incorporation"

    [again], _ = classify_graph._with_type_hints([_llm_result("certificate_of_incorporation")], {**state, "type_hints_shown": shown})  # type: ignore[arg-type]
    assert isinstance(again.content, str)


async def test_the_rule_guess_gets_its_hint_after_the_cached_prompt() -> None:
    model = MagicMock()
    model.bind_tools.return_value = model
    model.ainvoke = AsyncMock(return_value=AIMessage(content='{"document_type": "certificate_of_incorporation"}'))
    rule_result = {"top_candidates": [{"document_type": "certificate_of_incorporation", "match_strength": 0.7}]}
    with (
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model),
        patch.object(correction_hints, "load_type_corrections", AsyncMock(return_value=[_pair()])),
        patch.object(classify_graph.text_cache, "get", return_value="CERTIFICATE"),
    ):
        update = await classify_graph.agent_node({"document_id": "doc-1", "rule_result": rule_result})

    system = model.ainvoke.call_args.args[0][0]
    cached, dynamic = system.content
    assert "cache_control" in cached and "certificate of incumbency" not in cached["text"]
    assert "certificate of incumbency" in dynamic["text"]
    assert update["type_hints_shown"] == ["certificate_of_incorporation"]
    assert update["type_corrections"] == [_pair().model_dump()]


# --- the extractor --------------------------------------------------------------------------------------------------


def test_field_edits_count_per_type_once_two_cases_agree_and_skip_names_not_to_teach() -> None:
    fake = _fake(field_edit_counts=[
        {"document_type": "payslip", "field_name": "employer_address", "edit": "added", "cases": 3},
        {"document_type": "payslip", "field_name": "bonus", "edit": "deleted", "cases": 2},
        {"document_type": "payslip", "field_name": "overtime", "edit": "added", "cases": 1},
        {"document_type": "payslip", "field_name": "nationality", "edit": "added", "cases": 4},
        {"document_type": "payslip", "field_name": "Tan's Salary!", "edit": "added", "cases": 4},
    ])
    with patch.object(corrections_repository, "service_supabase_client", return_value=fake):
        edits = corrections_repository.field_edits("payslip")
    assert [(e.field_name, e.edit) for e in edits] == [("employer_address", "added"), ("bonus", "deleted")]
    assert fake.rpc_calls[0][1]["p_document_type"] == "payslip"


def test_the_extractor_hint_names_fields_added_and_deleted_and_no_values() -> None:
    edits = [
        FieldEdit(document_type="payslip", field_name="employer_address", edit="added", cases=3),
        FieldEdit(document_type="payslip", field_name="bonus", edit="deleted", cases=2),
        # Added in 2 cases and deleted in 2: no lesson either way.
        FieldEdit(document_type="payslip", field_name="allowance", edit="added", cases=2),
        FieldEdit(document_type="payslip", field_name="allowance", edit="deleted", cases=2),
    ]
    hint = correction_hints.field_hint("payslip", edits)
    assert "often added `employer_address` (3 cases)" in hint
    assert "often deleted `bonus` (2 cases)" in hint
    assert "allowance" not in hint
    assert correction_hints.field_hint("bank_statement", edits) == ""


async def test_the_extractors_hint_goes_after_the_cached_prefix() -> None:
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content='{"fields": {}}'))
    edits = [FieldEdit(document_type="payslip", field_name="employer_address", edit="added", cases=2)]
    with (
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model),
        patch.object(correction_hints, "load_field_edits", AsyncMock(return_value=edits)),
    ):
        await extract.run_extraction("payslip", "PAYSLIP\nNet pay 4,000")

    blocks = model.ainvoke.call_args_list[0].args[0][0]["content"]
    assert "cache_control" in blocks[0] and "employer_address" not in blocks[0]["text"]
    assert "employer_address" in blocks[1]["text"]
