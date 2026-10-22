"""Claude cost from a message's usage (app/pricing.py), which the cost dashboards rely on."""

from langchain_core.messages import AIMessage

from app import pricing


def _message(model: str, input_tokens: int, output: int, cache_read: int = 0, cache_creation: int = 0) -> AIMessage:
    return AIMessage(content="", response_metadata={"model_name": model}, usage_metadata={
        "input_tokens": input_tokens, "output_tokens": output, "total_tokens": input_tokens + output,
        "input_token_details": {"cache_read": cache_read, "cache_creation": cache_creation},
    })


def test_usage_separates_cached_input():
    # langchain-anthropic's input_tokens includes cache reads and writes.
    used = pricing.usage(_message("claude-sonnet-4-6", 5500, 200, cache_read=4000, cache_creation=500))
    assert used == pricing.Usage("claude-sonnet-4-6", 1000, 200, 4000, 500)
    assert used.total_input == 5500


def test_cost_at_list_price_and_cache_savings():
    cost = pricing.cost("claude-sonnet-4-6", 1000, 200, 4000, 500)
    assert round(cost.usd, 6) == 0.009075
    assert round(cost.saved_by_cache_usd, 6) == 0.0108


def test_longest_prefix_wins():
    assert pricing.price_for("claude-opus-4-5-20251101").input == 5
    assert pricing.price_for("claude-opus-4-1").input == 15


def test_unknown_model_costs_nothing():
    assert pricing.cost("some-other-model", 1000, 1000, 0, 0).usd == 0


def test_message_without_usage():
    assert pricing.usage(AIMessage(content="")) is None


def test_case_cost_adds_up_runs_per_document_and_case():
    from unittest.mock import MagicMock, patch

    from app import audit
    from app.events import Event, types

    def row(type: str, document_id: str | None, run: str, usd: float | None) -> dict:
        detail = {"run": run} if usd is None else {"run": run, "llm_cost_usd": usd, "llm_calls": 2}
        return Event(type=type, case_id="case-1", document_id=document_id, detail=detail).to_audit_row()

    rows = [
        row(types.DOCUMENT_ANALYZED, "doc-1", "document", 0.02),
        row(types.DOCUMENT_DETECTED, "doc-1", "detection", 0.05),
        row(types.DOCUMENT_ANALYZED, "doc-2", "document", 0.01),
        row(types.DOCUMENT_ANALYZED, "doc-3", "document", None),  # from before events carried cost
        row(types.CASE_ANALYSIS_COMPLETED, None, "case_analysis", 0.04),
    ]
    client = MagicMock()
    client.table.return_value.select.return_value.eq.return_value.in_.return_value.order.return_value \
        .limit.return_value.execute.return_value.data = rows
    with patch("app.audit.service_supabase_client", return_value=client):
        summary = audit.case_cost("case-1")
    assert summary["total_usd"] == 0.12
    assert summary["documents_usd"] == {"doc-1": 0.07, "doc-2": 0.01}
    assert summary["case_analysis_usd"] == 0.04
    assert len(summary["runs"]) == 4
