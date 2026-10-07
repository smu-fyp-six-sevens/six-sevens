"""The numerical binder: one model call that fills in check roles, and its failure handling."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from app.fraud_detection.font_consistency import ExtractedField
from app.langgraph.detection.binder import bind_numerical_checks

FIELDS = [
    ExtractedField(field_name="turnover", field_type="number", field_value="1000", confidence=0.95),
    ExtractedField(field_name="income_statement_items", field_type="array",
                   field_value=[{"label": "Cost of sales", "amount": -400}], confidence=0.95),
]


def _model(reply: str | Exception):
    model = MagicMock()
    if isinstance(reply, Exception):
        model.ainvoke = AsyncMock(side_effect=reply)
    else:
        model.ainvoke = AsyncMock(return_value=AIMessage(content=reply))
    return model


@pytest.mark.asyncio
async def test_bindings_are_parsed_from_the_reply() -> None:
    reply = ('{"bindings": [{"role": "revenue", "field": "turnover", "row": null},'
             ' {"role": "expense_line", "field": "income_statement_items", "row": 0}]}')
    with patch("app.langgraph.detection.binder.build_chat_anthropic", return_value=_model(reply)):
        result = await bind_numerical_checks("source_of_wealth", FIELDS)

    assert result.error is None
    assert [(b.role, b.field, b.row) for b in result.bindings] == [
        ("revenue", "turnover", None),
        ("expense_line", "income_statement_items", 0),
    ]


@pytest.mark.asyncio
async def test_no_fields_means_no_model_call() -> None:
    with patch("app.langgraph.detection.binder.build_chat_anthropic") as build:
        result = await bind_numerical_checks("source_of_wealth", [])

    build.assert_not_called()
    assert result.bindings == []
    assert result.error is None


@pytest.mark.asyncio
async def test_a_failed_call_gives_no_bindings_and_an_error() -> None:
    with patch("app.langgraph.detection.binder.build_chat_anthropic",
               return_value=_model(RuntimeError("api down"))):
        result = await bind_numerical_checks("source_of_wealth", FIELDS)

    assert result.bindings == []
    assert "api down" in result.error


@pytest.mark.asyncio
async def test_a_reply_that_is_not_json_gives_no_bindings() -> None:
    with patch("app.langgraph.detection.binder.build_chat_anthropic", return_value=_model("sorry, no")):
        result = await bind_numerical_checks("source_of_wealth", FIELDS)

    assert result.bindings == []
    assert "not the expected JSON" in result.error


@pytest.mark.asyncio
async def test_a_malformed_entry_is_dropped_and_the_rest_kept() -> None:
    reply = '{"bindings": [{"role": "revenue"}, {"role": "revenue", "field": "turnover", "row": "two"}]}'
    with patch("app.langgraph.detection.binder.build_chat_anthropic", return_value=_model(reply)):
        result = await bind_numerical_checks("source_of_wealth", FIELDS)

    assert [(b.role, b.field, b.row) for b in result.bindings] == [("revenue", "turnover", None)]
    assert "malformed" in result.error
