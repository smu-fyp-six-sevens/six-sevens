"""
The case analysis agent's loop (case_pipeline/cross_document/agents.py) with Claude scripted: what code keeps of the
planner's hypotheses before any check runs, how a reply that doesn't call its tool is asked once more and then read
from its text, the run's ceiling on model calls, a judge reply that can't be read, and a check that fails or hangs.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.case_pipeline.cross_document import agents, playbook
from app.case_pipeline.cross_document.bias import has_protected_text
from app.case_pipeline.cross_document.evidence import build_context
from app.case_pipeline.cross_document.graph import _open_question
from app.case_pipeline.models import Hypothesis, PlannedCheck
from app.case_pipeline.trace import RunTrace
from app.models import DocumentRecord, DocumentStatus


class ScriptedClaude:
    """build_chat_anthropic(...).bind_tools(...): each call answers with the next scripted reply."""

    def __init__(self, *replies: AIMessage) -> None:
        self.replies = list(replies)
        self.prompts: list[list] = []

    def bind_tools(self, tools, tool_choice=None) -> ScriptedClaude:
        return self

    async def ainvoke(self, messages):
        self.prompts.append(list(messages))
        return self.replies.pop(0)


def calls(tool: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": tool, "args": args, "id": "call-1"}],
                     response_metadata={"stop_reason": "tool_use"})


def says(text: str) -> AIMessage:
    return AIMessage(content=text, response_metadata={"stop_reason": "end_turn"})


@pytest.fixture
def claude(monkeypatch):
    def script(*replies: AIMessage) -> ScriptedClaude:
        model = ScriptedClaude(*replies)
        monkeypatch.setattr(agents, "build_chat_anthropic", lambda *args, **kwargs: model)
        return model
    return script


def document(document_type: str, document_id: str, **fields) -> DocumentRecord:
    return DocumentRecord(id=document_id, case_id="case-1", filename=f"{document_id}.pdf", status=DocumentStatus.DETECTED,
                          uploaded_at=datetime(2026, 11, 1, tzinfo=timezone.utc), document_type=document_type,
                          extracted_fields=fields)


@pytest.fixture
def context():
    case = build_context("case-1", [
        document("payslip", "doc-pay", net_pay="4160.00", employer_name="Brightwave Analytics Pte Ltd"),
        document("bank_statement", "doc-bs", transactions=[{"date": "05 OCT", "description": "SALARY", "amount": 3100}],
                 statement_period_start="01 Oct 2026", statement_period_end="31 Oct 2026"),
    ], {})
    case.trace = RunTrace("run-1")  # in memory: only save() writes it
    return case


def noted(context, step: str) -> dict:
    [event] = [e for e in context.trace.events if e["step"] == step]
    return event


def hypothesis_item(claim: str, checks: list[dict], **extra) -> dict:
    return {"claim": claim, "confidence": 0.6, "checks": checks, **extra}


SALARY_CHECKS = [
    {"tool": "get_fields", "args": {"document": "payslip_1", "names": ["net_pay"]}},
    {"tool": "query_transactions", "args": {"document": "bank_statement_1", "description_contains": "salary"}},
]


# --- What code keeps of the plan ----------------------------------------------------------------------------------


async def test_a_planned_hypothesis_is_kept_with_its_checks_and_a_probe_on_its_refutation_document(context, claude) -> None:
    claude(calls("submit_hypotheses", confidence=0.7, hypotheses=[hypothesis_item(
        "Salary credits fall short of net pay", SALARY_CHECKS, refutation_document="bank_statement_1",
        documents=["payslip_1", "bank_statement_1"],
    )]))

    hypotheses, confidence = await agents.hypothesise(context)

    [kept] = hypotheses
    assert (kept.id, kept.refutation_document, confidence) == ("h1", "bank_statement_1", 0.7)
    # No check was marked the probe: the one on the refutation document is.
    assert [(c.tool, c.refutation_probe) for c in kept.checks] == [("get_fields", False), ("query_transactions", True)]


async def test_hypotheses_that_cannot_be_tested_here_are_dropped_before_any_check_runs(context, claude) -> None:
    claude(calls("submit_hypotheses", hypotheses=[
        hypothesis_item("The director's name suggests he is foreign", SALARY_CHECKS),
        hypothesis_item("Net pay is high for the role", [{"tool": "get_fields", "args": {"document": "payslip_1",
                                                                                        "names": ["net_pay"]}}]),
        hypothesis_item("Uses a tool that does not exist", [{"tool": "delete_everything", "args": {}}]),
        hypothesis_item("   ", SALARY_CHECKS),
        "not a hypothesis",
    ]))

    hypotheses, _ = await agents.hypothesise(context)

    assert hypotheses == []
    planned = noted(context, "hypotheses")
    assert planned["dropped_for_bias"] == [
        {"claim": "The director's name suggests he is foreign", "reason": "touches a protected attribute"}]
    assert planned["dropped_for_scope"] == [{"claim": "Net pay is high for the role", "reason": "one document, nothing computed"}]


async def test_at_most_three_hypotheses_are_tested_and_an_unknown_refutation_document_is_ignored(context, claude) -> None:
    claude(calls("submit_hypotheses", hypotheses=[
        hypothesis_item(f"Claim {n}", SALARY_CHECKS, refutation_document="bank_statement_9") for n in range(5)
    ]))

    hypotheses, _ = await agents.hypothesise(context)

    assert [h.claim for h in hypotheses] == ["Claim 0", "Claim 1", "Claim 2"]
    assert {h.refutation_document for h in hypotheses} == {None}
    # With no refutation document named, the last check is made the probe.
    assert [c.refutation_probe for c in hypotheses[0].checks] == [False, True]


async def test_a_case_with_no_approved_documents_is_not_sent_to_claude(claude) -> None:
    model = claude()

    assert await agents.hypothesise(build_context("case-1", [], {})) == ([], 0.0)
    assert model.prompts == []


# --- A reply that doesn't call its tool -----------------------------------------------------------------------------


async def test_a_reply_without_the_tool_call_is_asked_once_more(context, claude) -> None:
    model = claude(says("Let me think about this case first."), calls("submit_hypotheses", hypotheses=[
        hypothesis_item("Salary credits fall short of net pay", SALARY_CHECKS)]))

    hypotheses, _ = await agents.hypothesise(context)

    assert len(hypotheses) == 1
    reminder = model.prompts[1][-1]
    assert isinstance(reminder, HumanMessage)
    assert reminder.content == "Your last reply did not call submit_hypotheses. Reply only by calling submit_hypotheses."
    assert context.model_calls == 2


async def test_hypotheses_written_as_json_text_are_read(context, claude) -> None:
    claude(says('{"confidence": 0.4, "hypotheses": [{"claim": "Salary credits fall short of net pay", '
                '"checks": [{"tool": "get_fields", "args": {"document": "payslip_1", "names": ["net_pay"]}}, '
                '{"tool": "query_transactions", "args": {"document": "bank_statement_1"}}]}]}'))

    hypotheses, confidence = await agents.hypothesise(context)

    assert ([h.claim for h in hypotheses], confidence) == (["Salary credits fall short of net pay"], 0.4)
    assert context.model_calls == 1  # readable text is an answer: not asked again


# --- The judge ---------------------------------------------------------------------------------------------------


def salary_hypothesis() -> Hypothesis:
    return Hypothesis(id="h1", claim="Salary credits fall short of net pay",
                      checks=[PlannedCheck(**check) for check in SALARY_CHECKS])


async def test_the_judges_verdicts_are_read_by_hypothesis(context, claude) -> None:
    claude(calls("submit_verdicts", verdicts=[
        {"id": "h1", "conclusive": True, "severity": "medium", "citations": ["ref:payslip_1:net_pay"]},
        {"id": "h9", "conclusive": True},  # no such hypothesis: ignored by the caller
        {"conclusive": True},  # no id
    ]))

    verdicts = await agents.adjudicate(context, [salary_hypothesis()], {"h1": []})

    assert set(verdicts) == {"h1", "h9"}
    assert verdicts["h1"]["citations"] == ["ref:payslip_1:net_pay"]


async def test_a_judge_reply_that_cannot_be_read_leaves_every_hypothesis_inconclusive(context, claude) -> None:
    claude(says("I think h1 is probably true."), says("On reflection, h1 is true."))

    verdicts = await agents.adjudicate(context, [salary_hypothesis()], {"h1": []})

    assert verdicts == {}
    assert context.model_calls == 2
    reply = noted(context, "judge_reply")
    assert (reply["via"], reply["parsed"], reply["raw"]) == ("text", False, "On reflection, h1 is true.")
    verdict = noted(context, "verdict")
    assert (verdict["hypothesis"], verdict["parsed"]) == ("h1", False)


async def test_no_hypotheses_means_no_judge_call(context, claude) -> None:
    model = claude()

    assert await agents.adjudicate(context, [], {}) == {}
    assert model.prompts == []


# --- The run's ceiling on model calls --------------------------------------------------------------------------------


async def test_a_run_never_makes_more_model_calls_than_its_ceiling(context, claude) -> None:
    claude(says("thinking"), says("still thinking"), says("and more"), says("and more"), says("never sent"))

    await agents.hypothesise(context)  # two calls, neither usable
    await agents.adjudicate(context, [salary_hypothesis()], {"h1": []})  # two more: the ceiling

    assert context.model_calls == agents.MAX_MODEL_CALLS_PER_RUN
    with pytest.raises(agents.ModelCallBudgetExceeded):
        await agents.adjudicate(context, [salary_hypothesis()], {"h1": []})


# --- Running the checks -----------------------------------------------------------------------------------------------


async def test_each_hypothesis_checks_run_with_the_evidence_tools(context) -> None:
    evidence, calls_by_hypothesis = await agents.gather_evidence(context, [salary_hypothesis()])

    assert [e["tool"] for e in evidence["h1"]] == ["get_fields", "query_transactions"]
    assert all(call["available"] for call in calls_by_hypothesis["h1"])


class BrokenTool:
    """An evidence tool that hangs or raises, in place of the real one."""

    def __init__(self, name: str, behaviour) -> None:
        self.name = name
        self._behaviour = behaviour

    async def ainvoke(self, args: dict):
        return await self._behaviour()


async def _hangs():
    await asyncio.sleep(1)


async def _raises():
    raise ValueError("bad arguments")


async def test_a_check_that_fails_or_hangs_is_recorded_unavailable_and_the_rest_still_run(context, monkeypatch) -> None:
    monkeypatch.setattr(agents.settings, "TOOL_TIMEOUT_SECONDS", 0.05)
    real_build_tools = agents.build_tools
    broken = {"monthly_flow": _hangs, "list_documents": _raises}
    monkeypatch.setattr(agents, "build_tools", lambda case, calls: [
        BrokenTool(t.name, broken[t.name]) if t.name in broken else t for t in real_build_tools(case, calls)
    ])
    hypothesis = Hypothesis(id="h1", claim="Salary credits fall short of net pay", checks=[
        PlannedCheck(tool="monthly_flow", args={"document": "bank_statement_1"}),
        PlannedCheck(tool="list_documents", args={}),
        PlannedCheck(tool="get_fields", args={"document": "payslip_1", "names": ["net_pay"]}),
    ])

    evidence, calls_by_hypothesis = await agents.gather_evidence(context, [hypothesis])

    results = [e["result"] for e in evidence["h1"]]
    assert results[:2] == [
        '{"available": false, "reason": "Tool error: no result within 0.05 seconds"}',
        '{"available": false, "reason": "Tool error: bad arguments"}',
    ]
    assert '"available": true' in results[2]
    assert [c["available"] for c in calls_by_hypothesis["h1"]] == [False, False, True]


# --- The scope rule and the evidence tools ---------------------------------------------------


@pytest.mark.parametrize("check", [
    {"tool": "salary_credit_match", "args": {"payslip": "payslip_1"}},
    {"tool": "revenue_vs_bank_credits", "args": {"statements": ["bank_statement_1"]}},
    {"tool": "assessed_income_vs_profit", "args": {"notice_of_assessment": "payslip_1"}},
    {"tool": "counterparty_round_trips", "args": {"document": "bank_statement_1"}},
    {"tool": "customer_concentration", "args": {"document": "bank_statement_1"}},
    {"tool": "statement_coverage", "args": {}},
])
async def test_a_hypothesis_checked_only_with_an_evidence_tool_is_in_scope(context, claude, check) -> None:
    claude(calls("submit_hypotheses", hypotheses=[hypothesis_item("Receipts do not look like trading", [check])]))

    hypotheses, _ = await agents.hypothesise(context)

    assert [h.claim for h in hypotheses] == ["Receipts do not look like trading"]
    assert noted(context, "hypotheses")["dropped_for_scope"] == []


# --- The planner's candidates and overview (row 63) -----------------------------------------------------------------


def traced(*documents: DocumentRecord):
    case = build_context("case-1", list(documents), {})
    case.trace = RunTrace("run-1")
    return case


def company_statement(document_id: str, rows: list[dict], **fields) -> DocumentRecord:
    fields = {"entity_type": "corporate", "entity_name": "Brightwave Analytics Pte Ltd",
              "statement_period_start": "01 Oct 2026", "statement_period_end": "31 Oct 2026", **fields}
    return document("bank_statement", document_id, transactions=rows, **fields)


def personal_statement(document_id: str, rows: list[dict]) -> DocumentRecord:
    return document("bank_statement", document_id, entity_type="individual", individual_name="Tan Wei Ming",
                    statement_period_start="01 Oct 2026", statement_period_end="31 Oct 2026", transactions=rows)


def payslip(net_pay: str = "4160.00") -> DocumentRecord:
    return document("payslip", "doc-pay", net_pay=net_pay, pay_date="28 Oct 2026")


def lines(case) -> list[str]:
    return [line for line in playbook.render(case).split("\n") if line]


def credit(day: str, amount: float, name: str = "Orbit Trading Pte Ltd") -> dict:
    return {"date": day, "description": "RECEIPT", "amount": amount, "counterparty": name}


def test_a_net_pay_that_does_not_arrive_is_a_strong_lead_and_one_that_does_is_routine() -> None:
    missing = traced(payslip(), personal_statement("doc-bs", [credit("29 OCT", 3100, "Brightwave")]))
    arrives = traced(payslip(), personal_statement("doc-bs", [credit("28 OCT", 4160, "Brightwave")]))

    [strong] = playbook.candidates(missing)
    [routine] = playbook.candidates(arrives)

    assert strong.strength == 3 and "4,160.00 has no matching credit" in strong.text
    assert "salary_credit_match(payslip=payslip_1, statement=bank_statement_1)" in strong.text
    assert routine.strength == 1 and "appears on bank_statement_1" in routine.text


@pytest.mark.parametrize(("amount", "strength"), [(3000, 3), (10000, 1), (30000, 3)],
                         ids=["under half", "about equal", "over twice"])
def test_annualised_credits_far_from_revenue_are_strong(amount, strength) -> None:
    accounts = document("source_of_wealth", "doc-acc", revenue="120000", financial_year_end="31 Dec 2025")
    case = traced(accounts, company_statement("doc-bs", [credit("05 OCT", amount)]))

    [lead] = playbook.candidates(case)

    assert lead.strength == strength and "revenue_vs_bank_credits(financial_statement=source_of_wealth_1" in lead.text


@pytest.mark.parametrize(("eci", "strength"), [("90000", 3), ("61000", 1), ("40000", 3)],
                         ids=["well over", "close", "well under"])
def test_assessed_income_over_a_quarter_from_profit_is_strong(eci, strength) -> None:
    case = traced(document("notice_of_assessment", "doc-noa", eci=eci),
                  document("source_of_wealth", "doc-acc", net_profit="60000"))

    [lead] = playbook.candidates(case)

    assert lead.strength == strength and "assessed_income_vs_profit(notice_of_assessment=notice_of_assessment_1" in lead.text


def test_round_trips_and_concentrated_receipts_are_worth_testing() -> None:
    rows = [credit(f"0{n} OCT", 5000) for n in range(1, 7)] + [
        {"date": "20 OCT", "description": "PAYMENT", "amount": -12000, "counterparty": "Orbit Trading Pte Ltd"}]
    case = traced(company_statement("doc-bs", rows))

    found = playbook.candidates(case)

    assert [c.strength for c in found] == [2, 2]
    assert "counterparty_round_trips(document=bank_statement_1)" in found[0].text
    assert "100% of the receipts on bank_statement_1" in found[1].text and "customer_concentration(" in found[1].text


def test_concentration_needs_six_credits() -> None:
    case = traced(company_statement("doc-bs", [credit(f"0{n} OCT", 5000) for n in range(1, 6)]))

    assert playbook.candidates(case) == []


def test_a_month_no_statement_covers_is_a_lead_only_with_two_statements() -> None:
    aug = company_statement("doc-a", [], statement_period_start="01 Aug 2026", statement_period_end="31 Aug 2026")
    oct_ = company_statement("doc-b", [])

    [lead] = playbook.candidates(traced(aug, oct_))

    assert lead.strength == 2 and "No statement covers 2026-09" in lead.text
    assert playbook.candidates(traced(aug)) == []


def test_an_instalment_from_a_facility_letter_is_looked_for_and_repayments_without_one_are_noted() -> None:
    repayment = {"date": "15 OCT", "description": "LOAN REPAYMENT", "amount": -1000}
    with_letter = traced(company_statement("doc-bs", [repayment]),
                         document("list_of_loans", "doc-ln", monthly_instalment="1000"))
    without_letter = traced(company_statement("doc-bs", [repayment]))

    [asked] = playbook.candidates(with_letter)
    [noted_only] = playbook.candidates(without_letter)

    assert asked.strength == 2 and "list_of_loans_1 sets a monthly instalment of 1,000.00" in asked.text
    assert noted_only.strength == 1 and "1 loan repayment(s) but the case has no facility letter" in noted_only.text


def test_at_most_the_cap_of_candidates_come_back_strongest_first() -> None:
    rows = [credit(f"0{n} OCT", 5000) for n in range(1, 7)] + [
        {"date": "20 OCT", "description": "LOAN REPAYMENT", "amount": -12000, "counterparty": "Orbit Trading Pte Ltd"}]
    case = traced(
        payslip(), personal_statement("doc-ps", [credit("29 OCT", 3100, "Brightwave")]),
        company_statement("doc-bs", rows), company_statement(
            "doc-bs2", [], statement_period_start="01 Aug 2026", statement_period_end="31 Aug 2026"),
        document("source_of_wealth", "doc-acc", revenue="1200000", net_profit="60000"),
        document("notice_of_assessment", "doc-noa", eci="90000"),
        document("list_of_loans", "doc-ln", monthly_instalment="1000"),
    )

    found = playbook.candidates(case)

    assert len(found) == playbook.MAX_CANDIDATES
    assert [c.strength for c in found] == sorted((c.strength for c in found), reverse=True)
    assert found[0].strength == 3


def test_no_line_of_the_playbook_carries_a_name_or_a_protected_word() -> None:
    rows = [credit(f"0{n} OCT", 5000, "Tan Wei Ming Malay Trading") for n in range(1, 7)] + [
        {"date": "20 OCT", "description": "PAYMENT TO CHINESE MALE DIRECTOR", "amount": -12000,
         "counterparty": "Tan Wei Ming Malay Trading"}]
    case = traced(payslip(), personal_statement("doc-ps", [credit("29 OCT", 3100, "Tan Wei Ming")]),
                  company_statement("doc-bs", rows),
                  document("passport", "doc-pp", full_name="Tan Wei Ming", race="Chinese", nationality="Singaporean"))

    text = playbook.render(case) + "\n" + playbook.overview(case)

    assert playbook.render(case)
    assert not has_protected_text(text)
    assert not any(word in text for word in ("Tan", "Wei", "Ming", "Chinese", "Singaporean"))


def test_the_overview_gives_each_documents_kind_and_period_and_what_the_checklist_lacks() -> None:
    case = traced(payslip(), personal_statement("doc-a", []), company_statement("doc-b", []),
                  document("source_of_wealth", "doc-acc", financial_year_end="31 Dec 2025"))

    overview = playbook.overview(case).split("\n")

    assert "- payslip_1: payslip (pay date 28 Oct 2026)" in overview
    assert "- bank_statement_1: personal bank statement (2026-10 to 2026-10)" in overview
    assert "- bank_statement_2: company bank statement (2026-10 to 2026-10)" in overview
    assert "- source_of_wealth_1: source_of_wealth (financial year end 31 Dec 2025)" in overview
    # Nothing is accepted in this case, so the checklist items are all still missing.
    assert overview[-1].startswith("- Not on file from the checklist: ")


def test_a_document_with_no_period_is_listed_without_one() -> None:
    assert playbook.overview(traced(document("nric", "doc-id"))).split("\n")[0] == "- nric_1: nric"


async def test_the_planner_is_sent_the_overview_and_the_leads_fenced_and_the_playbook_is_traced(claude) -> None:
    case = traced(payslip(), personal_statement("doc-bs", [credit("29 OCT", 3100, "Brightwave")]))
    model = claude(calls("submit_hypotheses", hypotheses=[]))

    await agents.hypothesise(case)

    prompt = model.prompts[0][-1].content
    overview, leads = playbook.overview(case), playbook.render(case)
    assert f'<document_data source="case_overview">\n{overview}\n</document_data>' in prompt
    assert f'<document_data source="candidates">\n{leads}\n</document_data>' in prompt
    assert '<document_data source="case_dossier">' in prompt
    event = noted(case, "playbook")
    assert (event["overview"], event["candidates"]) == (overview, leads)


async def test_a_case_with_no_leads_says_so_to_the_planner(claude) -> None:
    case = traced(document("nric", "doc-id", individual_name="Tan Wei Ming"))
    model = claude(calls("submit_hypotheses", hypotheses=[]))

    await agents.hypothesise(case)

    assert "(none: nothing computed points anywhere)" in model.prompts[0][-1].content
    assert noted(case, "playbook")["candidates"] == ""


async def test_the_overview_and_the_leads_are_cut_to_their_share_of_the_input_budget(claude, monkeypatch) -> None:
    monkeypatch.setattr(agents, "MAX_OVERVIEW_CHARS", 40)
    monkeypatch.setattr(agents, "MAX_CANDIDATE_CHARS", 60)
    case = traced(payslip(), personal_statement("doc-bs", [credit("29 OCT", 3100, "Brightwave")]))
    model = claude(calls("submit_hypotheses", hypotheses=[]))

    await agents.hypothesise(case)

    event = noted(case, "playbook")
    assert len(event["overview"]) <= 40 and len(event["candidates"]) <= 60
    assert event["candidates"] == playbook.render(case)[:60]
    assert len(model.prompts[0][-1].content) < agents.MAX_PLAN_INPUT_CHARS + 6000


async def test_no_protected_attribute_reaches_the_planner(claude) -> None:
    case = traced(document("passport", "doc-pp", full_name="Tan Wei Ming", race="Chinese", religion="Buddhist",
                           nationality="Singaporean", sex="M", passport_number="K1234567A"), payslip(),
                  personal_statement("doc-bs", [credit("29 OCT", 3100, "Brightwave")]))
    model = claude(calls("submit_hypotheses", hypotheses=[]))

    await agents.hypothesise(case)

    sent = " ".join(str(m.content) for m in model.prompts[0])
    # The system prompt names the forbidden attributes; the case's own values never appear.
    assert not any(value in sent for value in ("Chinese", "Buddhist", "Singaporean"))


# --- Open questions (row 64) ----------------------------------------------------------------------------------------


def claim(text: str = "Net pay does not arrive on the statement") -> Hypothesis:
    return Hypothesis(id="h1", claim=text, checks=[PlannedCheck(**check) for check in SALARY_CHECKS])


def test_an_inconclusive_verdict_with_a_document_that_would_settle_it_becomes_a_question() -> None:
    verdict = {"id": "h1", "conclusive": False, "would_settle_with": "  The October   payroll\nregister "}

    assert _open_question(claim(), verdict) == {
        "question": "Net pay does not arrive on the statement", "would_settle_with": "The October payroll register"}


def test_a_verdict_that_did_not_say_conclusive_is_still_open() -> None:
    assert _open_question(claim(), {"id": "h1", "would_settle_with": "Payroll register"}) is not None


def test_a_conclusive_verdict_or_none_at_all_is_not_a_question() -> None:
    assert _open_question(claim(), {"conclusive": True, "would_settle_with": "Payroll register"}) is None
    assert _open_question(claim(), None) is None


@pytest.mark.parametrize("settle", [None, "", "   \n ", 0])
def test_a_verdict_naming_no_document_is_not_a_question(settle) -> None:
    assert _open_question(claim(), {"conclusive": False, "would_settle_with": settle}) is None


@pytest.mark.parametrize(("text", "settle"), [
    ("The director's name suggests he is foreign", "Payroll register"),
    ("Net pay does not arrive on the statement", "Marital status certificate"),
    ("Net pay does not arrive on the statement", "Proof of the employee's race"),
])
def test_a_claim_or_document_touching_a_protected_attribute_is_not_a_question(text, settle) -> None:
    assert _open_question(claim(text), {"conclusive": False, "would_settle_with": settle}) is None


def test_a_question_is_cut_to_its_lengths() -> None:
    question = _open_question(claim("c" * 500), {"conclusive": False, "would_settle_with": "d" * 500})

    assert (len(question["question"]), len(question["would_settle_with"])) == (300, 200)
