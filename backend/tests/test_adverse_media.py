"""
The adverse-media check (case_pipeline/adverse_media.py): a web search for recent sanctions, regulatory action or
negative news about the applicant company. It is a due-diligence aid only — it never raises, and an empty company
name, an unreadable reply or a search error all come back as a plain status so the analysis carries on.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import AIMessage

from app.case_pipeline import adverse_media


async def _run(reply: object, company: str = "Acme Trading Pte Ltd"):
    model = MagicMock()
    model.bind_tools.return_value = model
    content = reply if isinstance(reply, str) else json.dumps(reply)
    model.ainvoke = AsyncMock(return_value=AIMessage(content=content))
    with (
        patch.object(adverse_media, "build_chat_anthropic", return_value=model),
        patch.object(adverse_media, "spend_model_call"),
    ):
        return await adverse_media.run_adverse_media_check(company), model


async def test_a_clean_search_reports_clean_and_runs_the_web_search_tool() -> None:
    result, model = await _run({"status": "clean", "summary": "Nothing came up.", "sources": []})

    assert result.status == "clean"
    assert result.query == "Acme Trading Pte Ltd"
    assert result.sources == []
    assert result.checked_at is not None
    # The web_search tool is bound, capped so one check can't run up a bill.
    tools = model.bind_tools.call_args.args[0]
    assert tools[0]["type"] == "web_search_20250305"
    assert tools[0]["max_uses"] == adverse_media._MAX_SEARCHES


async def test_concerns_keep_the_summary_and_only_real_http_sources() -> None:
    result, _ = await _run({
        "status": "concerns",
        "summary": "MAS issued an enforcement notice against the company last week.",
        "sources": [
            {"title": "MAS enforcement", "url": "https://mas.gov.sg/notice"},
            {"title": "No scheme link", "url": "javascript:alert(1)"},
        ],
    })

    assert result.status == "concerns"
    assert "enforcement" in result.summary
    assert [s.url for s in result.sources] == ["https://mas.gov.sg/notice"]


async def test_a_blank_company_name_is_skipped_without_a_model_call() -> None:
    with patch.object(adverse_media, "build_chat_anthropic") as build:
        result = await adverse_media.run_adverse_media_check("   ")

    assert result.status == "skipped"
    build.assert_not_called()


async def test_a_search_error_comes_back_as_failed_not_an_exception() -> None:
    model = MagicMock()
    model.bind_tools.return_value = model
    model.ainvoke = AsyncMock(side_effect=RuntimeError("web search unavailable"))
    with (
        patch.object(adverse_media, "build_chat_anthropic", return_value=model),
        patch.object(adverse_media, "spend_model_call"),
    ):
        result = await adverse_media.run_adverse_media_check("Acme Trading Pte Ltd")

    assert result.status == "failed"
    assert result.query == "Acme Trading Pte Ltd"


async def test_an_unreadable_reply_falls_back_to_clean() -> None:
    result, _ = await _run("the model forgot to answer in json")

    assert result.status == "clean"


def test_the_check_is_stored_with_the_run_and_read_back() -> None:
    """The case views read the run from the table, so a check dropped on save never reaches the frontend."""
    from datetime import datetime, timezone
    from unittest.mock import MagicMock

    from app.case_pipeline import repository
    from app.case_pipeline.models import AdverseMediaCheck, AdverseMediaSource, CaseAnalysis

    check = AdverseMediaCheck(
        status="concerns", query="Acme Trading Pte Ltd", summary="MAS issued a notice.",
        sources=[AdverseMediaSource(title="MAS", url="https://mas.gov.sg/notice")], checked_at=datetime.now(timezone.utc),
    )
    analysis = CaseAnalysis(
        run_id="run-1", case_id="case-1", status="complete", fingerprint="fp",
        started_at=datetime.now(timezone.utc), adverse_media=check,
    )
    client = MagicMock()
    with patch.object(repository, "supabase_client", client), patch.object(repository, "latest_run_id", return_value="run-1"):
        repository.finish_run(analysis)
    saved = client.table.return_value.update.call_args.args[0]["result"]

    row = {"run_id": "run-1", "case_id": "case-1", "status": "complete", "fingerprint": "fp",
           "started_at": analysis.started_at.isoformat(), "result": saved}
    assert repository._to_analysis(row).adverse_media == check


async def test_a_long_summary_is_kept_whole_on_one_line() -> None:
    # Never cut off: a sentence chopped mid-word reads as broken. The prompt is what keeps it short.
    long = "The search found nothing concerning\nabout the company; " * 5
    result, _ = await _run({"status": "clean", "summary": long, "sources": []})

    assert result.summary == " ".join(long.split())
    assert "…" not in result.summary


# --- The 24-hour cache (reuse_recent) and the time limit ---------------------------------------------------------


def _checked(hours_ago: float, *, status: str = "clean", query: str = "Acme Trading Pte Ltd"):
    from datetime import datetime, timedelta, timezone

    from app.case_pipeline.models import AdverseMediaCheck

    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    return AdverseMediaCheck(status=status, query=query, summary="s", checked_at=now - timedelta(hours=hours_ago)), now


def test_a_search_from_earlier_today_is_reused_and_keeps_its_time() -> None:
    previous, now = _checked(3)

    reused = adverse_media.reuse_recent(previous, "acme trading pte ltd", now)

    assert reused is not None and reused.reused
    # Its original time, so the cache runs out a day after the real search, not a day after the last reuse.
    assert reused.checked_at == previous.checked_at


def test_a_search_older_than_the_cache_is_run_again() -> None:
    previous, now = _checked(adverse_media.CACHE_HOURS + 1)

    assert adverse_media.reuse_recent(previous, "Acme Trading Pte Ltd", now) is None


def test_a_renamed_company_is_searched_again() -> None:
    previous, now = _checked(1)

    assert adverse_media.reuse_recent(previous, "Acme Holdings Pte Ltd", now) is None


def test_a_failed_or_skipped_check_is_never_reused() -> None:
    for status in ("failed", "skipped"):
        previous, now = _checked(1, status=status)
        assert adverse_media.reuse_recent(previous, "Acme Trading Pte Ltd", now) is None


async def test_a_search_past_the_time_limit_is_failed_not_waited_on() -> None:
    import asyncio

    async def hangs(_messages):
        await asyncio.sleep(60)

    model = MagicMock()
    model.bind_tools.return_value = model
    model.ainvoke = hangs
    with (
        patch.object(adverse_media, "build_chat_anthropic", return_value=model),
        patch.object(adverse_media, "spend_model_call"),
        patch.object(adverse_media, "_TIMEOUT_SECONDS", 0.05),
    ):
        result = await adverse_media.run_adverse_media_check("Acme Trading Pte Ltd")

    assert result.status == "failed"
