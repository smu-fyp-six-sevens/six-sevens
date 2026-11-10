"""
The code-side check of a case analysis verdict (case_pipeline/cross_document/verify.py): it stands only if the
evidence it cites was issued by a tool, a real attempt to disprove it ran, and its severity and category fit the
evidence. The model's own account is never trusted, so each way a verdict can fall short is tested here.
"""
from __future__ import annotations

import pytest

from app.case_pipeline.cross_document.evidence import CaseContext, DocView
from app.case_pipeline.cross_document.verify import plain_text, verify_verdict
from app.case_pipeline.models import FindingCategory, Hypothesis

PAYSLIP, STATEMENT, NOA = "payslip_1", "bank_statement_1", "noa_1"


def view(key: str) -> DocView:
    return DocView(key=key, id=f"id-{key}", filename=f"{key}.pdf", document_type=key.rsplit("_", 1)[0],
                   fields={}, findings=[], fields_confirmed_by_officer=False)


@pytest.fixture
def context() -> CaseContext:
    return CaseContext(case_id="case-1", docs={key: view(key) for key in (PAYSLIP, STATEMENT, NOA)})


def hypothesis(refutation_document: str | None = None, claim: str = "The salary credits fall short of net pay") -> Hypothesis:
    return Hypothesis(id="h1", claim=claim, refutation_document=refutation_document)


def verdict(citations: list[str], **overrides) -> dict:
    return {"conclusive": True, "citations": citations, "severity": "medium", "category": "behavioural_anomaly",
            "summary": "Salary credits fall short of the payslip's net pay.", "rationale": "Three months short.",
            **overrides}


def read(context: CaseContext, document: str, field: str, *, probe: bool = False, available: bool = True,
         tool: str = "get_fields") -> dict:
    """A logged tool call that read one field, as ToolKit.log records it."""
    ref = context.ledger.issue(document, field, "value", source=tool)
    return {"tool": tool, "document": document, "probe": probe, "available": available, "refs": [ref]}


def evidence(context: CaseContext) -> tuple[list[str], list[dict]]:
    """Two documents' fields read in support, and a probe on a third field: a verdict that stands."""
    calls = [read(context, PAYSLIP, "net_pay"), read(context, STATEMENT, "transactions")]
    calls.append(read(context, STATEMENT, "statement_period_start", probe=True))
    return [calls[0]["refs"][0], calls[1]["refs"][0]], calls


def test_a_verdict_with_cited_evidence_from_two_documents_and_a_probe_stands(context) -> None:
    citations, calls = evidence(context)

    finding, reason = verify_verdict(context, hypothesis(), verdict(citations, confidence=0.7), calls)

    assert reason == ""
    assert (finding.id, finding.severity, finding.category) == ("xd-h1", "medium", FindingCategory.BEHAVIOURAL_ANOMALY)
    assert finding.document_ids == ["id-payslip_1", "id-bank_statement_1"]
    assert (finding.citations, finding.confidence) == (citations, 0.7)
    # Each cited document's field and value, for the officer's side-by-side view.
    assert [(c.document_id, c.field, c.value) for c in finding.cited_values] == [
        ("id-payslip_1", "net_pay", "value"), ("id-bank_statement_1", "transactions", "value"),
    ]


def test_a_cited_table_or_computed_summary_is_not_a_value_to_find_on_the_page(context) -> None:
    plain = context.ledger.issue(PAYSLIP, "net_pay", 4200.5)
    table = context.ledger.issue(STATEMENT, "transactions", [{"amount": 1}])
    missing = context.ledger.issue(NOA, "assessable_income", None)

    assert [(c.field, c.value) for c in context.cited_values([plain, table, missing, "ref:unknown"])] == [("net_pay", "4200.5")]


# --- Discarded --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("change, reason", [
    ({"conclusive": False}, "not conclusive"),
    ({"conclusive": "yes"}, "not conclusive"),
    ({"citations": []}, "no citations"),
    ({"citations": [42, None]}, "no citations"),
    ({"citations": ["ref:payslip_1:gross_pay"]}, "citation not issued by a tool: ref:payslip_1:gross_pay"),
])
def test_a_verdict_that_is_not_conclusive_or_cites_nothing_real_is_discarded(context, change, reason) -> None:
    citations, calls = evidence(context)

    finding, why = verify_verdict(context, hypothesis(), {**verdict(citations), **change}, calls)

    assert (finding, why) == (None, reason)


def test_a_verdict_resting_on_a_protected_attribute_is_discarded(context) -> None:
    citations, calls = evidence(context)

    finding, why = verify_verdict(
        context, hypothesis(), verdict(citations, rationale="The director's name suggests he is not local."), calls)

    assert (finding, why) == (None, "rests on a protected attribute")


def test_one_documents_own_fields_are_not_a_cross_document_finding(context) -> None:
    calls = [read(context, PAYSLIP, "net_pay"), read(context, PAYSLIP, "employer_name", probe=True)]

    finding, why = verify_verdict(context, hypothesis(), verdict(calls[0]["refs"]), calls)

    assert (finding, why) == (None, "cites a single document and no computed fact")


def test_a_fact_computed_over_one_statement_is_evidence_on_its_own(context) -> None:
    stats = read(context, STATEMENT, "transaction_stats[credit]", tool="transaction_stats")
    probe = read(context, STATEMENT, "statement_period_end", probe=True)

    finding, why = verify_verdict(context, hypothesis(), verdict(stats["refs"]), [stats, probe])

    assert why == ""
    assert finding.document_ids == ["id-bank_statement_1"]


# --- The refutation probe ---------------------------------------------------------------------------------------


def test_with_no_probe_the_verdict_is_discarded(context) -> None:
    calls = [read(context, PAYSLIP, "net_pay"), read(context, STATEMENT, "transactions")]

    finding, why = verify_verdict(context, hypothesis(), verdict([c["refs"][0] for c in calls]), calls)

    assert (finding, why) == (None, "refutation probe did not run")


def test_a_probe_that_rereads_the_supporting_fields_tests_nothing(context) -> None:
    calls = [read(context, PAYSLIP, "net_pay"), read(context, STATEMENT, "transactions")]
    calls.append(read(context, PAYSLIP, "net_pay", probe=True))

    assert verify_verdict(context, hypothesis(), verdict([c["refs"][0] for c in calls[:2]]), calls)[1] == (
        "refutation probe did not run")


def test_a_probe_that_found_its_document_unreadable_does_not_count(context) -> None:
    calls = [read(context, PAYSLIP, "net_pay"), read(context, STATEMENT, "transactions")]
    calls.append(read(context, STATEMENT, "statement_period_start", probe=True, available=False))

    assert verify_verdict(context, hypothesis(), verdict([c["refs"][0] for c in calls[:2]]), calls)[1] == (
        "refutation probe did not run")


def test_a_probe_must_look_where_the_claim_said_it_could_be_shown_wrong(context) -> None:
    citations, calls = evidence(context)  # the probe reads the bank statement

    elsewhere = verify_verdict(context, hypothesis(refutation_document=NOA), verdict(citations), calls)
    there = verify_verdict(context, hypothesis(refutation_document=STATEMENT), verdict(citations), calls)

    assert elsewhere == (None, "refutation probe did not run")
    assert there[1] == ""


def test_a_computed_probe_counts_even_on_the_supporting_document(context) -> None:
    calls = [read(context, PAYSLIP, "net_pay"), read(context, STATEMENT, "transactions")]
    calls.append(read(context, STATEMENT, "monthly_flow", probe=True, tool="monthly_flow"))

    assert verify_verdict(context, hypothesis(STATEMENT), verdict([c["refs"][0] for c in calls[:2]]), calls)[1] == ""


# --- Severity and category ----------------------------------------------------------------------------------------


def test_high_needs_a_hard_breach_or_a_registry_fact(context) -> None:
    citations, calls = evidence(context)
    registry = context.ledger.issue(NOA, "uen_registry", "ACRA: struck off", source="get_business_profile",
                                    category=FindingCategory.REGISTRY_DISCREPANCY)
    timeline = context.ledger.issue(STATEMENT, "statement_period", "x", source="compare_periods",
                                    breach_flag="CHRONOLOGICAL_IMPOSSIBILITY")

    plain = verify_verdict(context, hypothesis(), verdict(citations, severity="high"), calls)[0]
    from_registry = verify_verdict(context, hypothesis(), verdict([*citations, registry], severity="high"), calls)[0]
    breached = verify_verdict(context, hypothesis(), verdict([*citations, timeline], severity="high"), calls)[0]

    assert (plain.severity, from_registry.severity, breached.severity) == ("medium", "high", "high")
    assert breached.breach_flags == ["CHRONOLOGICAL_IMPOSSIBILITY"]


def test_an_unknown_severity_reads_as_medium(context) -> None:
    citations, calls = evidence(context)

    assert verify_verdict(context, hypothesis(), verdict(citations, severity="critical"), calls)[0].severity == "medium"


@pytest.mark.parametrize("proposed, kept", [
    ("unsupported_claim", FindingCategory.UNSUPPORTED_CLAIM),
    ("tampering_forensic", FindingCategory.BEHAVIOURAL_ANOMALY),  # not the model's to choose
    ("made_up", FindingCategory.BEHAVIOURAL_ANOMALY),
    (None, FindingCategory.BEHAVIOURAL_ANOMALY),
])
def test_the_model_may_only_choose_its_own_categories(context, proposed, kept) -> None:
    citations, calls = evidence(context)

    assert verify_verdict(context, hypothesis(), verdict(citations, category=proposed), calls)[0].category == kept


def test_a_flagged_identifier_conflict_is_an_identity_finding_whatever_the_model_says(context) -> None:
    refs = [context.ledger.issue(key, "uen", "X", source="compare_identifiers", breach_flag="IDENTIFIER_CONFLICT")
            for key in (PAYSLIP, STATEMENT)]

    finding = verify_verdict(context, hypothesis(), verdict(refs, category="unsupported_claim"), [])[0]

    assert finding.category == FindingCategory.IDENTITY_ENTITY_CONFLICT


def test_citations_are_capped_and_each_document_named_once(context) -> None:
    citations, calls = evidence(context)
    extra = [context.ledger.issue(STATEMENT, "transactions", n, row=n, source="query_transactions") for n in range(20)]

    finding = verify_verdict(context, hypothesis(), verdict([*citations, *extra]), calls)[0]

    assert len(finding.citations) == 12
    assert finding.document_ids == ["id-payslip_1", "id-bank_statement_1"]


# --- What the officer reads ---------------------------------------------------------------------------------------


def test_the_officer_reads_plain_words_not_tool_names_flags_or_ref_ids() -> None:
    text = ("The company_name_consistency_tool (ref:noa_1:uen) shows an IDENTIFIER_CONFLICT , and the refutation "
            "probe found no explanation.")

    assert plain_text(text) == "The check shows the mismatch, and the cross-check found no explanation."


def test_a_named_tool_after_an_article_reads_as_one_article() -> None:
    assert plain_text("The compare_identifiers result differs.") == "The check result differs."


def test_the_findings_sentences_are_rewritten_in_plain_words(context) -> None:
    citations, calls = evidence(context)

    finding = verify_verdict(context, hypothesis(), verdict(
        citations, summary="query_transactions (ref:bank_statement_1:transactions) shows no salary credit",
        probe_summary="The refutation check read the statement period."), calls)[0]

    assert finding.summary == "the check shows no salary credit"
    assert finding.probe_summary == "The cross-check read the statement period."
