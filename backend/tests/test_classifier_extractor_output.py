"""The classify agent and the extractor must survive an answer that is not what they asked for."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage

from app import run_context
from app.langgraph import classify_support, extract, llm_classify, nodes
from app.langgraph import graph as document_graph

# --- the classify agent's answer -----------------------------------------------------------------


@pytest.mark.parametrize("reply", ["", "not json", "[1]", "null", '{"document_type": ', "{}"])
def test_an_unreadable_classifier_answer_goes_to_manual_review(reply) -> None:
    result = classify_support.validate_agent_output(reply)
    assert result["document_type"] is None and result["needs_manual_review"] is True


def test_one_bad_classifier_field_does_not_lose_the_rest() -> None:
    result = classify_support.validate_agent_output(
        '{"document_type": "payslip", "confidence": "high", "evidence": "net pay shown", '
        '"needs_manual_review": false, "fulfilled_requirements": ["payslip", "made_up"]}'
    )
    assert result["document_type"] == "payslip"
    assert result["confidence"] == 0.0  # unreadable is not a score
    assert result["evidence"] == ["net pay shown"]  # a bare string is one item, not a list of letters
    assert result["fulfilled_requirements"] == ["payslip"]
    assert result["output_status"] == "degraded"


@pytest.mark.parametrize("confidence, expected", [(1.7, 1.0), (-3, 0.0), ("0.4", 0.4), (True, 0.0), (None, 0.0)])
def test_classifier_confidence_is_clamped_or_ignored(confidence, expected) -> None:
    body = json.dumps({"document_type": "payslip", "confidence": confidence, "needs_manual_review": False})
    assert classify_support.validate_agent_output(body)["confidence"] == expected


def test_a_classifier_answer_that_is_repaired_is_kept_in_the_audit_record() -> None:
    raw = '{"document_type": "payslip", "confidence": "high"}'
    with run_context.run_context() as run:
        classify_support.validate_agent_output(raw)
    [entry] = run.repairs
    assert entry["site"] == "classifier agent" and entry["raw"] == raw and "confidence" in " ".join(entry["issues"])


def test_an_unreadable_llm_classifier_reply_is_recorded_and_gives_no_candidates() -> None:
    with run_context.run_context() as run:
        result = llm_classify.parse_llm_classification("I think it is a payslip.")
    assert result["top_candidates"] == []
    assert run.repairs and run.repairs[0]["raw"] == "I think it is a payslip."


# --- the extractor's answer ----------------------------------------------------------------------


def test_a_reply_without_extracted_fields_is_not_an_extraction() -> None:
    result = extract.parse_extraction_output('{"result": "done"}')
    assert result["output_error"] and result["extracted_fields"] == {}


ESCALATION = {"suggested_document_type": "bank_statement", "reason": "It is a statement", "evidence": ["Opening balance"]}


@pytest.mark.parametrize(
    "body",
    [
        {"extracted_fields": {}, "escalation": ESCALATION},
        {"extracted_fields": None, "escalation": ESCALATION},
        {"escalation": ESCALATION},
    ],
)
def test_an_escalation_is_kept_with_or_without_fields_and_sends_the_document_back(body) -> None:
    result = extract.parse_extraction_output(json.dumps(body))
    assert result.get("output_error") is None and result["escalation"] == ESCALATION
    state = {"extraction": result, "classification": {"document_type": "payslip"}, "attempts": 0}
    assert document_graph._decide_after_extract(state) == "reclassify"  # type: ignore[arg-type]


def test_a_bare_reason_as_the_escalation_still_sends_the_document_back() -> None:
    result = extract.parse_extraction_output('{"extracted_fields": {}, "escalation": "This is a bank statement"}')
    assert result["escalation"] == {"reason": "This is a bank statement"}


@pytest.mark.parametrize("body", ['{"extracted_fields": null}', '{"extracted_fields": [1]}', "{}"])
def test_no_fields_and_no_escalation_is_not_an_extraction(body) -> None:
    assert extract.parse_extraction_output(body)["output_error"]


def test_an_empty_extraction_is_still_an_extraction() -> None:
    result = extract.parse_extraction_output('{"extracted_fields": {}}')
    assert result.get("output_error") is None and result["extracted_fields"] == {}


@pytest.mark.parametrize(
    "confidence, expected", [(0.8, 0.8), (1.7, 1.0), (-1, 0.0), ("high", None), (True, None)]
)
def test_field_confidence_is_clamped_and_never_invented(confidence, expected) -> None:
    body = json.dumps({"extracted_fields": {"a": {"value": 5, "confidence": confidence}}})
    assert extract.parse_extraction_output(body)["field_confidences"]["a"] == expected


def test_a_confidence_that_is_not_a_number_is_unknown() -> None:
    body = '{"extracted_fields": {"a": {"value": 5, "confidence": NaN}}}'
    assert extract.parse_extraction_output(body)["field_confidences"]["a"] is None


def test_a_cut_off_extraction_keeps_the_complete_fields_and_says_so() -> None:
    cut = (
        '{"extracted_fields": {"company_name": {"value": "ACME", "confidence": 0.9}, '
        '"total": {"value": "1,200", "confidence": 0.8}, "transactions": {"value": [{"date": "1 Jan'
    )
    result = extract.parse_extraction_output(cut, cut_off=True)
    assert result["extracted_fields"] == {"company_name": "ACME", "total": "1,200"}
    assert "2 complete field(s)" in result["output_error"] and result["escalation"] is None


class _Model:
    def __init__(self, *replies: AIMessage) -> None:
        self.replies, self.calls = list(replies), []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        return self.replies.pop(0)


async def _run_extraction(model: _Model):
    with (
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model),
        patch.object(extract, "build_extraction_prompt", return_value="prompt"),
    ):
        return await extract.run_extraction("payslip", "Net pay 1,200.00")


GOOD = '{"extracted_fields": {"net_pay": {"value": 1200.0, "confidence": 0.9}}}'


async def test_an_unreadable_extraction_is_retried_once_and_then_read() -> None:
    model = _Model(AIMessage(content="Sorry, here you go"), AIMessage(content=GOOD))
    result = await _run_extraction(model)
    assert len(model.calls) == 2 and result["retried"] is True
    assert result["extracted_fields"] == {"net_pay": 1200.0} and result.get("output_error") is None


async def test_an_extraction_that_stays_unreadable_fails_after_one_retry() -> None:
    model = _Model(AIMessage(content="no"), AIMessage(content="still no"))
    result = await _run_extraction(model)
    assert len(model.calls) == 2 and result["output_error"] and result["escalation"] is None


async def test_a_cut_off_extraction_is_not_retried() -> None:
    cut = AIMessage(content='{"extracted_fields": {"a": 1, "b": ', response_metadata={"stop_reason": "max_tokens"})
    model = _Model(cut)
    result = await _run_extraction(model)
    assert len(model.calls) == 1 and "cut off" in result["output_error"]


async def test_extraction_counts_against_the_runs_call_ceiling() -> None:
    model = _Model(AIMessage(content="no"), AIMessage(content=GOOD))
    with run_context.run_context(1), pytest.raises(run_context.RunBudgetExceeded):
        await _run_extraction(model)
    assert len(model.calls) == 1  # the retry was refused before any request


def test_an_unusable_extraction_is_not_a_reclassification() -> None:
    state = {
        "extraction": {"output_error": "Extraction did not return valid JSON.", "escalation": None},
        "classification": {"document_type": "payslip"},
        "attempts": 0,
    }
    assert document_graph._decide_after_extract(state) == "failed"  # type: ignore[arg-type]


async def test_an_unusable_extraction_stops_the_document_and_keeps_what_was_stored() -> None:
    state = {"document_id": "d", "classification": {"document_type": "payslip", "confidence": 0.99}}
    failed = {
        "extracted_fields": {}, "field_confidences": {}, "confidence": 0.0, "escalation": None,
        "output_error": "cut off", "output_status": "failed",
    }
    with (
        patch.object(nodes, "set_status", AsyncMock()),
        patch.object(nodes, "trace"),
        patch.object(nodes.text_cache, "get", return_value="text"),
        patch.object(nodes.extract, "run_extraction", AsyncMock(return_value=failed)),
        patch.object(nodes, "extracted_fields_repository") as fields_repo,
        patch.object(nodes, "document_repository") as documents_repo,
    ):
        out = await nodes.extract_node(state)  # type: ignore[arg-type]
    assert out["status"] == "failed" and "cut off" in out["failure_reason"]
    fields_repo.replace_extracted_fields.assert_not_called()
    documents_repo.update_extracted_fields.assert_not_called()


async def test_a_rerun_audit_loads_the_saved_trace_before_recording() -> None:
    from app.langgraph import pipeline

    order: list[str] = []
    with patch.object(pipeline, "trace") as trace:
        trace.resume.side_effect = lambda _id: order.append("resume")
        trace.record.side_effect = lambda *_a, **_k: order.append("record")
        await pipeline._record_run_audit("d", run_context.RunContext())
    assert order == ["resume", "record"]


# --- a low-confidence guess can still be sent back -----------------------------------------------


async def _extract_node(classification: dict, attempts: int):
    state = {"document_id": "d", "classification": classification, "attempts": attempts}
    ok = {"extracted_fields": {}, "field_confidences": {}, "confidence": 0.0, "escalation": None}
    with (
        patch.object(nodes, "set_status", AsyncMock()),
        patch.object(nodes, "trace"),
        patch.object(nodes.text_cache, "get", return_value="text"),
        patch.object(nodes.extract, "run_extraction", AsyncMock(return_value=ok)) as run,
        patch.object(nodes, "persist_extracted_fields", AsyncMock()),
    ):
        await nodes.extract_node(state)  # type: ignore[arg-type]
    return run


async def test_a_low_confidence_guess_is_given_to_the_generic_extraction() -> None:
    run = await _extract_node({"document_type": "list_of_loans", "confidence": 0.72}, attempts=0)
    run.assert_awaited_once_with("unrecognised", "text", classified_as="list_of_loans")


async def test_the_guess_is_not_repeated_after_a_reclassification() -> None:
    run = await _extract_node({"document_type": "list_of_loans", "confidence": 0.72}, attempts=1)
    run.assert_awaited_once_with("unrecognised", "text", classified_as=None)


@pytest.mark.parametrize(
    "classification",
    [{"document_type": "payslip", "confidence": 0.97}, {"document_type": "unrecognised", "confidence": 0.3}],
)
async def test_a_confident_or_other_document_gets_no_hint(classification) -> None:
    run = await _extract_node(classification, attempts=0)
    assert run.await_args.kwargs["classified_as"] is None


async def test_the_hint_is_its_own_block_after_the_cached_prefix() -> None:
    model = _Model(AIMessage(content=GOOD), AIMessage(content=GOOD))
    with (
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model),
        patch.object(extract, "build_extraction_prompt", return_value="prompt"),
    ):
        await extract.run_extraction("unrecognised", "Net pay 1,200.00", classified_as="list_of_loans")
        await extract.run_extraction("unrecognised", "Net pay 1,200.00")
    with_hint, without = (call[0]["content"] if isinstance(call[0], dict) else call[0].content for call in model.calls)
    assert [b["text"] for b in without] == ["prompt", "Net pay 1,200.00"]
    assert with_hint[0] == without[0] and with_hint[0].get("cache_control")
    assert "list_of_loans" in with_hint[1]["text"] and with_hint[2]["text"] == "Net pay 1,200.00"


def test_an_objection_with_no_suggested_type_sends_the_document_back() -> None:
    body = {"extracted_fields": {}, "escalation": {"suggested_document_type": None, "reason": "A budget proposal"}}
    result = extract.parse_extraction_output(json.dumps(body))
    state = {"extraction": result, "classification": {"document_type": "list_of_loans"}, "attempts": 0}
    assert document_graph._decide_after_extract(state) == "reclassify"  # type: ignore[arg-type]


_EXAMPLE_REPORT = """Bureau Score
Example 1: Scored >>
Score......................... 1420
Risk Grade ....................GG
Probability of Default 5.14%
Example 2: Non-Scored >>
Score......................... Not Applicable
Risk Grade ....................HX
For more details on the risk grades, see below.
Account balance 1,420.50 at BB Bank
"""


def _extraction(**fields) -> dict:
    return {
        "extracted_fields": dict(fields),
        "field_confidences": {name: 0.9 for name in fields},
        "field_reasons": {},
    }


def test_example_block_values_are_dropped() -> None:
    from app.langgraph.extract import _drop_example_values

    result = _drop_example_values(
        _extraction(bureau_score=1420, risk_grade="GG", probability_of_default="5.14%", accounts=[]), _EXAMPLE_REPORT,
    )

    assert sorted(result["dropped_example_values"]) == ["bureau_score", "probability_of_default", "risk_grade"]
    assert list(result["extracted_fields"]) == ["accounts"]


def test_value_also_printed_outside_an_example_block_is_kept() -> None:
    from app.langgraph.extract import _drop_example_values

    text = _EXAMPLE_REPORT + "Bureau Score 1420 for the applicant\nRisk Grade BB\n"
    result = _drop_example_values(_extraction(bureau_score=1420, risk_grade="BB"), text)

    assert result["dropped_example_values"] == []


def test_text_without_example_blocks_is_untouched() -> None:
    from app.langgraph.extract import _drop_example_values

    result = _drop_example_values(_extraction(bureau_score=1500), "Bureau Score 1500")

    assert result["dropped_example_values"] == []
    assert result["extracted_fields"] == {"bureau_score": 1500}
