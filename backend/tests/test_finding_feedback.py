"""
Officer feedback for the detection specialists: how it is saved (scrubbed, bias-screened), counted (feedback_counts,
answered by the fake), matched on check code and attached to the tool result that fired it, and recorded per finding.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph.message import add_messages

from app import feedback_repository
from app.feedback_repository import FeedbackGroup, FeedbackItem
from app.langgraph.detection import feedback as detection_feedback
from app.langgraph.detection import specialists
from app.main import app
from app.models import CaseRecord
from tests.support.fake_supabase import FakeSupabase
from tests.test_finding_reviews import _TIMESTAMP

client = TestClient(app)


def _item(n: int, **overrides) -> FeedbackItem:
    return FeedbackItem(**{
        "id": f"00000000-0000-0000-0000-{n:012d}", "case_id": f"case-{n}", "scope": "document", "kind": "verdict",
        "check_name": "metadata_tool", "verdict": "wrong", "reason": "tool_misread", "summary": "Author edited",
        "reviewer": "officer", "updated_at": "2026-10-23T02:00:00+00:00", **overrides,
    })


def _group(code: str | None, lesson: str = "confirmed", tool: str = "numerical_checks_tool", **overrides) -> FeedbackGroup:
    return FeedbackGroup(**{
        "check_name": tool, "check_code": code, "lesson": lesson, "cases": 2, "ids": [f"id-{code or tool}-{lesson}"],
        "latest": "2026-10-23T02:00:00+00:00", **overrides,
    })


def _row(group: FeedbackGroup) -> dict:
    """A feedback_counts row, as the database returns it."""
    return group.model_dump()


def _fake(rows: list[dict]) -> FakeSupabase:
    fake = FakeSupabase(keys={"finding_feedback": ("source_key",)})
    fake.functions["feedback_counts"] = lambda params: rows
    return fake


def _result(tool: str, *codes: str | None) -> ToolMessage:
    findings = [{"id": f"{tool}-{n}", "summary": "s", **({"check_code": c} if c else {})} for n, c in enumerate(codes, 1)]
    return ToolMessage(content=json.dumps({"tool": tool, "findings": findings}), tool_call_id=f"call-{tool}", name=tool, id=f"msg-{tool}")


def _feedback_text(message: ToolMessage) -> str:
    return message.content[1]["text"] if isinstance(message.content, list) else ""


# --- saving ---------------------------------------------------------------------------------------------------------


def _save(**overrides) -> bool:
    return feedback_repository.save(**{
        "source_key": "v:case-1:doc-1:fp", "case_id": "case-1", "document_id": "doc-1", "scope": "document",
        "kind": "verdict", "check_name": "numerical_checks_tool", "check_code": "PAY.NET", "document_type": "payslip",
        "verdict": "wrong", "reason": "other", "summary": "Net pay is off", "note": "Rounded by payroll",
        "reviewer": "officer", "names": [], **overrides,
    })


def test_feedback_is_saved_for_the_agents() -> None:
    fake = _fake([])
    with patch.object(feedback_repository, "service_supabase_client", return_value=fake):
        assert _save() is True
    [row] = fake.rows("finding_feedback")
    assert row["check_code"] == "PAY.NET" and row["note"] == "Rounded by payroll"


def test_a_note_touching_a_protected_attribute_is_not_saved_and_replaces_nothing() -> None:
    fake = _fake([])
    with patch.object(feedback_repository, "service_supabase_client", return_value=fake):
        _save()
        # The officer re-saves the same verdict with a biased note: the earlier, clean item goes too.
        assert _save(note="Normal for a foreign applicant") is False
    assert fake.rows("finding_feedback") == []


def test_a_dismissed_word_is_kept_under_its_own_code_unless_it_names_a_party_to_the_case() -> None:
    spelling = {"check_name": "spelling_tool", "summary": 'Possible misspelling "Shenton"', "note": None}
    fake = _fake([])
    with patch.object(feedback_repository, "service_supabase_client", return_value=fake):
        assert _save(**spelling, check_code="SPELLING.shenton") is True
        # The word can't be scrubbed out of the code without losing what it matches on, so it isn't kept.
        assert _save(**spelling, source_key="v:case-1:doc-1:fp2", check_code="SPELLING.tanaka", names=["Tanaka Holdings", "Tanaka"]) is False
        # A fixed code is never screened as words, even when a name happens to be inside it.
        assert _save(source_key="v:case-1:doc-1:fp3", check_code="META.CONTENT_EDITOR", names=["Ten"]) is True
    assert [row["check_code"] for row in fake.rows("finding_feedback")] == ["SPELLING.shenton", "META.CONTENT_EDITOR"]


# --- counting -------------------------------------------------------------------------------------------------------


def _counts(rows: list[dict]) -> list[FeedbackGroup]:
    fake = _fake(rows)
    with patch.object(feedback_repository, "service_supabase_client", return_value=fake):
        groups = feedback_repository.counts(["numerical_checks_tool"], "document", "case-9")
    assert fake.rpc_calls == [("feedback_counts", {
        "p_scope": "document", "p_check_names": ["numerical_checks_tool"], "p_exclude_case_id": "case-9",
    })]
    return groups


def test_a_false_alarm_from_a_single_case_is_not_shown() -> None:
    assert _counts([_row(_group("STAT.LAST_TWO_DIGITS", "false_alarm", cases=1))]) == []


def test_a_false_alarm_two_cases_agree_on_is_shown() -> None:
    [group] = _counts([_row(_group("STAT.LAST_TWO_DIGITS", "false_alarm", cases=2))])
    assert group.key == "STAT.LAST_TWO_DIGITS"


def test_a_single_confirmation_or_missed_issue_is_shown() -> None:
    groups = _counts([_row(_group("PAY.NET", "confirmed", cases=1)), _row(_group(None, "missed", cases=1))])
    assert [g.key for g in groups] == ["PAY.NET", "numerical_checks_tool"]


def test_notes_touching_a_protected_attribute_are_screened_when_read() -> None:
    [group] = _counts([_row(_group(
        "PAY.NET", notes=["Normal for a foreign applicant", "Rounded by payroll", "Third", "Fourth"],
        summaries=["The director's age looks off", "Net pay is off"],
    ))])
    assert group.notes == ["Rounded by payroll", "Third"]
    assert group.summaries == ["Net pay is off"]


def test_a_lesson_reads_as_counts_with_reasons_and_notes() -> None:
    line = feedback_repository.describe(_group(
        "STAT.LAST_TWO_DIGITS", "false_alarm", cases=6, reasons={"normal_for_document": 5, "other": 1},
        notes=["Normal for this bank"],
    ), "this check")
    assert line == (
        '- Officers found it a false alarm in 6 cases (Normal for this kind of document in 5, Other reason in 1) '
        '— officers\' notes: "Normal for this bank"'
    )
    assert feedback_repository.describe(_group(None, "missed", cases=1), "this check") == (
        "- Officers found an issue this check missed in 1 case"
    )


# --- matching on check code -----------------------------------------------------------------------------------------


async def test_a_specialist_reads_feedback_once_for_all_its_tools() -> None:
    groups = [_group("PAY.NET"), _group(None, "missed", tool="external_validation_tool")]
    with patch.object(feedback_repository, "counts", return_value=groups) as counts:
        loaded = await detection_feedback.load(["numerical_checks_tool", "external_validation_tool"], "case-9")
    counts.assert_called_once_with(["numerical_checks_tool", "external_validation_tool"], "document", "case-9")
    assert set(loaded) == {"PAY.NET", "external_validation_tool"}


async def test_a_failed_lookup_gives_no_feedback_instead_of_failing_the_review() -> None:
    with patch.object(feedback_repository, "counts", side_effect=RuntimeError("down")):
        assert await detection_feedback.load(["metadata_tool"], "case-1") == {}


def test_feedback_on_one_check_code_is_never_shown_with_another() -> None:
    loaded = {"PAY.NET": [_group("PAY.NET", "false_alarm")]}
    [message], shown = specialists._with_feedback([_result("numerical_checks_tool", "STAT.ROUND_NUMBERS")], loaded, {})
    assert shown == {} and isinstance(message.content, str)

    [message], shown = specialists._with_feedback([_result("numerical_checks_tool", "PAY.NET")], loaded, {})
    assert "Check PAY.NET:" in _feedback_text(message) and 'source="officer_feedback"' in _feedback_text(message)
    assert shown == {"PAY.NET": ["id-PAY.NET-false_alarm"]}


def test_a_finding_with_no_code_gets_its_tools_feedback() -> None:
    loaded = {"numerical_checks_tool": [_group(None, "missed", notes=["CPF on the bonus was missed"])]}
    [message], shown = specialists._with_feedback([_result("numerical_checks_tool", None)], loaded, {})
    assert "CPF on the bonus was missed" in _feedback_text(message)
    assert specialists._shown({"tool": "numerical_checks_tool"}, {"feedback_shown": shown}) == {
        "feedback_ids": ["id-numerical_checks_tool-missed"],
    }


def test_each_finding_records_only_its_own_codes_feedback() -> None:
    state = {"feedback_shown": {"PAY.NET": ["a"], "STAT.ROUND_NUMBERS": ["b"], "numerical_checks_tool": ["c"]}}
    assert specialists._shown({"tool": "numerical_checks_tool", "check_code": "PAY.NET"}, state) == {"feedback_ids": ["a"]}
    assert specialists._shown({"tool": "numerical_checks_tool", "check_code": "PAY.CPF_RATE"}, state) == {}
    assert specialists._shown({"tool": "spelling_tool"}, state) == {}


def test_a_code_is_shown_once_a_run() -> None:
    loaded = {"PAY.NET": [_group("PAY.NET")]}
    [message], shown = specialists._with_feedback([_result("numerical_checks_tool", "PAY.NET")], loaded, {"PAY.NET": ["x"]})
    assert shown == {} and isinstance(message.content, str)


def test_a_result_with_feedback_still_reads_as_the_tools_result() -> None:
    loaded = {"PAY.NET": [_group("PAY.NET")]}
    [message], _ = specialists._with_feedback([_result("numerical_checks_tool", "PAY.NET")], loaded, {})
    result = specialists._read_tool_message(message)
    assert result is not None and [f.model_dump()["check_code"] for f in result.findings] == ["PAY.NET"]


def test_the_feedback_node_replaces_the_new_results_in_place() -> None:
    call = AIMessage(content="", tool_calls=[{"name": "numerical_checks_tool", "args": {}, "id": "call-numerical_checks_tool"}], id="ai-1")
    messages = add_messages([], [call, _result("numerical_checks_tool", "PAY.NET")])
    state = {"messages": messages, "feedback": {"PAY.NET": [_group("PAY.NET")]}, "feedback_shown": {}}

    update = specialists._feedback_node(state)  # type: ignore[arg-type]

    merged = add_messages(messages, update["messages"])
    assert len(merged) == 2 and "Check PAY.NET:" in _feedback_text(merged[1])
    assert update["feedback_shown"] == {"PAY.NET": ["id-PAY.NET-confirmed"]}


async def test_the_must_run_tools_result_carries_its_feedback_and_the_system_prompt_stays_fixed() -> None:
    model = MagicMock()
    model.bind_tools.return_value = model
    model.ainvoke = AsyncMock(return_value=AIMessage(content="", tool_calls=[{"name": "font_consistency_tool", "args": {}, "id": "c1"}]))
    node = specialists._make_agent_node(specialists.SPECIALISTS["layout_rendering"])
    loaded = {"HT.INVISIBLE": [_group("HT.INVISIBLE", tool="hidden_text_tool", notes=["White text on stamps"])]}
    pre_run = [
        AIMessage(content="", tool_calls=[{"name": "hidden_text_tool", "args": {}, "id": "pre_run_hidden_text_tool"}]),
        _result("hidden_text_tool", "HT.INVISIBLE"),
    ]
    with (
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model),
        patch.object(specialists.detection_tools, "_document", AsyncMock(return_value=SimpleNamespace(case_id="case-9"))),
        patch.object(specialists.detection_tools, "document_fields", AsyncMock(return_value=[])),
        patch.object(specialists.detection_tools, "file_facts", AsyncMock(return_value=None)),
        patch.object(specialists, "_pre_run", AsyncMock(return_value=pre_run)),
        patch.object(detection_feedback, "load", AsyncMock(return_value=loaded)) as load,
    ):
        update = await node({"document_id": "doc-fb", "specialist": "layout_rendering", "brief": "b", "document_type": "payslip"})

    assert load.await_args.args[1] == "case-9"  # other cases only: the lookup is told this one
    sent = model.ainvoke.call_args.args[0]
    assert "officer_feedback" not in "".join(block["text"] for block in sent[0].content)
    assert "White text on stamps" in _feedback_text(sent[-1])
    assert update["feedback_shown"] == {"HT.INVISIBLE": ["id-HT.INVISIBLE-confirmed"]}
    assert update["feedback"] == loaded


# --- opening feedback -----------------------------------------------------------------------------------------------


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
