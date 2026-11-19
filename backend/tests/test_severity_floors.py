"""Severity floors (specialists._floor): what the AI may not rate down or dismiss, and the rating it gave kept."""

from __future__ import annotations

import json
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.langgraph.detection import specialists
from app.langgraph.detection.graph import merge_reports

_TOOL = "numerical_checks_tool"
_ID = f"{_TOOL}-1"
RULE_CODES = (
    "PAY.CPF_CEILING", "PAY.CPF_BASE", "PAY.CPF_RATE", "PAY.NET",
    "BS.RUNNING_BALANCE", "BS.OPEN_CLOSE", "ID.UEN_FORMAT", "REG.UEN_EXISTS",
)
RULE_TYPES = ("payslip", "notice_of_assessment", "bank_statement")


def _finding(code: str = "PAY.NET", hint: str = "inconsistent", **extra: Any) -> dict:
    return {"id": _ID, "check_code": code, "severity_hint": hint, "summary": "Net pay doesn't add up", **extra}


def _collect(finding: dict, document_type: str, *, severity: str | None = "low", dismiss: bool = False) -> dict:
    assessment = {"finding_id": finding["id"], "dismiss": dismiss, "severity": severity,
                  "rationale": "Looks like rounding.", "confidence": 0.8}
    messages = [
        AIMessage(content="", tool_calls=[{"name": _TOOL, "args": {}, "id": "call-1"}]),
        ToolMessage(content=json.dumps({"tool": _TOOL, "findings": [finding]}), tool_call_id="call-1", name=_TOOL),
        AIMessage(content=json.dumps({"summary": "done", "assessments": [assessment]})),
    ]
    state = {"messages": messages, "specialist": "content_validation", "document_type": document_type}
    return specialists._collect_node(cast(Any, state))["report"]


# --- (a) a proved-impossible finding stays high ---------------------------------------------------


def test_a_hard_breach_rated_low_is_raised_to_high_and_keeps_the_ai_rating() -> None:
    [kept] = _collect(_finding("PAY.YTD", "impossible"), "nric")["findings"]
    assert kept["severity"] == "high"
    assert kept["agent_severity"] == "low"
    assert kept["severity_floor_reason"] == specialists._HARD_BREACH_REASON


def test_a_dismissed_hard_breach_is_kept_at_high() -> None:
    report = _collect(_finding("PAY.YTD", "impossible"), "nric", severity=None, dismiss=True)
    assert report["dismissed"] == []
    [kept] = report["findings"]
    assert kept["severity"] == "high" and kept["agent_severity"] == "dismissed"
    assert kept["rationale"].startswith("Not dismissed")
    assert kept["ai_accepted"] is False


def test_an_impossible_value_that_needs_review_can_still_be_dismissed() -> None:
    report = _collect(_finding("PAY.YTD", "impossible", needs_review=True), "nric", severity=None, dismiss=True)
    assert report["findings"] == []
    assert [d["id"] for d in report["dismissed"]] == [_ID]


# --- (b) income and balance checks stay at least medium ------------------------------------------


@pytest.mark.parametrize("document_type", RULE_TYPES)
@pytest.mark.parametrize("code", RULE_CODES)
def test_an_income_or_balance_check_rated_low_is_raised_to_medium(code: str, document_type: str) -> None:
    [kept] = _collect(_finding(code), document_type)["findings"]
    assert kept["severity"] == "medium"
    assert kept["agent_severity"] == "low"
    assert kept["severity_floor_reason"] == specialists._RULE_FLOOR_REASON


@pytest.mark.parametrize("code", RULE_CODES)
def test_an_income_or_balance_check_cannot_be_dismissed(code: str) -> None:
    report = _collect(_finding(code), "payslip", severity=None, dismiss=True)
    assert report["dismissed"] == []
    [kept] = report["findings"]
    assert kept["severity"] == "medium" and kept["agent_severity"] == "dismissed"


def test_a_rating_above_the_floor_stands_with_no_note() -> None:
    [kept] = _collect(_finding("BS.RUNNING_BALANCE"), "bank_statement", severity="high")["findings"]
    assert kept["severity"] == "high"
    assert "agent_severity" not in kept and "severity_floor_reason" not in kept


def test_a_code_in_check_codes_counts_too() -> None:
    finding = _finding("PAGE.BALANCE_CARRY", check_codes=["PAGE.BALANCE_CARRY", "BS.RUNNING_BALANCE"])
    [kept] = _collect(finding, "bank_statement")["findings"]
    assert kept["severity"] == "medium" and kept["agent_severity"] == "low"


# --- (c) everything else stays the AI's call ------------------------------------------------------


@pytest.mark.parametrize("code", RULE_CODES)
def test_the_same_checks_on_another_document_type_are_the_ais_call(code: str) -> None:
    [kept] = _collect(_finding(code), "nric")["findings"]
    assert kept["severity"] == "low" and "agent_severity" not in kept
    assert _collect(_finding(code), "nric", severity=None, dismiss=True)["findings"] == []


def test_another_check_on_a_payslip_is_the_ais_call() -> None:
    [kept] = _collect(_finding("PAY.YTD"), "payslip")["findings"]
    assert kept["severity"] == "low" and "agent_severity" not in kept
    assert _collect(_finding("PAY.YTD"), "payslip", severity=None, dismiss=True)["findings"] == []


def test_a_higher_tool_floor_beats_the_rule() -> None:
    [kept] = _collect(_finding("PAY.NET", severity_floor="high"), "payslip")["findings"]
    assert kept["severity"] == "high"
    assert kept["severity_floor_reason"] == specialists._TOOL_FLOOR_REASON


def test_an_unassessed_finding_is_not_an_overrule() -> None:
    finding = _finding("PAY.NET")
    messages = [
        AIMessage(content="", tool_calls=[{"name": _TOOL, "args": {}, "id": "call-1"}]),
        ToolMessage(content=json.dumps({"tool": _TOOL, "findings": [finding]}), tool_call_id="call-1", name=_TOOL),
        AIMessage(content=json.dumps({"summary": "done", "assessments": []})),
    ]
    state = {"messages": messages, "specialist": "content_validation", "document_type": "payslip"}
    [kept] = specialists._collect_node(cast(Any, state))["report"]["findings"]
    assert kept["severity"] == "medium" and "agent_severity" not in kept


# --- the lead's merge: a second look clears a stale note ------------------------------------------


def _report(specialist: str, finding: dict, *, second_look: bool = False) -> dict:
    return {
        "specialist": specialist, "brief": "", "tools_called": [_TOOL], "summary": "", "uncertain": False,
        "dismissed": [], "findings": [finding], "unable_to_assess": [], "nothing_to_check": [],
        "second_look": second_look,
    }


def _merged(first: dict, second: dict) -> dict:
    state = {"document_id": "doc-1", "context": {"document_type": "payslip"}, "reports": [
        _report("content_validation", first), _report("digital_authenticity", second, second_look=True),
    ]}
    [finding] = merge_reports(cast(Any, state))["findings"]
    return finding


_FLOORED = {"id": _ID, "tool": _TOOL, "summary": "Net pay doesn't add up", "severity": "medium",
            "agent_severity": "low", "severity_floor_reason": specialists._RULE_FLOOR_REASON}


def test_a_second_look_rated_above_the_floor_clears_the_note() -> None:
    second = {"id": _ID, "tool": _TOOL, "summary": "Net pay doesn't add up", "severity": "high"}
    merged = _merged(dict(_FLOORED), second)
    assert merged["severity"] == "high"
    assert "agent_severity" not in merged and "severity_floor_reason" not in merged


def test_a_second_look_also_below_the_floor_keeps_the_note() -> None:
    second = {"id": _ID, "tool": _TOOL, "summary": "Net pay doesn't add up", "severity": "low"}
    merged = _merged(dict(_FLOORED), second)
    assert merged["severity"] == "medium"
    assert merged["agent_severity"] == "low"
    assert merged["severity_floor_reason"] == specialists._RULE_FLOOR_REASON


def test_a_second_look_floored_after_an_unfloored_first_rating_keeps_the_higher_ai_rating() -> None:
    first = {"id": _ID, "tool": _TOOL, "summary": "Net pay doesn't add up", "severity": "info"}
    merged = _merged(first, dict(_FLOORED))
    assert merged["severity"] == "medium"
    assert merged["agent_severity"] == "low"
    assert merged["severity_floor_reason"] == specialists._RULE_FLOOR_REASON


def test_a_rule_check_signal_is_not_held_at_medium() -> None:
    # C021: a bank statement that prints no opening balance got BS.OPEN_CLOSE's "can't be checked" note, and the
    # balance-check floor held it at medium, enough alone to rate a clean case Medium.
    note = _finding("BS.OPEN_CLOSE", "signal", summary="The statement prints no opening balance")
    [kept] = _collect(note, "bank_statement")["findings"]
    assert kept["severity"] == "low"
    assert "severity_floor_reason" not in kept


def test_a_balance_that_does_not_add_up_is_still_held_at_medium() -> None:
    [kept] = _collect(_finding("BS.OPEN_CLOSE", "inconsistent"), "bank_statement")["findings"]
    assert kept["severity"] == "medium"
