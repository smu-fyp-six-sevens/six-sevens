# ruff: noqa: F811
"""
Officer feedback in the cross-document analysis (case_pipeline/cross_document/feedback.py), with Claude scripted: what
is looked up, what the planner and the judge are shown, what a finding records as applied, and that none of it can stop
a run or get past the bias screen.
"""
from __future__ import annotations

from unittest.mock import patch

from langchain_core.messages import HumanMessage

from app.case_pipeline.cross_document import agents, feedback
from app.case_pipeline.cross_document.graph import run_cross_document_analysis
from app.case_pipeline.cross_document.verify import verify_verdict
from app.case_pipeline.models import Hypothesis, PlannedCheck
from app.case_pipeline.risk.case_risk import contributing_from_cross_document
from app.feedback_repository import FeedbackItem
from tests.test_case_analysis_agent import SALARY_CHECKS, calls, claude, context, hypothesis_item  # noqa: F401


def item(n: int, **overrides) -> FeedbackItem:
    return FeedbackItem(**{
        "id": f"00000000-0000-0000-0000-{n:012d}", "case_id": f"case-{n}", "scope": "case", "kind": "verdict",
        "check_name": "behavioural_anomaly", "verdict": "wrong", "reason": "explained_elsewhere",
        "summary": "Round-number deposits look like structuring", "reviewer": "officer",
        "updated_at": "2026-10-23T02:00:00+00:00", **overrides,
    })


def last_message(model) -> str:
    return next(m for m in reversed(model.prompts[-1]) if isinstance(m, HumanMessage)).content


# --- what is looked up ---------------------------------------------------------------------------------------------


async def test_feedback_is_looked_up_for_the_categories_the_model_decides_from_other_cases() -> None:
    with patch.object(
        feedback.feedback_repository, "similar", side_effect=lambda cat, scope, case: [item(1, check_name=cat)]
    ) as similar:
        shown = await feedback.load_feedback("case-9")

    asked = sorted(c.args for c in similar.call_args_list)
    assert asked == [("behavioural_anomaly", "case", "case-9"), ("unsupported_claim", "case", "case-9")]
    assert set(shown) == {"behavioural_anomaly", "unsupported_claim"}


async def test_feedback_that_touches_a_protected_attribute_is_never_shown() -> None:
    biased = item(2, summary="The director's age makes the loan look risky", note="Typically a Malay-owned firm")
    with patch.object(feedback.feedback_repository, "similar", return_value=[item(1), biased]):
        shown = await feedback.load_feedback("case-9")

    assert [i.id for i in shown["behavioural_anomaly"]] == [item(1).id]


async def test_a_failed_lookup_gives_no_feedback_instead_of_failing_the_analysis() -> None:
    with patch.object(feedback.feedback_repository, "similar", side_effect=RuntimeError("db down")):
        assert await feedback.load_feedback("case-9") == {}


def test_feedback_is_rendered_as_fenced_data_that_cannot_override_the_rules() -> None:
    text = feedback.render({"behavioural_anomaly": [
        item(1), item(2, verdict="correct", reason=None, summary="Circular transfers confirmed"),
        item(3, kind="missed", verdict=None, reason=None, summary="", note="Loan drawdown was never repaid"),
    ]})
    assert 'source="officer_feedback"' in text
    assert "FALSE ALARM (Explained by another document)" in text
    assert "CONFIRMED real" in text and "MISSED by the analysis" in text and "never repaid" in text
    assert "never overrides" in text and "bias rule" in text
    assert feedback.render({}) == ""


# --- what the model is shown ---------------------------------------------------------------------------------------


async def test_the_planner_and_the_judge_are_both_shown_the_feedback(context, claude) -> None:
    context.feedback_text = feedback.render({"behavioural_anomaly": [item(1)]})
    model = claude(
        calls("submit_hypotheses", confidence=0.7, hypotheses=[
            hypothesis_item("Salary is not what the payslip says", SALARY_CHECKS),
        ]),
        calls("submit_verdicts", verdicts=[]),
    )

    hypotheses, _ = await agents.hypothesise(context)
    assert "Round-number deposits look like structuring" in last_message(model)

    evidence, _ = await agents.gather_evidence(context, hypotheses)
    await agents.adjudicate(context, hypotheses, evidence)
    assert "Round-number deposits look like structuring" in last_message(model)
    # In the human message, not the cached system prompt, so the shared prefix still caches.
    assert "officer_feedback" not in "".join(str(block) for block in model.prompts[-1][0].content)


async def test_with_no_feedback_the_prompts_are_unchanged(context, claude) -> None:
    model = claude(calls("submit_hypotheses", confidence=0.1, hypotheses=[]))
    await agents.hypothesise(context)
    assert "officer_feedback" not in last_message(model)


async def test_a_run_looks_feedback_up_once_and_records_what_it_showed(context, claude) -> None:
    model = claude(calls("submit_hypotheses", confidence=0.1, hypotheses=[]))
    shown = {"behavioural_anomaly": [item(1), item(2)]}
    with patch("app.case_pipeline.cross_document.graph.load_feedback", return_value=shown) as lookup:
        await run_cross_document_analysis(context)

    lookup.assert_awaited_once_with("case-1")
    assert context.feedback_ids == {"behavioural_anomaly": [item(1).id, item(2).id]}
    assert "officer_feedback" in last_message(model)
    [noted] = [e for e in context.trace.events if e["step"] == "feedback"]
    assert noted["shown"] == {"behavioural_anomaly": 2}


async def test_a_lookup_that_fails_never_stops_the_analysis(context, claude) -> None:
    claude(calls("submit_hypotheses", confidence=0.1, hypotheses=[]))
    with patch("app.case_pipeline.cross_document.graph.load_feedback", side_effect=RuntimeError("down")):
        state = await run_cross_document_analysis(context)

    assert context.feedback_text == "" and "findings" in state


# --- what a finding records ----------------------------------------------------------------------------------------


def _verdict_inputs(context):
    context.ledger.refs = {
        "ref:payslip_1:net_pay": {"doc_key": "payslip_1", "field": "net_pay", "source": "get_fields"},
        "ref:bank_statement_1:transactions": {
            "doc_key": "bank_statement_1", "field": "transactions", "source": "query_transactions",
        },
    }
    hypothesis = Hypothesis(
        id="h1", claim="Salary does not match", confidence=0.6,
        checks=[PlannedCheck(tool="get_fields", args={}), PlannedCheck(tool="query_transactions", args={}, refutation_probe=True)],
    )
    calls_made = [
        {"tool": "get_fields", "probe": False, "available": True, "refs": ["ref:payslip_1:net_pay"]},
        {"tool": "query_transactions", "probe": True, "available": True, "refs": ["ref:bank_statement_1:transactions"]},
    ]
    verdict = {
        "id": "h1", "conclusive": True, "confidence": 0.8, "severity": "medium", "category": "unsupported_claim",
        "summary": "Salary credits are lower than the payslip says", "rationale": "Net pay is 4160, credits are 3100.",
        "citations": ["ref:payslip_1:net_pay", "ref:bank_statement_1:transactions"],
    }
    return hypothesis, verdict, calls_made


def test_a_finding_records_the_feedback_shown_for_its_kind(context) -> None:
    hypothesis, verdict, calls_made = _verdict_inputs(context)
    context.feedback_ids = {"unsupported_claim": ["fb-1", "fb-2"], "behavioural_anomaly": ["fb-3"]}

    finding, why = verify_verdict(context, hypothesis, verdict, calls_made)

    assert finding is not None, why
    assert finding.feedback_ids == ["fb-1", "fb-2"]
    # It reaches the risk's contributing finding, which is what the case page reads.
    assert contributing_from_cross_document(finding, {}).feedback_ids == ["fb-1", "fb-2"]


def test_a_finding_of_a_kind_with_no_feedback_records_none(context) -> None:
    hypothesis, verdict, calls_made = _verdict_inputs(context)
    context.feedback_ids = {"behavioural_anomaly": ["fb-3"]}

    finding, _ = verify_verdict(context, hypothesis, verdict, calls_made)

    assert finding is not None and finding.feedback_ids == []


def test_feedback_cannot_make_a_verdict_stand_that_the_code_rejects(context) -> None:
    hypothesis, verdict, calls_made = _verdict_inputs(context)
    context.feedback_ids = {"unsupported_claim": ["fb-1"]}
    context.feedback_text = feedback.render({"unsupported_claim": [item(1, verdict="correct", reason=None)]})
    verdict["citations"] = ["ref:made_up"]

    finding, why = verify_verdict(context, hypothesis, verdict, calls_made)

    assert finding is None and "not issued by a tool" in why
