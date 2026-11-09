"""The web search tool's per-search fee is part of a call's cost (pricing.py), so the Claude Cost tab and audit match the bill."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from app import pricing


def _reply(searches: int) -> AIMessage:
    return AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
        response_metadata={"model_name": "claude-haiku-5-5",
                           "usage": {"server_tool_use": {"web_search_requests": searches, "web_fetch_requests": 0}}},
    )


def test_the_searches_a_call_ran_are_read_from_its_usage() -> None:
    assert pricing.usage(_reply(3)).web_searches == 3
    assert pricing.usage(AIMessage(content="ok", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})).web_searches == 0


def test_each_search_adds_one_cent_to_the_cost() -> None:
    tokens_only = pricing.cost("claude-haiku-5-5", 1000, 100, 0, 0)
    with_searches = pricing.cost("claude-haiku-5-5", 1000, 100, 0, 0, web_searches=3)

    assert round(with_searches.usd - tokens_only.usd, 6) == 0.03


def test_an_unpriced_model_still_counts_its_searches() -> None:
    assert pricing.cost("some-unknown-model", 1000, 100, 0, 0, web_searches=2).usd == 0.02
