"""The sensibility checks on agent runs (app/evals/): each marks what it should, reads only its own run, and never breaks one."""

import pytest

from app.evals import case_analysis, classifier, detection, extraction, runner
from app.evals.base import judged, run_steps

ROOT = "run1"


def _step(name: str, **fields) -> dict:
    return {"step": name, "in_span": ROOT, **fields}


def _results(checks) -> dict[str, str]:
    return {c.check: c.result for c in checks}


# --- picking a run's steps ---------------------------------------------------------------------------------------


def test_run_steps_keep_only_the_run_asked_for():
    steps = [
        _step("ingest"),
        {"step": "span", "name": "classify.agent_call", "id": "child", "parent": ROOT},
        {"step": "agent_call", "in_span": "child"},
        {"step": "span", "name": "tool.vision_llm", "id": "grandchild", "parent": "child"},
        {"step": "detection_start", "in_span": "older-run"},
        {"step": "span", "name": "detection.run", "id": "older-run", "parent": None},
    ]
    kept = run_steps(steps, ROOT)
    assert [s.get("name") or s["step"] for s in kept] == ["ingest", "classify.agent_call", "agent_call", "tool.vision_llm"]
    assert run_steps(steps, None) == steps


def test_judged_marks_pass_broken_and_not_applicable():
    assert judged("a", "c", "output", True, "why").result == "pass"
    assert judged("a", "c", "output", False, "why").result == "warn"
    assert judged("a", "c", "output", False, "why", broken="fail").reason == "why"
    assert judged("a", "c", "output", None, "why").result == "n/a"


# --- classifier --------------------------------------------------------------------------------------------------


def test_classifier_checks_flag_overconfidence_refusals_and_a_skipped_read():
    steps = [
        _step("ingest", text_available=True),
        _step("rule_classify", certain_type=None),
        _step("agent_call", tool_calls=["vision_llm"]),
        _step("classify_tool_refused", tools=["vision_llm"]),
        _step("confidence", agent_confidence=0.99, computed_confidence=0.6),
        _step("classification_finalised", document_type="payslip"),
    ]
    marks = _results(classifier.checks(steps))
    assert marks["read_before_deciding"] == "warn"
    assert marks["tool_order"] == "warn"
    assert marks["not_overconfident"] == "warn"
    assert marks["agrees_with_certain_rule"] == "n/a"


def test_classifier_fails_a_type_against_a_certain_rule_and_no_look_without_text():
    steps = [
        _step("ingest", text_available=False),
        _step("rule_classify", certain_type="nric"),
        _step("classification_finalised", document_type="passport"),
        _step("agent_call", tool_calls=[]),
    ]
    marks = _results(classifier.checks(steps))
    assert marks["agrees_with_certain_rule"] == "fail"
    assert marks["looked_when_no_text"] == "fail"


def test_classifier_that_read_and_agreed_passes():
    steps = [
        _step("ingest", text_available=True),
        _step("rule_classify", certain_type=None),
        {"step": "span", "name": "tool.llm_classify_tool", "id": "t1", "parent": ROOT},
        _step("agent_call", tool_calls=["llm_classify_tool"]),
        _step("confidence", agent_confidence=0.9, computed_confidence=0.85),
        _step("classification_finalised", document_type="payslip"),
    ]
    results = classifier.checks(run_steps(steps, ROOT))
    assert {c.result for c in results} <= {"pass", "n/a"}


def test_an_officer_typed_document_has_no_classifier_checks():
    assert classifier.checks([_step("classification_skipped", document_type="payslip")]) == []


def test_classifier_must_answer_an_extraction_objection():
    steps = [
        _step("rule_classify"),
        _step("classification_finalised", document_type="payslip"),
        _step("reclassify", reason="looks like a bank statement"),
        _step("agent_call", tool_calls=[]),
        _step("classification_finalised", document_type="payslip", needs_manual_review=False),
    ]
    assert _results(classifier.checks(steps))["answers_objection"] == "warn"
    steps[-1]["document_type"] = "bank_statement"
    assert _results(classifier.checks(steps))["answers_objection"] == "pass"


# --- extractor ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "bad"),
    [("net_pay", "S$1,234.50", None), ("closing_balance", "(1,200)", None), ("net_pay", "(1,200)", "negative"),
     ("net_pay", 1200, None),
     ("net_pay", "abc", "bad_numbers"), ("basic_salary", "-100", "negative"), ("closing_balance", "-100", None),
     ("pay_date", "not a date", "bad_dates"), ("pay_date", "2026-10-31", None), ("transactions", [{"a": 1}], None)],
)
def test_field_shape_reads_values_sensibly(field, value, bad):
    shape = extraction.field_shape("payslip", {field: value})
    for key in ("bad_dates", "bad_numbers", "negative"):
        assert (field in shape[key]) == (key == bad), (key, shape)


def test_field_shape_lists_missing_required_fields_by_name_only():
    shape = extraction.field_shape("payslip", {"net_pay": "1000"})
    assert shape["missing_required"]
    assert "net_pay" not in shape["missing_required"]
    assert all(isinstance(name, str) for name in shape["missing_required"])


def test_extractor_checks_the_second_look():
    steps = [
        _step("extraction", second_look={"fields": []}, second_look_rejected_fields=[{"field": "x"}],
              second_look_error="timeout", field_shape={"missing_required": [], "bad_dates": [], "bad_numbers": [],
                                                        "negative": []}),
        _step("extraction_second_look", turn_count=4),
        _step("extraction_tool_refused", tools=["vision_read"]),
    ]
    marks = _results(extraction.checks(steps))
    assert marks["required_fields_present"] == "pass"
    assert marks["second_look_finished"] == "fail"
    assert marks["second_look_turns"] == "warn"
    assert marks["second_look_changes_used"] == "warn"
    assert marks["second_look_tool_order"] == "warn"


def test_without_a_second_look_its_checks_dont_apply():
    marks = _results(extraction.checks([_step("extraction", field_shape=None)]))
    assert marks["second_look_finished"] == marks["second_look_turns"] == "n/a"
    assert marks["required_fields_present"] == "n/a"


# --- detection ---------------------------------------------------------------------------------------------------


def _specialist(**fields) -> dict:
    return {"step": "detection_specialist", "in_span": "spec1", "specialist": "content_validation",
            "tools_called": ["numerical_checks_tool"], "tool_calls": [{"name": "numerical_checks_tool", "args": {}}],
            "dismissed": [], **fields}


def test_content_validation_must_run_the_numerical_checks_on_a_payslip():
    plan = _step("detection_plan", fallback_briefs=[])
    missing = detection.checks([plan, _specialist(tools_called=["spelling_tool"])], document_type="payslip", lead_summary="ok")
    marks = {c.check: c for c in missing if c.agent == "specialist:content_validation"}
    assert marks["ran_expected_checks"].result == "fail"
    assert marks["ran_expected_checks"].span == "spec1"
    ran = detection.checks([plan, _specialist()], document_type="payslip", lead_summary="ok")
    assert _results(c for c in ran if c.agent.startswith("specialist"))["ran_expected_checks"] == "pass"
    other = detection.checks([plan, _specialist(tools_called=["spelling_tool"])], document_type="nric", lead_summary="ok")
    assert _results(c for c in other if c.agent.startswith("specialist"))["ran_expected_checks"] == "n/a"


def test_specialist_report_problems_are_warnings():
    step = _specialist(tool_calls=[{"name": "spelling_tool", "args": {}}] * 2, wrapped_up=True, unassessed=2,
                       floor_overruled=1, dismissed=[{"id": "f1", "reason": "Dismissed as a false positive."}])
    marks = _results(c for c in detection.checks([step], document_type="payslip", lead_summary="ok") if c.agent != "lead")
    assert marks["no_repeated_calls"] == marks["within_tool_rounds"] == "warn"
    assert marks["assessed_every_finding"] == marks["respected_floors"] == marks["explained_dismissals"] == "warn"


def test_lead_checks_briefs_second_looks_and_its_summary():
    steps = [
        _step("detection_plan", fallback_briefs=["layout_rendering"]),
        _step("detection_review", second_look_requested=True, second_look_accepted=False, second_look_already_used=False),
    ]
    marks = _results(c for c in detection.checks(steps, document_type="payslip", lead_summary="") if c.agent == "lead")
    assert marks == {"specific_briefs": "warn", "valid_second_look": "warn", "wrote_summary": "warn"}


# --- case analysis -----------------------------------------------------------------------------------------------


def test_the_judge_fails_when_no_conclusive_verdict_stands_up():
    steps = [
        _step("judge_reply", via="tool"),
        _step("verdict", hypothesis="h1", verdict={"conclusive": True}),
        _step("verify", hypothesis="h1", kept=False, reason="the probe never ran"),
    ]
    assert _results(case_analysis.checks(steps))["verdicts_stand_up"] == "fail"
    steps += [_step("verdict", hypothesis="h2", verdict={"conclusive": True}),
              _step("verify", hypothesis="h2", kept=True, finding={"document_ids": ["d1", "d2"]})]
    assert _results(case_analysis.checks(steps))["verdicts_stand_up"] == "warn"


def test_an_inconclusive_verdict_dropped_by_the_verifier_is_not_held_against_the_judge():
    steps = [
        _step("judge_reply", via="tool"),
        _step("verdict", hypothesis="h1", verdict={"conclusive": False}),
        _step("verify", hypothesis="h1", kept=False, reason="not conclusive"),
    ]
    assert _results(case_analysis.checks(steps))["verdicts_stand_up"] == "n/a"


def test_a_kept_cross_document_finding_needs_two_documents():
    steps = [_step("judge_reply", via="tool"), _step("verify", hypothesis="h1", kept=True, finding={"document_ids": ["d1"]})]
    assert _results(case_analysis.checks(steps))["findings_span_documents"] == "fail"


def test_the_summary_may_not_advise_an_outcome():
    steps = [{"step": "span", "name": "case_pipeline.summary", "id": "s", "parent": ROOT},
             _step("summary", source="model", headline="We recommend you decline the application.", reasons=[])]
    assert _results(case_analysis.checks(steps))["no_outcome_advice"] == "fail"
    steps[1]["headline"] = "Two documents disagree on the company's address."
    assert _results(case_analysis.checks(steps))["no_outcome_advice"] == "pass"


def test_a_replaced_summary_warns_and_its_text_isnt_checked():
    steps = [{"step": "span", "name": "case_pipeline.summary", "id": "s", "parent": ROOT},
             _step("summary", source="fallback", headline="Decline the application.", reasons=[])]
    marks = _results(case_analysis.checks(steps))
    assert marks["kept_its_limits"] == "warn"
    assert marks["no_outcome_advice"] == "n/a"


def test_the_planner_fails_on_a_protected_attribute():
    steps = [_step("hypotheses", via="tool", dropped_for_bias=[{"claim": "x"}], unknown_tools=[], probes_added_by_code=[])]
    marks = _results(case_analysis.checks(steps))
    assert marks["no_protected_attributes"] == "fail"
    assert marks["used_real_tools"] == marks["planned_refutation_probes"] == "pass"


def test_evidence_requests_must_cite_real_findings():
    steps = [_step("evidence_requests", proposed=[{"title": "Bank letter", "what_it_shows": "income", "source": "agent"}]),
             _step("outlook", requests=[{"title": "Bank letter", "source": "agent",
                                         "dropped": [{"finding_id": "F9", "reason": "no such finding"}]}])]
    assert _results(case_analysis.checks(steps))["addresses_real_findings"] == "warn"


@pytest.mark.parametrize(
    ("status", "sources", "expected"),
    [("clean", 0, "pass"), ("concerns", 2, "pass"), ("concerns", 0, "warn"), ("failed", 0, "warn")],
)
def test_adverse_media_result_must_be_backed(status, sources, expected):
    marks = _results(case_analysis.checks([_step("adverse_media", status=status, sources=sources)]))
    assert marks["result_backed"] == expected


# --- recording ---------------------------------------------------------------------------------------------------


def test_results_are_recorded_as_one_evals_step(monkeypatch: pytest.MonkeyPatch):
    recorded: list[tuple[str, dict]] = []
    monkeypatch.setattr(runner.telemetry, "current_step_id", lambda: ROOT)

    class _Trace:
        run_id = "r1"
        events = [_step("hypotheses", via="text", dropped_for_bias=[], unknown_tools=[], probes_added_by_code=[])]

        def record(self, step, **fields):
            recorded.append((step, fields))

    runner.record_case_run(_Trace())
    assert len(recorded) == 1
    step, fields = recorded[0]
    assert step == "evals"
    assert fields["warnings"] == 1 and fields["failures"] == 0
    assert {r["check"] for r in fields["results"]} >= {"answered_through_tool"}


def test_a_check_that_raises_never_breaks_the_run(monkeypatch: pytest.MonkeyPatch):
    recorded: list = []

    def boom(steps):
        raise ValueError("broken check")

    monkeypatch.setattr(case_analysis, "checks", boom)

    class _Trace:
        run_id = "r1"
        events: list = []

        def record(self, step, **fields):
            recorded.append(step)

    runner.record_case_run(_Trace())  # must not raise
    assert recorded == []


def test_a_run_with_nothing_to_check_records_nothing(monkeypatch: pytest.MonkeyPatch):
    recorded: list = []

    class _Trace:
        run_id = "r1"
        events: list = []

        def record(self, step, **fields):
            recorded.append(step)

    runner.record_case_run(_Trace())
    assert recorded == []


@pytest.mark.parametrize(
    ("record", "module", "kwargs"),
    [
        (runner.record_document_run, classifier, {}),
        (runner.record_detection_run, detection, {"document_type": "payslip", "lead_summary": None}),
    ],
)
def test_a_check_that_raises_never_breaks_a_document_or_detection_run(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, record, module, kwargs,
):
    from app.langgraph import trace

    recorded: list = []

    def boom(*args, **kw):
        raise ValueError("broken check")

    monkeypatch.setattr(module, "checks", boom)
    monkeypatch.setattr(trace, "record", lambda *a, **kw: recorded.append(a))
    monkeypatch.setattr(runner.telemetry, "current_step_id", lambda: ROOT)

    assert record("doc-1", **kwargs) is None  # must not raise
    assert recorded == []
    assert "the run is unaffected" in caplog.text
