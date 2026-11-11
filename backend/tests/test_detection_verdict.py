"""The combined verdict: flagged, inconclusive, or clean."""

from __future__ import annotations

from app.langgraph.detection.graph import merge_reports


def _report(specialist: str, *, findings=(), unable=(), nothing=(), tools_called=("some_tool",), dismissed=()) -> dict:
    return {
        "specialist": specialist,
        "brief": "",
        "tools_called": list(tools_called),
        "summary": "",
        "uncertain": False,
        "dismissed": list(dismissed),
        "findings": list(findings),
        "unable_to_assess": list(unable),
        "nothing_to_check": list(nothing),
    }


def _verdict(*reports: dict) -> dict:
    state = {"document_id": "doc-1", "context": {"document_type": "nric"}, "reports": list(reports)}
    return merge_reports(state)  # type: ignore[arg-type]


NO_METADATA = {"tool": "metadata_tool", "reason": "This PNG image carries no authorship or editing metadata"}


def test_a_check_with_nothing_to_examine_does_not_stop_a_clean_verdict() -> None:
    result = _verdict(
        _report("digital_authenticity", nothing=[NO_METADATA]),
        _report("content_validation"),
    )
    assert result["verdict"] == "clean"
    assert result["unable_to_assess"] == []
    assert result["nothing_to_check"] == [{"specialist": "digital_authenticity", **NO_METADATA}]


def test_a_check_that_could_not_run_still_makes_it_inconclusive() -> None:
    failed = {"tool": "spelling_tool", "reason": "No extractable text found in document"}
    result = _verdict(_report("content_validation", unable=[failed]), _report("digital_authenticity", nothing=[NO_METADATA]))
    assert result["verdict"] == "inconclusive"


def test_a_finding_still_flags() -> None:
    finding = {"id": "numerical_checks_tool-1", "tool": "numerical_checks_tool", "severity": "high", "summary": "x", "rationale": "y"}
    result = _verdict(_report("content_validation", findings=[finding], nothing=[NO_METADATA]))
    assert result["verdict"] == "flagged"


def test_reports_from_before_nothing_to_check_existed_still_merge() -> None:
    old = _report("content_validation")
    del old["nothing_to_check"]
    assert _verdict(old)["verdict"] == "clean"


# --- a second look can retire a finding, and can redo a check that failed ---------------------------------

CHUEN = {"id": "spelling_tool-1", "tool": "spelling_tool", "severity": "low", "summary": 'Possible misspelling "CHUEN".', "rationale": "y"}
CHUEN_DISMISSED = {"id": "spelling_tool-1", "tool": "spelling_tool", "summary": CHUEN["summary"], "reason": "A surname."}


def test_a_finding_dismissed_in_a_second_look_is_dropped_from_the_report() -> None:
    result = _verdict(
        _report("content_validation", findings=[CHUEN]),
        _report("content_validation", dismissed=[CHUEN_DISMISSED]),
    )

    assert result["findings"] == []
    assert result["verdict"] == "clean"
    assert result["consulted"][0]["dismissed"] == [CHUEN_DISMISSED]


def test_a_finding_the_second_look_lists_again_after_dismissing_it_stays() -> None:
    result = _verdict(
        _report("content_validation", dismissed=[CHUEN_DISMISSED]),
        _report("content_validation", findings=[CHUEN]),
    )

    assert [f["id"] for f in result["findings"]] == ["spelling_tool-1"]


def test_a_dismissal_removes_only_the_finding_it_names() -> None:
    other = {**CHUEN, "id": "spelling_tool-2", "summary": 'Possible misspelling "COMPTROLER".'}
    result = _verdict(
        _report("content_validation", findings=[CHUEN, other]),
        _report("content_validation", dismissed=[CHUEN_DISMISSED]),
    )

    assert [f["summary"] for f in result["findings"]] == [other["summary"]]


def test_a_check_that_failed_and_ran_in_the_second_look_is_not_left_unassessed() -> None:
    timeout = {"tool": "layout_anomalies_tool", "reason": "Execution timeout: no result within 60 seconds."}
    result = _verdict(
        _report("layout_rendering", unable=[timeout], tools_called=["layout_anomalies_tool"]),
        _report("layout_rendering", tools_called=["layout_anomalies_tool", "font_consistency_tool"]),
    )

    assert result["unable_to_assess"] == []
    assert result["verdict"] == "clean"


def test_a_check_that_failed_again_in_the_second_look_stays_unassessed() -> None:
    timeout = {"tool": "layout_anomalies_tool", "reason": "Execution timeout: no result within 60 seconds."}
    result = _verdict(
        _report("layout_rendering", unable=[timeout], tools_called=["layout_anomalies_tool"]),
        _report("layout_rendering", unable=[timeout], tools_called=["layout_anomalies_tool"]),
    )

    assert [u["tool"] for u in result["unable_to_assess"]] == ["layout_anomalies_tool"]


def test_another_specialists_later_run_does_not_clear_a_failure() -> None:
    timeout = {"tool": "layout_anomalies_tool", "reason": "Execution timeout: no result within 60 seconds."}
    result = _verdict(
        _report("layout_rendering", unable=[timeout], tools_called=["layout_anomalies_tool"]),
        _report("content_validation", tools_called=["layout_anomalies_tool"]),
    )

    assert [u["specialist"] for u in result["unable_to_assess"]] == ["layout_rendering"]
