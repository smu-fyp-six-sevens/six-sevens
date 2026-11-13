"""Fingerprints (finding_keys.py): the key that ties a finding in a report to its row, and an officer's verdict to both."""

from __future__ import annotations

from types import SimpleNamespace

from app import finding_keys


def _finding(**overrides) -> dict:
    values = {
        "tool": "numerical_checks_tool", "check_code": "PAY.NET", "page": 1, "bbox": [10.2, 20.4, 30.6, 40.1],
        "summary": "Net pay doesn't add up", "severity": "medium", "rationale": "Checked.", "ai_accepted": True,
    }
    values.update(overrides)
    return values


def _report(findings=(), dismissed=()) -> dict:
    return {"findings": [dict(f) for f in findings], "consulted": [{"dismissed": [dict(d) for d in dismissed]}]}


def _fingerprints(report: dict) -> list[str]:
    finding_keys.assign_document_fingerprints(report)
    return [f["fingerprint"] for f, _ in finding_keys.report_findings(report)]


def test_the_same_finding_gets_the_same_key_however_the_agents_rated_it() -> None:
    first = _fingerprints(_report([_finding()]))
    again = _fingerprints(_report([_finding(severity="high", rationale="Different words.", ai_accepted=False)]))
    assert first == again


def test_the_key_ignores_the_case_and_spacing_of_the_summary() -> None:
    assert _fingerprints(_report([_finding()])) == _fingerprints(_report([_finding(summary="  NET pay  doesn't\nadd up ")]))


def test_a_different_check_page_or_place_is_a_different_finding() -> None:
    base = _fingerprints(_report([_finding()]))
    for change in ({"check_code": "PAY.CPF_RATE"}, {"page": 2}, {"bbox": [10.2, 90.0, 30.6, 99.0]}, {"tool": "other_tool"}):
        assert _fingerprints(_report([_finding(**change)])) != base, change


def test_a_box_that_moves_by_less_than_a_point_is_the_same_place() -> None:
    assert _fingerprints(_report([_finding()])) == _fingerprints(_report([_finding(bbox=[10.4, 20.2, 30.7, 40.3])]))


def test_the_same_words_at_the_same_place_are_told_apart_by_order() -> None:
    first, second = _fingerprints(_report([_finding(), _finding()]))
    assert second == f"{first}-2"


def test_kept_and_dismissed_findings_all_get_a_key_and_a_decision() -> None:
    kept = _finding()
    overridden = _finding(summary="Another", ai_accepted=False)
    unassessed = _finding(summary="Third", ai_accepted=False, rationale=finding_keys.UNASSESSED_RATIONALE)
    dismissed = _finding(summary="A false alarm")
    report = _report([kept, overridden, unassessed], [dismissed])

    keys = _fingerprints(report)
    decisions = [decision for _, decision in finding_keys.report_findings(report)]

    assert decisions == ["accepted", "overridden", "not_assessed", "dismissed"]
    assert len(set(keys)) == 4 and all(keys)


def test_a_dismissal_that_a_second_look_kept_is_listed_once() -> None:
    both = _finding()
    report = _report([both], [both, both])
    assert [decision for _, decision in finding_keys.report_findings(report)] == ["accepted"]


def _case_finding(**overrides) -> SimpleNamespace:
    values = dict(
        document_ids=["b", "a"], category="identity_entity_conflict", hypothesis_id="deterministic",
        summary="Two different UENs", citations=["ref:2", "ref:1"], fingerprint=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_a_code_made_case_finding_is_keyed_on_its_words_and_documents_in_any_order() -> None:
    first, second = _case_finding(), _case_finding(document_ids=["a", "b"], summary=" two different UENs ")
    finding_keys.assign_case_fingerprints([first])
    finding_keys.assign_case_fingerprints([second])
    assert first.fingerprint == second.fingerprint


def test_an_agent_made_case_finding_is_keyed_on_what_it_rests_on_not_its_wording() -> None:
    # Two runs of the analysis (so the same key isn't numbered apart within one run).
    first = _case_finding(hypothesis_id="h1", summary="Worded one way")
    reworded = _case_finding(hypothesis_id="h1", summary="Worded another way", citations=["ref:1", "ref:2"])
    other_evidence = _case_finding(hypothesis_id="h1", citations=["ref:9"])
    for run in ([first], [reworded], [other_evidence]):
        finding_keys.assign_case_fingerprints(run)
    assert first.fingerprint == reworded.fingerprint != other_evidence.fingerprint
