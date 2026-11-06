"""
What query_stats records: each Supabase call under a stable label, and each Claude call's tokens and list-price
cost, counted into the unit of work it ran in and labelled by the agent that made it. The audit trail's cost per
document and case, and the dashboard's Claude cost, are read from these figures.
"""
from __future__ import annotations

import uuid

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app import metrics, query_stats, telemetry

MODEL = "claude-sonnet-5-5"  # $2 / $10 / $0.10 per million input, output and cache-read tokens


def response(input_tokens: int = 1500, output_tokens: int = 200, cache_read: int = 1000, *, usage: bool = True) -> LLMResult:
    message = AIMessage(
        content="{}",
        usage_metadata={"input_tokens": input_tokens, "output_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                        "input_token_details": {"cache_read": cache_read}} if usage else None,
        response_metadata={"model_name": MODEL},
    )
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def counted(agent: str, result: str = "ok") -> float:
    return metrics.llm_calls.labels(agent, MODEL if result == "ok" else "unknown", result)._value.get()


async def call_claude(agent: str, result: LLMResult | BaseException) -> None:
    """One model call, as the callback sees it, made inside the agent's span."""
    run_id = uuid.uuid4()
    with telemetry.get_tracer().start_as_current_span(agent):
        await query_stats.llm_callback.on_chat_model_start({}, [], run_id=run_id)
        if isinstance(result, BaseException):
            await query_stats.llm_callback.on_llm_error(result, run_id=run_id)
        else:
            await query_stats.llm_callback.on_llm_end(result, run_id=run_id)


async def test_a_claude_call_is_counted_into_its_unit_with_tokens_and_list_price_cost() -> None:
    before = counted("layout_rendering")

    with query_stats.scope("detection doc-1") as unit:
        await call_claude("layout_rendering", response())

    # 500 uncached x $2 + 200 out x $10 + 1,000 cache reads x $0.10, per million.
    assert unit.llm_detail() == {
        "llm_cost_usd": 0.0031, "llm_calls": 1, "llm_input_tokens": 1500, "llm_output_tokens": 200,
        "llm_cache_read_tokens": 1000, "llm_cache_write_tokens": 0,
    }
    assert counted("layout_rendering") == before + 1
    assert "1 claude (1500 in / 200 out tokens; cache read 1000 / write 0; $0.0031)" in unit.summary()


async def test_a_failed_claude_call_is_counted_as_an_error_of_its_agent_and_costs_nothing() -> None:
    before = counted("lead_detection", "error")

    with query_stats.scope("detection doc-1") as unit:
        await call_claude("lead_detection", RuntimeError("overloaded"))

    assert counted("lead_detection", "error") == before + 1
    assert unit.llm_calls == 0


async def test_a_reply_with_no_usage_is_not_counted() -> None:
    with query_stats.scope("detection doc-1") as unit:
        await call_claude("lead_detection", response(usage=False))

    assert unit.is_empty()


def test_a_unit_of_work_records_its_claude_cost_as_metrics(monkeypatch) -> None:
    recorded: list[tuple] = []
    monkeypatch.setattr(metrics, "record_work", lambda *args: recorded.append(args))

    with query_stats.scope("detection doc-1", work="detection") as unit:
        unit.add_llm(1500, 200, cache_read=1000, cost_usd=0.0031)
        unit.add_llm(800, 100, cost_usd=0.002)

    assert recorded == [("detection", pytest.approx(0.0051), 2, 2300, 1000)]


@pytest.mark.parametrize("url, label", [
    ("https://x.supabase.co/rest/v1/documents?id=eq.1", "GET documents"),
    ("https://x.supabase.co/rest/v1/rpc/sync_findings", "GET rpc"),
    ("https://x.supabase.co/storage/v1/object/documents/case-1/a.pdf", "GET storage"),
    ("https://x.supabase.co/auth/v1/user", "GET /auth/v1/user"),
])
def test_a_supabase_call_is_counted_under_its_table_not_its_full_url(url, label) -> None:
    with query_stats.scope("GET /cases/{id}") as unit:
        query_stats._on_request(httpx.Request("GET", url))

    assert dict(unit.calls) == {label: 1}


def test_a_path_is_labelled_without_its_ids() -> None:
    assert query_stats.label_path("/cases/8f1c2b3a-1111-4222-8333-944455556666/analysis") == "/cases/{id}/analysis"
