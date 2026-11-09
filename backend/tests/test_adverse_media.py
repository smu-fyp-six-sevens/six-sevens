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


# What the searches returned, as the API's web_search_tool_result block lists them.
MAS_PAGE = {"type": "web_search_result", "url": "https://www.mas.gov.sg/news/notice", "title": "MAS notice on Acme"}


def _search(*results: dict) -> dict:
    return {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1", "content": list(results)}


def _search_error(code: str = "too_many_requests") -> dict:
    return {"type": "web_search_tool_result", "tool_use_id": "srvtoolu_1",
            "content": {"type": "web_search_tool_result_error", "error_code": code}}


async def _run(reply: object, company: str = "Acme Trading Pte Ltd", searches: list[dict] | None = None):
    """The check against a model whose searches returned `searches` (one successful search by default)."""
    model = MagicMock()
    model.bind_tools.return_value = model
    text = reply if isinstance(reply, str) else json.dumps(reply)
    blocks = [_search(MAS_PAGE)] if searches is None else searches
    model.ainvoke = AsyncMock(return_value=AIMessage(content=[*blocks, {"type": "text", "text": text}]))
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


async def test_concerns_keep_only_the_cited_pages_the_searches_returned() -> None:
    result, _ = await _run({
        "status": "concerns",
        "summary": "MAS issued an enforcement notice against the company last week.",
        "sources": [
            # Returned by the search: kept, with the page's own title rather than the model's.
            {"title": "Model's wording", "url": "https://www.mas.gov.sg/news/notice/"},
            # Never returned: made up, dropped.
            {"title": "Invented", "url": "https://www.straitstimes.com/made-up-story"},
            {"title": "No scheme link", "url": "javascript:alert(1)"},
        ],
    })

    assert result.status == "concerns"
    assert "enforcement" in result.summary
    assert [(s.title, s.url) for s in result.sources] == [("MAS notice on Acme", "https://www.mas.gov.sg/news/notice")]


async def test_concerns_whose_every_cited_page_is_made_up_are_not_shown() -> None:
    result, _ = await _run({
        "status": "concerns", "summary": "Acme was charged with fraud.",
        "sources": [{"title": "Invented", "url": "https://www.reuters.com/made-up"}],
    })

    # Neither a concern it can't back nor a clean it didn't find.
    assert result.status == "failed"
    assert result.sources == []


async def test_a_check_whose_searches_all_failed_is_failed_not_clean() -> None:
    # Rate-limited searches still come back as a 200, with an error object instead of results.
    result, _ = await _run(
        {"status": "clean", "summary": "Nothing concerning.", "sources": []},
        searches=[_search_error(), _search_error("unavailable")],
    )

    assert result.status == "failed"


async def test_the_search_is_limited_to_the_trusted_sites() -> None:
    _, model = await _run({"status": "clean", "summary": "Nothing came up.", "sources": []})

    [tool] = model.bind_tools.call_args.args[0]
    assert tool["allowed_domains"] == adverse_media.TRUSTED_DOMAINS
    assert "mas.gov.sg" in tool["allowed_domains"]


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


# --- A check is "clean" only when every search ran and the reply says so; anything else is "failed" -------------


async def test_an_unreadable_reply_is_failed_not_clean() -> None:
    result, _ = await _run("the model forgot to answer in json")

    assert (result.status, result.summary) == ("failed", adverse_media.UNREADABLE)


async def test_a_reply_that_does_not_validate_is_failed() -> None:
    # Valid JSON, but not a status the check knows.
    result, _ = await _run({"status": "probably fine", "summary": "Looks ok.", "sources": []})

    assert (result.status, result.summary) == ("failed", adverse_media.UNREADABLE)


async def test_a_clean_reply_with_no_search_behind_it_is_failed() -> None:
    # The model answered from memory: whatever the text says, nothing was looked up.
    result, _ = await _run({"status": "clean", "summary": "Nothing concerning.", "sources": []}, searches=[])

    assert (result.status, result.summary) == ("failed", adverse_media.NO_SEARCH)


async def test_a_clean_reply_with_any_failed_search_is_failed() -> None:
    result, _ = await _run(
        {"status": "clean", "summary": "Nothing concerning.", "sources": []},
        searches=[_search(MAS_PAGE), _search_error()],
    )

    assert (result.status, result.summary) == ("failed", adverse_media.SEARCH_ERRORS)


async def test_a_backed_concern_stands_even_if_another_search_failed() -> None:
    result, _ = await _run(
        {"status": "concerns", "summary": "MAS notice.", "sources": [{"title": "t", "url": MAS_PAGE["url"]}]},
        searches=[_search(MAS_PAGE), _search_error()],
    )

    assert result.status == "concerns"


async def test_asking_for_one_search_too_many_is_not_a_failure() -> None:
    # max_uses_exceeded only means the searches it was allowed all ran.
    result, _ = await _run(
        {"status": "clean", "summary": "Nothing concerning.", "sources": []},
        searches=[_search(MAS_PAGE), _search_error("max_uses_exceeded")],
    )

    assert result.status == "clean"


# --- Former names -----------------------------------------------------------------------------------------------


async def test_former_names_are_given_one_search_within_the_limit() -> None:
    model = MagicMock()
    model.bind_tools.return_value = model
    model.ainvoke = AsyncMock(return_value=AIMessage(content=[
        _search(MAS_PAGE), {"type": "text", "text": json.dumps({"status": "clean", "summary": "", "sources": []})},
    ]))
    with patch.object(adverse_media, "build_chat_anthropic", return_value=model), patch.object(adverse_media, "spend_model_call"):
        result = await adverse_media.run_adverse_media_check("Acme Trading Pte Ltd", ["Zenith Bay Engineering Pte. Ltd."])

    prompt = model.ainvoke.call_args.args[0][0].content
    assert "Zenith Bay Engineering Pte. Ltd." in prompt
    assert "exactly one of your searches on the former names" in prompt
    # The same bad-news search, under the old name: not a search for the rename itself.
    assert '"Zenith Bay Engineering Pte. Ltd." sanctions fraud investigation' in prompt
    # Still capped at the same number of searches in all.
    assert model.bind_tools.call_args.args[0][0]["max_uses"] == adverse_media._MAX_SEARCHES
    assert result.former_names == ["Zenith Bay Engineering Pte. Ltd."]


async def test_without_former_names_the_prompt_does_not_mention_them() -> None:
    _, model = await _run({"status": "clean", "summary": "", "sources": []})

    assert "Former names" not in model.ainvoke.call_args.args[0][0].content


def test_former_names_drop_blanks_repeats_and_the_current_name() -> None:
    names = adverse_media.former_names_of(
        "Acme Trading Pte Ltd",
        ["Zenith Bay Engineering Pte. Ltd.", "ZENITH BAY ENGINEERING PTE LTD", "", None, "Acme Trading Pte. Ltd.", "Old Co; Older Co"],
    )

    assert names == ["Zenith Bay Engineering Pte. Ltd.", "Old Co", "Older Co"]


def test_the_cache_searches_again_once_a_former_name_turns_up() -> None:
    previous, now = _checked(1)

    assert adverse_media.reuse_recent(previous, "Acme Trading Pte Ltd", now, former_names=["Zenith Bay Pte Ltd"]) is None
    searched = previous.model_copy(update={"former_names": ["Zenith Bay Pte Ltd"]})
    assert adverse_media.reuse_recent(searched, "Acme Trading Pte Ltd", now, former_names=["zenith bay pte ltd"]) is not None


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
