"""The code-side comparisons always run, give one finding per difference, and are bounded when shown to the agent."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.case_pipeline.cross_document import graph
from app.case_pipeline.cross_document.agents import MAX_JUDGE_EVIDENCE_CHARS, fit_evidence
from app.case_pipeline.cross_document.deterministic import MAX_BRIEF_LINES, brief, run_deterministic_checks
from app.case_pipeline.cross_document.evidence import build_context
from app.models import DocumentRecord, DocumentStatus


def _doc(doc_id: str, document_type: str, fields: dict) -> DocumentRecord:
    return DocumentRecord(
        id=doc_id, case_id="c", filename=f"{doc_id}.pdf", document_type=document_type, status=DocumentStatus.DETECTED,
        uploaded_at=datetime(2026, 9, 1, tzinfo=timezone.utc), extracted_fields=fields,
    )


def _context(*docs: DocumentRecord):
    return build_context("c", list(docs), {})


def test_each_difference_is_its_own_finding() -> None:
    context = _context(
        _doc("noa", "income_tax_notice", {"company_name": "BRIGHTSTONE ANALYTICS PTE LTD", "uen": "201912345K"}),
        _doc("coi", "certificate_of_incorporation", {"company_name": "BRIGHTWAVE ANALYTICS PTE LTD", "uen": "202054321M"}),
    )

    result = run_deterministic_checks(context)

    kinds = sorted((f.category.value, f.severity) for f in result.findings)
    assert ("identity_entity_conflict", "high") in kinds  # the UEN
    assert ("cross_document_variance", "medium") in kinds  # the company name
    assert len(result.findings) == 2
    assert all(f.citations and f.hypothesis_id == "deterministic" for f in result.findings)
    assert len({f.id for f in result.findings}) == 2


def test_one_conflicting_name_on_two_documents_is_still_one_finding() -> None:
    context = _context(
        _doc("a", "bank_statement", {"company_name": "OTHER CO PTE LTD"}),
        _doc("b", "payslip", {"company_name": "OTHER CO PTE LTD"}),
        _doc("c", "certificate_of_incorporation", {"company_name": "REAL CO PTE LTD"}),
    )

    names = [f for f in run_deterministic_checks(context).findings if "Company name" in f.summary]

    assert len(names) == 1


def test_matching_documents_give_consistent_lines_and_no_findings() -> None:
    context = _context(
        _doc("a", "certificate_of_incorporation", {"company_name": "ACME PTE LTD", "uen": "201912345K"}),
        _doc("b", "bank_statement", {"company_name": "Acme Pte. Ltd.", "uen": "201912345K"}),
    )

    result = run_deterministic_checks(context)

    assert result.findings == []
    assert any("Company name is the same" in line for line in result.consistent)
    assert any("UEN is the same" in line for line in result.consistent)


def test_person_id_and_name_are_separate_findings() -> None:
    context = _context(
        _doc("nric", "nric", {"nric_number": "S1234567A", "individual_name": "Goh Ka Yee"}),
        _doc("pay", "payslip", {"nric_number": "S7654321B", "individual_name": "Goh Ka Yee"}),
    )

    findings = run_deterministic_checks(context).findings

    assert [f.severity for f in findings] == ["high"]
    assert "ID number" in findings[0].summary


def test_the_brief_for_the_agent_is_bounded() -> None:
    context = _context(*(
        _doc(f"d{i}", "certificate_of_incorporation", {"company_name": f"COMPANY NUMBER {i} PTE LTD"}) for i in range(8)
    ))

    text = brief(run_deterministic_checks(context), context)

    assert len(text.splitlines()) <= 2 * MAX_BRIEF_LINES + 1
    assert all(len(line) <= 300 for line in text.splitlines())


def test_judge_evidence_is_shortened_to_the_limit() -> None:
    big = {f"h{i}": [{"tool": "t", "result": "x" * 900} for _ in range(4)] for i in range(3)}

    fitted = fit_evidence(big)

    assert sum(len(e["result"]) for items in fitted.values() for e in items) <= MAX_JUDGE_EVIDENCE_CHARS
    assert fit_evidence({"h1": [{"result": "short"}]}) == {"h1": [{"result": "short"}]}


async def test_analysis_runs_the_code_checks_whatever_the_agent_proposes() -> None:
    context = _context(
        _doc("a", "certificate_of_incorporation", {"company_name": "ACME PTE LTD", "uen": "201912345K"}),
        _doc("b", "bank_statement", {"company_name": "ACME PTE LTD", "uen": "202054321M"}),
    )

    with patch.object(graph, "hypothesise", AsyncMock(return_value=[])):
        state = await graph.run_cross_document_analysis(context)

    assert [f.hypothesis_id for f in state["findings"]] == ["deterministic"]
    assert "(identifier)" in context.established


def test_the_agent_sees_one_line_for_one_disagreement_not_one_per_pair() -> None:
    context = _context(
        _doc("a", "certificate_of_incorporation", {"company_name": "ALPHA ANALYTICS PTE LTD"}),
        _doc("b", "bank_statement", {"company_name": "BETA ANALYTICS PTE LTD"}),
        _doc("c", "payslip", {"company_name": "BETA ANALYTICS PTE LTD"}),
        _doc("d", "list_of_loans", {"company_name": "GAMMA ANALYTICS PTE LTD"}),
    )
    result = run_deterministic_checks(context)

    text = brief(result, context)
    differs = [line for line in text.splitlines() if line.startswith("- DIFFERS")]

    assert len(result.findings) == 3  # the officer still gets a row for each pair
    assert len(differs) == 1
    assert 'b.pdf, c.pdf have "BETA ANALYTICS PTE LTD"' in differs[0]
    assert 'a.pdf has "ALPHA ANALYTICS PTE LTD"' in differs[0]
    assert 'd.pdf has "GAMMA ANALYTICS PTE LTD"' in differs[0]
