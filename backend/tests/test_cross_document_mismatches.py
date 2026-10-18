"""A mismatch a comparison tool flags is reported as its own finding, without a separate refutation probe."""

from __future__ import annotations

from app.case_pipeline.cross_document.evidence import CaseContext, DocView
from app.case_pipeline.cross_document.verify import verify_verdict
from app.case_pipeline.models import Hypothesis, PlannedCheck


def _view(key: str, **fields) -> DocView:
    return DocView(key=key, id=f"id-{key}", filename=f"{key}.pdf", document_type=key.rsplit("_", 1)[0],
                   fields=fields, findings=[], fields_confirmed_by_officer=False)


def _context() -> CaseContext:
    return CaseContext(case_id="c", docs={"noa_1": _view("noa_1"), "bank_statement_1": _view("bank_statement_1")})


def _flagged(context: CaseContext, flag: str, field: str = "company_name") -> list[str]:
    return [
        context.ledger.issue(key, field, "X", source="company_name_consistency_tool", breach_flag=flag)
        for key in context.docs
    ]


def _hypothesis() -> Hypothesis:
    # Only the mismatch check itself ran: there is no separate probe.
    return Hypothesis(id="h1", claim="names differ", checks=[PlannedCheck(tool="company_name_consistency_tool")])


def _verdict(citations: list[str], severity: str = "medium") -> dict:
    return {"conclusive": True, "citations": citations, "severity": severity, "summary": "names differ", "rationale": "r"}


def test_a_flagged_name_mismatch_stands_without_a_refutation_probe() -> None:
    context = _context()
    refs = _flagged(context, "NAME_MISMATCH")

    finding, reason = verify_verdict(context, _hypothesis(), _verdict(refs), [])

    assert reason == "" and finding is not None
    assert finding.category == "cross_document_variance"


def test_a_name_mismatch_cannot_be_high_but_an_identifier_conflict_can() -> None:
    context = _context()
    name = verify_verdict(context, _hypothesis(), _verdict(_flagged(context, "NAME_MISMATCH"), "high"), [])[0]
    ident = verify_verdict(context, _hypothesis(), _verdict(_flagged(context, "IDENTIFIER_CONFLICT", "uen"), "high"), [])[0]

    assert name is not None and name.severity == "medium"
    assert ident is not None and ident.severity == "high"


def test_a_claim_with_no_mismatch_flag_still_needs_its_probe() -> None:
    context = _context()
    refs = [context.ledger.issue(key, "company_name", "X", source="get_fields") for key in context.docs]

    finding, reason = verify_verdict(context, _hypothesis(), _verdict(refs), [])

    assert finding is None and reason == "refutation probe did not run"


def test_consistent_comparisons_become_plain_lines() -> None:
    from app.case_pipeline.cross_document.graph import _consistent_line

    context = _context()
    name = {"tool": "company_name_consistency_tool", "args": {"document": "noa_1"}}
    uen = {"tool": "compare_identifiers", "args": {"items": [
        {"document": "noa_1", "field": "uen"}, {"document": "bank_statement_1", "field": "uen"},
    ]}}

    assert _consistent_line(context, name) == "Company name on noa_1.pdf match the other documents"
    assert _consistent_line(context, uen) == "uen is the same on noa_1.pdf, bank_statement_1.pdf"
    assert _consistent_line(context, {"tool": "monthly_flow", "args": {}}) is None
