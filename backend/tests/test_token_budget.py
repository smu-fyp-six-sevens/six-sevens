"""The per-run token budget (run_context.RunContext.max_tokens): counted from each finished call, checked before the next."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app import run_context
from app.langgraph import pipeline
from app.query_stats import llm_callback


def test_a_run_stops_once_it_has_used_its_budget() -> None:
    with run_context.run_context(max_tokens=10_000) as run:
        run_context.spend_model_call("a")
        run_context.add_model_tokens(6_000)
        run_context.spend_model_call("b")  # 6,000 used: still under
        run_context.add_model_tokens(5_000)
        with pytest.raises(run_context.RunBudgetExceeded, match="token budget of 10,000 reached"):
            run_context.spend_model_call("c")
    assert run.calls == 2 and run.tokens == 11_000 and run.exhausted_by == "tokens"
    assert run.limit_reached() == "its token budget of 10,000 tokens"


def test_a_budget_of_one_token_allows_only_the_first_call() -> None:
    with run_context.run_context(max_tokens=1) as run:
        run_context.spend_model_call("first")
        run_context.add_model_tokens(5_213)
        with pytest.raises(run_context.RunBudgetExceeded):
            run_context.spend_model_call("second")
    assert run.calls == 1


def test_the_call_ceiling_still_reports_as_model_calls() -> None:
    with run_context.run_context(1, max_tokens=1_000_000) as run:
        run_context.spend_model_call("a")
        with pytest.raises(run_context.RunBudgetExceeded, match="model call ceiling"):
            run_context.spend_model_call("b")
    assert run.exhausted_by == "model calls"


def test_tokens_outside_a_run_are_ignored() -> None:
    run_context.add_model_tokens(10**9)
    run_context.spend_model_call("anything")


async def test_the_llm_callback_counts_input_and_output_tokens_into_the_run() -> None:
    message = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 1_200, "output_tokens": 300, "total_tokens": 1_500},
        response_metadata={"model_name": "claude-haiku-4-5-20251001"},
    )
    with run_context.run_context(max_tokens=1_000) as run:
        await llm_callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=uuid4())
        with pytest.raises(run_context.RunBudgetExceeded):
            run_context.spend_model_call("next")
    assert run.tokens == 1_500


async def test_a_document_stopped_by_its_budget_fails_with_a_plain_reason() -> None:
    async def graph_that_overspends(*args, **kwargs):
        run_context.spend_model_call("classify agent")
        run_context.add_model_tokens(5_000)
        run_context.spend_model_call("extraction")

    failed = AsyncMock()
    with (
        patch.object(pipeline.settings, "DOCUMENT_MAX_TOKENS", 1),
        patch.object(pipeline, "get_document_graph", return_value=AsyncMock(ainvoke=graph_that_overspends)),
        patch.object(pipeline, "_record_run_audit", AsyncMock()),
        patch.object(pipeline.nodes, "fail_document", failed),
    ):
        await pipeline.process_document("doc-1")

    reason = failed.await_args.args[1]
    assert reason.startswith("Stopped before finishing: the token budget of 1 reached")
    assert "Unhandled" not in reason
