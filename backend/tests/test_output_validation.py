"""Model replies and tool results that are malformed must be repaired, defaulted or recorded, never raised."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Annotated
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from pydantic import BaseModel
from typing_extensions import TypedDict

from app.case_pipeline.risk import outlook
from app.langgraph import extract
from app.langgraph.detection import graph as detection_graph
from app.langgraph.detection import specialists
from app.langgraph.detection_report import read_report
from app import run_context
from app.llm_contracts import EvidenceRequests, LeadPlan, SpecialistAssessment, ToolResult
from app.llm_io import message_text, parse_json_object, salvage_list, validate
from app.models import CaseRisk, ContributingFinding

# --- reading a reply ---------------------------------------------------------------------------

REPLIES = [
    ("", None),
    ("no json here", None),
    ("[1, 2, 3]", None),
    ('"just a string"', None),
    ("null", None),
    ('{"a": 1', None),
    ('{"a": 1}', {"a": 1}),
    ('```json\n{"a": 1}\n```', {"a": 1}),
    ('Here you go: {"a": 1} hope that helps', {"a": 1}),
    ('<think>{"x": 0}</think>{"a": 1}', {"a": 1}),
    ('{"a": 1} then {oops}', {"a": 1}),
]


@pytest.mark.parametrize("text, expected", REPLIES)
def test_parse_json_object(text, expected) -> None:
    assert parse_json_object(text) == expected


@pytest.mark.parametrize(
    "content, expected",
    [("abc", "abc"), (None, ""), (5, ""), ([{"type": "text", "text": "a"}, {"type": "tool_use"}, "b"], "ab")],
)
def test_message_text(content, expected) -> None:
    assert message_text(content) == expected


def test_salvage_list_keeps_only_the_finished_items() -> None:
    text = '{"assessments": [{"finding_id": "a"}, {"finding_id": "b"}, {"finding_id": "c", "ratio'
    assert salvage_list(text, "assessments") == [{"finding_id": "a"}, {"finding_id": "b"}]


# --- checking it against a contract -------------------------------------------------------------


def test_validate_ok_degraded_and_failed() -> None:
    assert validate(SpecialistAssessment, {"summary": "fine"}).status == "ok"

    degraded = validate(SpecialistAssessment, {"summary": "fine", "uncertain": "maybe", "assessments": "x"})
    assert degraded.status == "degraded" and degraded.value is not None
    assert degraded.value.uncertain is False and degraded.value.assessments == []

    assert validate(SpecialistAssessment, None).status == "failed"
    assert validate(SpecialistAssessment, ["not", "an", "object"]).status == "failed"


def test_validate_drops_items_that_do_not_fit_and_keeps_the_rest() -> None:
    checked = validate(
        SpecialistAssessment,
        {"assessments": [{"finding_id": "a", "dismiss": True}, "junk", 7, {"finding_id": 3, "severity": "HIGH"}]},
    )
    assert checked.value is not None
    assert [a.finding_id for a in checked.value.assessments] == ["a", "3"]


def test_tool_result_gives_every_finding_a_summary() -> None:
    checked = validate(ToolResult, {"tool": "t", "findings": [{"id": "t-1"}, {"summary": "s", "extra": 1}, "bad"]})
    assert checked.value is not None
    first, second = checked.value.findings
    assert first.summary and second.summary == "s"
    assert second.model_dump()["extra"] == 1


def test_lead_plan_keeps_only_text_briefs() -> None:
    plan = validate(LeadPlan, {"briefs": {"a": "look at fonts", "b": 5, "c": "  "}}).value
    assert plan is not None and plan.briefs == {"a": "look at fonts"}
    assert validate(LeadPlan, {"briefs": ["x"]}).value == LeadPlan()


# --- extraction ---------------------------------------------------------------------------------


@pytest.mark.parametrize("reply", ["[1, 2]", '"text"', "null", "", "{broken"])
def test_extraction_that_is_not_an_object_fails_with_a_reason(reply) -> None:
    result = extract.parse_extraction_output(reply)
    assert result["extracted_fields"] == {} and result["confidence"] == 0.0
    assert result["output_error"] and result["escalation"] is None


def test_extraction_reads_json_with_prose_around_it() -> None:
    result = extract.parse_extraction_output('Sure! {"extracted_fields": {"a": {"value": 1, "confidence": 0.9}}} Done.')
    assert result["extracted_fields"] == {"a": 1}


def test_extraction_escalation_is_always_an_object_or_none() -> None:
    wrong_type = extract.parse_extraction_output('{"extracted_fields": {}, "escalation": "wrong type"}')
    assert wrong_type["escalation"] == {"reason": "wrong type"}
    assert extract.parse_extraction_output('{"extracted_fields": {}, "escalation": 5}')["escalation"] is None
    assert extract.parse_extraction_output('{"extracted_fields": {}}')["escalation"] is None


def test_extraction_cut_off_says_so() -> None:
    result = extract.parse_extraction_output('{"extracted_fields": {"a": ', cut_off=True)
    assert "cut off" in result["output_error"]


# --- a specialist reading tool results and its own reply ----------------------------------------


def _collect(*tool_messages: ToolMessage, final: AIMessage | None = None) -> dict:
    messages = [
        AIMessage(content="", tool_calls=[{"name": "spelling_tool", "args": {}, "id": "call-1"}]),
        *tool_messages,
        final or AIMessage(content=json.dumps({"summary": "done", "assessments": []})),
    ]
    return specialists._collect_node({"messages": messages, "specialist": "content_validation"})["report"]


def _tool(content: str, *, status: str = "success") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="call-1", name="spelling_tool", status=status)


@pytest.mark.parametrize(
    "content, status",
    [
        ("not json at all", "success"),
        ("[1, 2]", "success"),
        ('"text"', "success"),
        ("", "success"),
        ('{"findings": []}', "error"),
    ],
)
def test_a_tool_result_that_is_unreadable_is_unable_to_assess(content, status) -> None:
    report = _collect(_tool(content, status=status))
    assert [u["tool"] for u in report["unable_to_assess"]] == ["spelling_tool"]
    assert report["findings"] == []


def test_findings_in_an_unreadable_form_make_the_check_unassessed() -> None:
    good = {"id": "spelling_tool-1", "summary": "Possible misspelling"}
    report = _collect(_tool(json.dumps({"tool": "spelling_tool", "findings": [good, "junk", 4]})))
    assert [f["id"] for f in report["findings"]] == ["spelling_tool-1"]
    assert "2 finding(s)" in report["unable_to_assess"][0]["reason"]


def test_a_finding_without_an_id_or_summary_is_kept() -> None:
    report = _collect(_tool(json.dumps({"tool": "spelling_tool", "findings": [{"word": "teh"}]})))
    [finding] = report["findings"]
    assert finding["id"] == "spelling_tool-1" and finding["summary"] and finding["word"] == "teh"


@pytest.mark.parametrize("final", ["", "I looked and all is well.", "[1]"])
def test_a_specialist_reply_that_is_unreadable_leaves_findings_unapproved(final) -> None:
    finding = {"id": "spelling_tool-1", "summary": "Possible misspelling"}
    report = _collect(
        _tool(json.dumps({"tool": "spelling_tool", "findings": [finding]})), final=AIMessage(content=final)
    )
    assert report["uncertain"] is True
    [kept] = report["findings"]
    assert kept["ai_accepted"] is False and kept["severity"] == "medium"


def test_a_specialist_reply_with_unusable_assessments_approves_nothing() -> None:
    finding = {"id": "spelling_tool-1", "summary": "Possible misspelling"}
    report = _collect(
        _tool(json.dumps({"tool": "spelling_tool", "findings": [finding]})),
        final=AIMessage(content='{"assessments": "x"}'),
    )
    [kept] = report["findings"]
    assert kept["ai_accepted"] is False and kept["severity"] == "medium"


def test_a_specialist_reply_cut_off_keeps_the_assessments_that_finished() -> None:
    findings = [{"id": f"spelling_tool-{n}", "summary": "x"} for n in (1, 2)]
    cut = (
        '{"summary": "s", "assessments": [{"finding_id": "spelling_tool-1", "severity": "high", '
        '"rationale": "r"}, {"finding_id": "spel'
    )
    report = _collect(
        _tool(json.dumps({"tool": "spelling_tool", "findings": findings})), final=AIMessage(content=cut)
    )
    by_id = {f["id"]: f for f in report["findings"]}
    assert by_id["spelling_tool-1"]["severity"] == "high" and by_id["spelling_tool-1"]["ai_accepted"] is True
    assert by_id["spelling_tool-2"]["ai_accepted"] is False
    assert report["uncertain"] is True and "cut off" in report["uncertainty_reason"]
    assert report["output_status"] == "salvaged"


def test_a_garbage_run_never_reads_as_clean() -> None:
    report = _collect(_tool("garbage"), final=AIMessage(content="???"))
    state = {"reports": [report], "document_id": "d", "context": {"document_type": "payslip"}}
    assert detection_graph.merge_reports(state)["verdict"] != "clean"  # type: ignore[arg-type]


# --- the lead and the evidence requests ---------------------------------------------------------


async def test_a_failing_lead_gives_every_specialist_the_default_brief() -> None:
    model = MagicMock()
    model.ainvoke = AsyncMock(side_effect=RuntimeError("provider down"))
    with patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model), patch.object(detection_graph, "trace"):
        result = await detection_graph.lead_plan_node({"document_id": "d", "context": {"document_type": "payslip"}})
    briefs = result["plan"]["briefs"]
    assert set(briefs) == set(specialists.SPECIALISTS) and all(briefs.values())


async def test_a_lead_reply_that_is_not_json_gives_default_briefs_and_no_second_look() -> None:
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content="I would rather not."))
    state = {"document_id": "d", "context": {"document_type": "payslip"}, "reports": []}
    with patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model), patch.object(detection_graph, "trace"):
        plan = await detection_graph.lead_plan_node(state)  # type: ignore[arg-type]
        review = await detection_graph.lead_review_node(state)  # type: ignore[arg-type]
    assert all(plan["plan"]["briefs"].values())
    assert review == {
        "lead_summary": "",
        "agent_decisions": [{
            "agent": "lead_reviewer",
            "decision_id": "review_0",
            "decision": "no second look requested",
            "confidence": 0.0,
        }],
    }


def _risk() -> CaseRisk:
    finding = ContributingFinding(
        document_id="d", filename="f.pdf", finding_id="f1", fingerprint="f1", severity="medium", summary="s", rationale="r",
        points=5, category="cross_document_variance",
    )
    return CaseRisk(state="scored", level="Medium", score=40, points=5, contributing_findings=[finding])


def _requests_model(response=None, error: Exception | None = None):
    bound = MagicMock()
    bound.ainvoke = AsyncMock(return_value=response, side_effect=error)
    model = MagicMock()
    model.bind_tools.return_value = bound
    return model


async def test_evidence_requests_failing_gives_no_requests() -> None:
    with patch.object(outlook, "build_chat_anthropic", return_value=_requests_model(error=RuntimeError("down"))):
        assert (await outlook.propose_requests_with_status(_risk()))[0] == []


@pytest.mark.parametrize("text", ["", "no requests", "[1]", '{"requests": "x"}', '{"requests": [{"title": ""}]}'])
async def test_evidence_requests_unreadable_gives_no_requests(text) -> None:
    with patch.object(outlook, "build_chat_anthropic", return_value=_requests_model(AIMessage(content=text))):
        assert (await outlook.propose_requests_with_status(_risk()))[0] == []


async def test_evidence_requests_read_from_the_forced_tool_call() -> None:
    reply = AIMessage(content="", tool_calls=[{
        "name": "submit_requests", "id": "c1",
        "args": {"requests": [
            {"title": "Tenancy agreement", "addresses": ["f1"], "expected_effect": "partial"},
            {"title": "", "addresses": ["f1"]},
            "junk",
        ]},
    }])
    with patch.object(outlook, "build_chat_anthropic", return_value=_requests_model(reply)):
        [request] = (await outlook.propose_requests_with_status(_risk()))[0]
    assert request.title == "Tenancy agreement" and request.addresses == ["f1"]
    assert request.expected_effect == "partial"


async def test_evidence_requests_fall_back_to_text_and_cut_off_replies() -> None:
    cut = '{"requests": [{"title": "Board resolution", "addresses": ["f1"]}, {"title": "Aud'
    with patch.object(outlook, "build_chat_anthropic", return_value=_requests_model(AIMessage(content=cut))):
        [request] = (await outlook.propose_requests_with_status(_risk()))[0]
    assert request.title == "Board resolution" and request.expected_effect == "full"


def test_evidence_requests_contract_requires_a_title() -> None:
    assert validate(EvidenceRequests, {"requests": [{"what_it_shows": "x"}]}).value == EvidenceRequests()


# --- reading a stored report ---------------------------------------------------------------------


def test_an_old_or_damaged_report_is_readable() -> None:
    assert read_report(None) is None and read_report([1]) is None and read_report("x") is None

    report = read_report(
        {"findings": [{"id": "a"}, "junk", {"severity": "weird", "summary": 3}], "unable_to_assess": None}
    )
    assert report is not None
    assert report["verdict"] == "inconclusive"
    assert report["schema_version"] == 0
    assert report["unable_to_assess"] == [] and report["consulted"] == []
    assert [f["severity"] for f in report["findings"]] == ["medium", "medium"]
    assert all(isinstance(f["summary"], str) for f in report["findings"])


def test_a_current_report_is_unchanged() -> None:
    raw = {
        "verdict": "flagged", "summary": "s", "schema_version": 1,
        "findings": [{"id": "a", "tool": "t", "severity": "high", "summary": "x", "rationale": "y", "extra": 1}],
        "unable_to_assess": [], "nothing_to_check": [], "consulted": [], "dismissed": [],
    }
    assert read_report(raw) == raw


# --- numbers are never made up -------------------------------------------------------------------


def test_contracts_carry_no_financial_amounts() -> None:
    """A missing or unparseable amount must stay missing (unknown), never become 0. The contracts hold
    text, flags and lists; the only numbers are counters and a confidence."""
    import typing

    from app import llm_contracts

    # Counters, and the classifier's own 0-to-1 confidence in its answer (clamped; unreadable becomes 0).
    counters = {
        ("ToolResult", "truncated"),
        ("ToolResult", "dropped_findings"),
        ("ClassifierReply", "confidence"),
        ("LeadPlan", "confidence"),
        ("LeadReview", "confidence"),
        ("AssessmentItem", "confidence"),
        ("SpecialistAssessment", "confidence"),
    }
    for name, model in vars(llm_contracts).items():
        if not (isinstance(model, type) and issubclass(model, BaseModel) and model is not BaseModel):
            continue
        for field_name, info in model.model_fields.items():
            if (name, field_name) in counters:
                continue
            types = {typing.get_origin(a) or a for a in (info.annotation, *typing.get_args(info.annotation))}
            assert not types & {int, float, Decimal}, f"{name}.{field_name} would coerce a number"


def test_a_tool_finding_keeps_its_own_values_exactly() -> None:
    finding = {"id": "t-1", "summary": "s", "stated": "N/A", "amount": 0, "ratio": None, "total": "1,200.50"}
    checked = validate(ToolResult, {"tool": "t", "findings": [finding]})
    assert checked.value is not None
    kept = checked.value.findings[0].model_dump()
    assert kept["stated"] == "N/A" and kept["amount"] == 0 and kept["ratio"] is None and kept["total"] == "1,200.50"
    assert isinstance(kept["amount"], int)


def test_extraction_does_not_turn_an_unreadable_amount_into_zero() -> None:
    result = extract.parse_extraction_output(
        '{"extracted_fields": {"revenue": {"value": null, "confidence": 0.2}, "profit": {"value": "N/A", "confidence": 0.3}}}'
    )
    assert result["extracted_fields"]["revenue"] is None
    assert result["extracted_fields"]["profit"] == "N/A"


# --- the run's call ceiling ----------------------------------------------------------------------


def test_the_run_context_stops_spending_at_its_ceiling() -> None:
    with run_context.run_context(2) as run:
        run_context.spend_model_call("a")
        run_context.spend_model_call("b")
        with pytest.raises(run_context.RunBudgetExceeded):
            run_context.spend_model_call("c")
        assert run.calls == 2 and run.exhausted


def test_outside_a_run_there_is_no_ceiling() -> None:
    for _ in range(100):
        run_context.spend_model_call("anything")

    # Nothing was counted anywhere: a run started afterwards begins at zero.
    with run_context.run_context(5) as context:
        assert context.calls == 0


async def test_parallel_tasks_share_one_run_ceiling() -> None:
    async def call(_: int) -> bool:
        try:
            run_context.spend_model_call("parallel")
        except run_context.RunBudgetExceeded:
            return False
        return True

    with run_context.run_context(5):
        results = await asyncio.gather(*(asyncio.create_task(call(n)) for n in range(12)))
    assert results.count(True) == 5


async def test_a_lead_past_the_ceiling_is_skipped_without_a_call() -> None:
    model = MagicMock()
    model.ainvoke = AsyncMock(return_value=AIMessage(content='{"briefs": {}}'))
    with (
        patch("app.langgraph._anthropic.build_chat_anthropic", return_value=model),
        patch.object(detection_graph, "trace"),
        run_context.run_context(0),
    ):
        result = await detection_graph.lead_plan_node({"document_id": "d", "context": {"document_type": "payslip"}})
    model.ainvoke.assert_not_called()
    assert all(result["plan"]["briefs"].values())


def test_a_specialist_past_the_ceiling_keeps_what_its_tools_found_unapproved() -> None:
    finding = {"id": "spelling_tool-1", "summary": "Possible misspelling"}
    with run_context.run_context(0) as run:
        run.exhausted = True
        report = _collect(
            _tool(json.dumps({"tool": "spelling_tool", "findings": [finding]})), final=AIMessage(content="")
        )
    assert report["uncertain"] is True and "ceiling" in report["uncertainty_reason"]
    assert [f["ai_accepted"] for f in report["findings"]] == [False]


# --- the audit record of repaired output ---------------------------------------------------------


def test_a_repaired_output_is_recorded_with_the_raw_reply() -> None:
    raw = '{"summary": "s", "assessments": [{"finding_id": "a"}, "junk"], "uncertain": "maybe"}'
    with run_context.run_context() as run:
        checked = validate(SpecialistAssessment, parse_json_object(raw), site="test site", raw=raw)
    assert checked.status == "degraded"
    [entry] = run.repairs
    assert entry["site"] == "test site" and entry["status"] == "degraded"
    assert entry["raw"] == raw
    assert '"finding_id": "a"' in entry["used"]
    assert any("uncertain" in issue for issue in entry["issues"])
    assert any("unreadable" in issue for issue in entry["issues"])


def test_an_output_that_fits_leaves_no_audit_entry() -> None:
    with run_context.run_context() as run:
        assert validate(SpecialistAssessment, {"summary": "fine", "assessments": []}).status == "ok"
    assert run.repairs == []


def test_a_list_that_was_not_a_list_is_not_silently_ok() -> None:
    checked = validate(SpecialistAssessment, {"summary": "s", "assessments": "x"})
    assert checked.status == "degraded"


# --- tool time limits ----------------------------------------------------------------------------


class ToolState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    document_id: str


async def test_a_tool_that_hangs_ends_as_unable_to_assess() -> None:
    from app.langgraph.detection import tools as detection_tools
    from app.langgraph.detection.tool_timeout import guard_tool

    async def hang(*_args, **_kwargs):
        await asyncio.sleep(30)

    call = AIMessage(content="", tool_calls=[{"name": "spelling_tool", "args": {}, "id": "c1"}])

    with (
        patch.object(detection_tools, "_document", hang),
        patch("app.langgraph.detection.tool_timeout.settings") as limits,
    ):
        limits.TOOL_TIMEOUT_SECONDS = limits.SLOW_TOOL_TIMEOUT_SECONDS = 0.05
        tool = detection_tools.spelling_tool.model_copy()  # its own copy, so the shared tool is left alone
        graph = StateGraph(ToolState)
        graph.add_node("tools", ToolNode([guard_tool(tool)], handle_tool_errors=True))
        graph.set_entry_point("tools")
        graph.add_edge("tools", END)
        out = await asyncio.wait_for(graph.compile().ainvoke({"messages": [call], "document_id": "d"}), timeout=5)
    result = json.loads(out["messages"][-1].content)
    assert result["unable_to_assess"] is True and "timeout" in result["reason"].lower()


def test_a_failed_call_that_the_specialist_redid_successfully_is_not_unassessed() -> None:
    failed = ToolMessage(content="boom", tool_call_id="call-1", name="numerical_checks_tool", status="error")
    redone = ToolMessage(
        content=json.dumps({"tool": "numerical_checks_tool", "findings": []}), tool_call_id="call-2",
        name="numerical_checks_tool",
    )

    report = _collect(failed, redone)

    assert report["unable_to_assess"] == []


def test_a_failed_call_that_was_not_redone_stays_unassessed_without_internal_markers() -> None:
    failed = ToolMessage(content="boom", tool_call_id="call-1", name="numerical_checks_tool", status="error")

    report = _collect(failed)

    assert [u["tool"] for u in report["unable_to_assess"]] == ["numerical_checks_tool"]
    assert all("_failed_call" not in u for u in report["unable_to_assess"])


# --- a hard breach cannot be dismissed ----------------------------------------------------------------


def _assess(finding: dict, *, dismiss: bool, severity: str = "low") -> dict:
    final = AIMessage(content=json.dumps({"summary": "s", "assessments": [
        {"finding_id": finding["id"], "severity": severity, "dismiss": dismiss, "rationale": "Looks like an extraction error."},
    ]}))
    return _collect(_tool(json.dumps({"tool": "numerical_checks_tool", "findings": [finding]})), final=final)


IMPOSSIBLE = {"id": "numerical_checks_tool-1", "summary": "Net pay 5,462.04, expected 3,462.04", "severity_hint": "impossible", "needs_review": False}


def test_an_impossible_finding_cannot_be_dismissed_and_stays_for_the_officer() -> None:
    report = _assess(IMPOSSIBLE, dismiss=True)

    [kept] = report["findings"]
    assert report["dismissed"] == []
    assert kept["severity"] == "high" and kept["ai_accepted"] is False
    assert kept["rationale"].startswith("Not dismissed: the check shows this value is impossible.")
    assert "extraction error" in kept["rationale"]


@pytest.mark.parametrize("rated", ["info", "low", "medium"])
def test_an_impossible_finding_the_specialist_keeps_is_floored_at_high(rated) -> None:
    report = _assess(IMPOSSIBLE, dismiss=False, severity=rated)

    [kept] = report["findings"]
    assert kept["severity"] == "high" and kept["ai_accepted"] is True


def test_an_impossible_finding_that_needs_review_can_still_be_dismissed() -> None:
    report = _assess({**IMPOSSIBLE, "needs_review": True}, dismiss=True)

    assert report["findings"] == []
    assert [d["id"] for d in report["dismissed"]] == ["numerical_checks_tool-1"]


def test_a_finding_that_is_only_a_signal_can_still_be_dismissed() -> None:
    report = _assess({**IMPOSSIBLE, "severity_hint": "signal"}, dismiss=True)

    assert report["findings"] == []
    assert len(report["dismissed"]) == 1
