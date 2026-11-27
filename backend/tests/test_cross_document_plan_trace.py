# ruff: noqa: F811
"""What code corrected in the planner's plan is noted on the run's trace, for the sensibility checks (evals/case_analysis.py)."""
from __future__ import annotations

from app.case_pipeline.cross_document import agents
from tests.test_case_analysis_agent import SALARY_CHECKS, calls, claude, context, hypothesis_item, noted  # noqa: F401


async def test_the_note_names_the_tools_the_planner_made_up(context, claude) -> None:
    claude(calls("submit_hypotheses", confidence=0.6, hypotheses=[
        hypothesis_item("Salary is not what the payslip says", [*SALARY_CHECKS, {"tool": "made_up_tool", "args": {}}]),
    ]))

    await agents.hypothesise(context)

    assert noted(context, "hypotheses")["unknown_tools"] == ["made_up_tool"]


async def test_the_note_lists_the_hypotheses_whose_probe_code_added(context, claude) -> None:
    probed = [
        {**SALARY_CHECKS[0]},
        {**SALARY_CHECKS[1], "refutation_probe": True},
    ]
    claude(calls("submit_hypotheses", confidence=0.6, hypotheses=[
        hypothesis_item("Salary credits fall short of net pay", SALARY_CHECKS),
        hypothesis_item("Salary credits are not the payslip's net pay", probed),
    ]))

    await agents.hypothesise(context)

    note = noted(context, "hypotheses")
    assert note["probes_added_by_code"] == ["h1"]
    assert note["unknown_tools"] == []
