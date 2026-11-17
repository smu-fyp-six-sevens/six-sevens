"""A per-document finding whose figure another document should carry is shown to the planner as a lead."""

from __future__ import annotations

from app.case_pipeline.cross_document import agents
from app.case_pipeline.cross_document.evidence import CaseContext, DocView, render_dossier
from app.case_pipeline.cross_document.leads import lead_for

TAX_FINDING = {
    "id": "numerical_checks_tool-2", "tool": "numerical_checks_tool", "severity": "medium",
    "summary": "Tax expense is a flat 17% of profit before tax", "check_code": "FS.TAX_EXPENSE_RATE",
}


def _view(key: str, document_type: str, findings=()) -> DocView:
    return DocView(key=key, id=f"id-{key}", filename=f"{key}.pdf", document_type=document_type,
                   fields={}, findings=[{**f, "category": "tampering_forensic"} for f in findings],
                   fields_confirmed_by_officer=False)


def _docs(*views: DocView) -> dict[str, DocView]:
    return {v.key: v for v in views}


def test_a_tax_finding_leads_to_the_notice_of_assessment() -> None:
    docs = _docs(_view("source_of_wealth_1", "source_of_wealth"), _view("notice_of_assessment_1", "notice_of_assessment"))

    lead = lead_for(TAX_FINDING, docs)

    assert lead is not None
    assert "notice_of_assessment_1" in lead and "tax_payable" in lead and "do not restate" in lead


def test_no_lead_when_the_case_has_no_document_to_test_it_against() -> None:
    assert lead_for(TAX_FINDING, _docs(_view("source_of_wealth_1", "source_of_wealth"))) is None


def test_no_lead_for_a_finding_that_has_no_counterpart() -> None:
    docs = _docs(_view("notice_of_assessment_1", "notice_of_assessment"))

    assert lead_for({"check_code": "FS.BS_CAST"}, docs) is None
    assert lead_for({"summary": "no code"}, docs) is None


def test_the_check_code_is_also_read_from_nested_details() -> None:
    docs = _docs(_view("notice_of_assessment_1", "notice_of_assessment"))

    assert "notice_of_assessment_1" in (lead_for({"details": {"check_code": "FS.TAX_EXPENSE_RATE"}}, docs) or "")


def test_a_payslip_finding_leads_to_the_bank_statement() -> None:
    docs = _docs(_view("payslip_1", "payslip"), _view("bank_statement_1", "bank_statement"))

    assert "bank_statement_1" in (lead_for({"check_code": "PAY.NET"}, docs) or "")


def test_the_dossier_shows_the_lead_under_its_finding() -> None:
    docs = _docs(
        _view("source_of_wealth_1", "source_of_wealth", [TAX_FINDING]), _view("notice_of_assessment_1", "notice_of_assessment"),
    )

    lines = render_dossier(CaseContext(case_id="c", docs=docs)).splitlines()

    finding_line = next(i for i, line in enumerate(lines) if "flat 17%" in line)
    assert lines[finding_line + 1].strip().startswith("lead: test this figure against notice_of_assessment_1")


def test_the_plan_instructions_tell_the_planner_to_use_the_leads() -> None:
    assert "`lead:`" in agents._PLAN_INSTRUCTIONS
    assert agents.PROMPT_VERSION == "cross-doc-v11-playbook"


def test_confidence_tool_schemas_avoid_unsupported_numeric_bounds() -> None:
    for tool in (agents._PLAN_TOOL, agents._VERDICT_TOOL):
        assert "minimum" not in repr(tool["input_schema"])
        assert "maximum" not in repr(tool["input_schema"])
