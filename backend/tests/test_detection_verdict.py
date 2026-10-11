"""The combined verdict: flagged, inconclusive, or clean."""

from __future__ import annotations

from app.langgraph.detection.graph import merge_reports


def _report(specialist: str, *, findings=(), unable=(), nothing=()) -> dict:
    return {
        "specialist": specialist,
        "brief": "",
        "tools_called": ["some_tool"],
        "summary": "",
        "uncertain": False,
        "dismissed": [],
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
