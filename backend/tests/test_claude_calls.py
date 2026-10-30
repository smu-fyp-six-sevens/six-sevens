"""How every Claude call is made: effort and no sampling parameters, refusals as their own failure, tools asked for."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.case_pipeline.cross_document import agents
from app.case_pipeline.cross_document.evidence import CaseContext
from app.langgraph import _anthropic, pipeline
from app.langgraph.detection import specialists
from app.llm_io import ModelRefused, refusal_reason

_REFUSAL = {"stop_reason": "refusal", "stop_details": {"type": "refusal", "category": "cyber", "explanation": None}}


def _model(model: str, effort: str):
    _anthropic._models.clear()
    with patch.object(_anthropic.settings, "ANTHROPIC_API_KEY", "test-key"):
        built = _anthropic.build_chat_anthropic(model, 1024, effort=effort)  # type: ignore[arg-type]
    _anthropic._models.clear()
    return built


def test_a_request_sets_effort_and_adaptive_thinking_and_sends_no_temperature():
    payload = _model("claude-haiku-5-5", "low")._get_request_payload([HumanMessage(content="hi")])
    assert "temperature" not in payload and "top_p" not in payload and "top_k" not in payload
    assert payload["output_config"] == {"effort": "low"}
    # Thinking is the model's adaptive default; nothing turns it off or sets a fixed budget.
    assert payload.get("thinking", {"type": "adaptive"})["type"] == "adaptive"


def test_a_forced_tool_on_haiku_is_still_sent():
    # Setting `thinking` on the model would make langchain drop a forced tool_choice; effort alone keeps it.
    tool = {"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}
    bound = _model("claude-haiku-5-5", "low").bind_tools([tool], tool_choice="any")
    assert bound.kwargs["tool_choice"] == {"type": "any"}  # type: ignore[attr-defined]


def test_the_verdict_tool_reaches_the_api_strict_with_closed_objects():
    bound = _model("claude-sonnet-5-5", "medium").bind_tools(agents._TOOLS, tool_choice="auto")
    sent = {tool["name"]: tool for tool in bound.kwargs["tools"]}  # type: ignore[attr-defined]
    verdicts = sent["submit_verdicts"]
    assert verdicts["strict"] is True
    assert verdicts["input_schema"]["additionalProperties"] is False
    assert verdicts["input_schema"]["properties"]["verdicts"]["items"]["additionalProperties"] is False
    assert "strict" not in sent["submit_hypotheses"]
    assert bound.kwargs["tool_choice"] == {"type": "auto"}  # type: ignore[attr-defined]


# --- refusals ------------------------------------------------------------------------------------


def test_a_refusal_reason_names_its_category():
    assert refusal_reason(_REFUSAL) == (
        "Claude declined to process this content (cyber safety category). Review the document manually."
    )
    assert refusal_reason({"stop_reason": "end_turn"}) is None


async def test_a_refusal_raises_model_refused_from_the_call_itself():
    model = GenericFakeChatModel(
        messages=iter([AIMessage(content="", response_metadata=_REFUSAL)]), callbacks=[_anthropic._refusal_callback],
    )
    with pytest.raises(ModelRefused, match="cyber"):
        await model.ainvoke("hi")


async def test_an_ordinary_reply_passes_the_refusal_check():
    result = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok", response_metadata={"stop_reason": "end_turn"}))]])
    await _anthropic._refusal_callback.on_llm_end(result)


async def test_a_refused_document_fails_with_the_refusal_as_its_reason():
    reason = refusal_reason(_REFUSAL)
    with patch.object(pipeline.nodes, "fail_document", AsyncMock()) as fail:
        await pipeline._fail_after_error("doc-1", ModelRefused(reason))
    assert fail.await_args.args == ("doc-1", reason)


async def test_a_specialist_refusal_ends_its_review_with_the_reason_and_does_not_raise():
    async def refuse():
        raise ModelRefused("Claude declined to process this content. Review the document manually.")

    reply = await specialists._invoke(refuse)
    report = specialists._collect_node({"specialist": "layout_rendering", "brief": "b", "messages": [reply]})["report"]
    assert report["uncertain"] is True
    assert report["uncertainty_reason"] == "Claude declined to process this content. Review the document manually."


# --- the cross-document agent asks for its tool ----------------------------------------------------


def _agent_model(*replies: AIMessage) -> MagicMock:
    model = MagicMock()
    model.bind_tools.return_value = model
    model.ainvoke = AsyncMock(side_effect=list(replies))
    return model


_CALL = AIMessage(content="", tool_calls=[{"name": "submit_hypotheses", "args": {"hypotheses": []}, "id": "t1"}])


async def _ask(model: MagicMock):
    with patch.object(agents, "build_chat_anthropic", return_value=model):
        return await agents._ask_for_tool(
            CaseContext(case_id="c", docs={}), [HumanMessage(content="plan")], agents._PLAN_TOOL, "hypotheses",
            100, "test",
        )


async def test_the_planner_asks_once_more_when_the_reply_calls_no_tool():
    model = _agent_model(AIMessage(content="Nothing looks off to me."), _CALL)
    _, args = await _ask(model)
    assert args == {"hypotheses": []}
    assert model.ainvoke.await_count == 2
    assert "Reply only by calling submit_hypotheses" in model.ainvoke.await_args.args[0][-1].content


async def test_the_planner_does_not_ask_again_when_the_tool_was_called():
    model = _agent_model(_CALL)
    _, args = await _ask(model)
    assert args == {"hypotheses": []}
    assert model.ainvoke.await_count == 1


async def test_the_planner_reads_a_json_reply_without_asking_again():
    model = _agent_model(AIMessage(content='{"hypotheses": []}'))
    _, args = await _ask(model)
    assert args is None
    assert model.ainvoke.await_count == 1


async def test_the_planner_gives_up_after_one_retry():
    model = _agent_model(AIMessage(content="no"), AIMessage(content="still no"))
    _, args = await _ask(model)
    assert args is None
    assert model.ainvoke.await_count == 2
