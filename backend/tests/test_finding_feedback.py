from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app import feedback_repository
from app.feedback_repository import FeedbackItem
from app.langgraph.detection import feedback as detection_feedback
from app.langgraph.detection import specialists
from app.main import app
from app.models import CaseRecord
from tests.test_finding_reviews import _TIMESTAMP

client = TestClient(app)


def _item(n: int, **overrides) -> FeedbackItem:
    return FeedbackItem(**{
        "id": f"00000000-0000-0000-0000-{n:012d}", "case_id": f"case-{n}", "scope": "document", "kind": "verdict",
        "check_name": "metadata_tool", "verdict": "wrong", "reason": "tool_misread", "summary": "Author edited",
        "reviewer": "officer", "updated_at": "2026-10-23T02:00:00+00:00", **overrides,
    })


def test_at_most_five_items_are_shown() -> None:
    assert len(feedback_repository._pick([_item(n) for n in range(1, 12)])) == 5


def test_a_confirmation_is_included_when_one_exists_even_past_the_newest_five() -> None:
    items = [_item(n) for n in range(1, 9)] + [_item(9, verdict="correct", reason=None)]
    chosen = feedback_repository._pick(items)
    assert len(chosen) == 5
    assert any(i.verdict == "correct" for i in chosen)
    assert [i.id for i in chosen[:4]] == [i.id for i in items[:4]]


def test_no_confirmation_is_invented_when_none_exists() -> None:
    assert not any(i.verdict == "correct" for i in feedback_repository._pick([_item(n) for n in range(1, 9)]))


def test_feedback_is_rendered_as_fenced_data_with_the_reason_and_confirmations() -> None:
    text = detection_feedback.render({"metadata_tool": [
        _item(1), _item(2, verdict="correct", reason=None, summary="Creator tool is an editor"),
        _item(3, kind="missed", verdict=None, reason=None, summary="", note="Stripped metadata"),
    ]})
    assert 'source="officer_feedback"' in text
    assert "FALSE ALARM (The tool misread the document)" in text
    assert "CONFIRMED real" in text and "MISSED by this check" in text and "Stripped metadata" in text
    assert "severity_floor" in text  # feedback cannot lower a floor
    assert detection_feedback.render({}) == ""


async def test_a_failed_lookup_gives_no_feedback_instead_of_failing_the_review() -> None:
    with patch("app.langgraph.detection.feedback.feedback_repository.similar", side_effect=RuntimeError("down")):
        assert await detection_feedback.feedback_for(["metadata_tool"], "case-1", None) == {}


def test_a_finding_says_which_feedback_its_check_was_shown() -> None:
    state = {"feedback_shown": {"metadata_tool": ["a", "b"]}}
    assert specialists._applied({"tool": "metadata_tool"}, state) == {"feedback_ids": ["a", "b"]}
    assert specialists._applied({"tool": "spelling_tool"}, state) == {}


def test_opening_feedback_gives_the_reason_and_the_original_case() -> None:
    with (
        patch("app.routers.finding_reviews.feedback_repository.get_many", return_value=[_item(1)]),
        patch("app.routers.finding_reviews.case_service.get_case") as get_case,
    ):
        get_case.return_value = CaseRecord(
            id="case-1", greenlink_case_id="GLDB-7", created_at=_TIMESTAMP, updated_at=_TIMESTAMP,
        )
        response = client.get("/finding-feedback", params={"ids": ["00000000-0000-0000-0000-000000000001"]})
    assert response.status_code == 200
    [item] = response.json()
    assert item["reason_label"] == "The tool misread the document"
    assert item["greenlink_case_id"] == "GLDB-7" and item["case_id"] == "case-1"


def test_feedback_from_a_deleted_case_still_opens_without_a_link() -> None:
    with (
        patch("app.routers.finding_reviews.feedback_repository.get_many", return_value=[_item(1)]),
        patch("app.routers.finding_reviews.case_service.get_case", return_value=None),
    ):
        [item] = client.get("/finding-feedback", params={"ids": ["00000000-0000-0000-0000-000000000001"]}).json()
    assert item["greenlink_case_id"] is None


async def test_a_specialist_is_shown_feedback_before_its_first_turn_and_records_it() -> None:
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from langchain_core.messages import AIMessage

    from app.langgraph.detection import tools

    model = MagicMock()
    model.bind_tools.return_value = model
    model.ainvoke = AsyncMock(return_value=AIMessage(content="", tool_calls=[{"name": "font_consistency_tool", "args": {}, "id": "c1"}]))
    node = specialists._make_agent_node(specialists.SPECIALISTS["layout_rendering"])
    shown = {"font_consistency_tool": [_item(1, check_name="font_consistency_tool", note="Fonts differ on stamps")]}
    tools.clear_context("doc-fb")
    with (
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model),
        patch.object(tools, "_document", AsyncMock(return_value=SimpleNamespace(case_id="case-9"))),
        patch.object(tools, "document_fields", AsyncMock(return_value=[])),
        patch.object(tools, "file_facts", AsyncMock(return_value=None)),
        patch.object(detection_feedback, "feedback_for", AsyncMock(return_value=shown)) as lookup,
    ):
        update = await node({"document_id": "doc-fb", "specialist": "layout_rendering", "brief": "b", "document_type": "payslip"})

    assert lookup.await_args.args[1:] == ("case-9", "payslip")  # other cases only: the lookup is told this one
    system = model.ainvoke.call_args.args[0][0]
    assert "Fonts differ on stamps" in "".join(block["text"] for block in system.content)
    assert update["feedback_shown"] == {"font_consistency_tool": [shown["font_consistency_tool"][0].id]}
