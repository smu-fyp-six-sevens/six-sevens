"""The code-side comparisons always run, give one finding per difference, and are bounded when shown to the agent."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.case_pipeline.cross_document import graph
from app.case_pipeline.cross_document.agents import MAX_JUDGE_EVIDENCE_CHARS, fit_evidence
from app.case_pipeline.cross_document.deterministic import MAX_BRIEF_LINES, brief, run_deterministic_checks
from app.case_pipeline.cross_document.evidence import build_context
from app.models import DocumentRecord, DocumentStatus

FIXTURES = Path(__file__).parent / "fixtures" / "regression"


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

    names = [f for f in run_deterministic_checks(context).findings if "company name" in f.summary]

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

    with patch.object(graph, "hypothesise", AsyncMock(return_value=([], 0.0))):
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


# --- personal details of one person across documents -----------------------------


def _person_docs(**credit_report) -> list[DocumentRecord]:
    return [
        _doc("nric", "nric", {
            "nric_number": "S8427269H", "individual_name": "LAKSHMI D/O RAJENDRAN", "race": "INDIAN",
            "date_of_birth": "13-02-1984", "sex": "F", "nationality": "SINGAPOREAN", "country": "SINGAPORE",
        }),
        _doc("cbr", "credit_report", {
            "id_number": "S8427269H", "individual_name": "Lakshmi d/o Rajendran", "date_of_birth": "13/02/1984",
            "nationality": "Singaporean", **credit_report,
        }),
    ]


def test_the_same_details_in_different_formats_are_not_a_difference() -> None:
    assert run_deterministic_checks(_context(*_person_docs())).findings == []


def test_a_different_id_number_is_flagged_high() -> None:
    findings = run_deterministic_checks(_context(*_person_docs(id_number="S8417611G"))).findings

    assert [(f.severity, f.category.value) for f in findings] == [("high", "identity_entity_conflict")]
    assert "ID number" in findings[0].summary


def test_a_finding_records_each_documents_value_unmasked_for_the_compare_view() -> None:
    finding = run_deterministic_checks(_context(*_person_docs(id_number="S8417611G"))).findings[0]

    # The summary masks the ID numbers; the officer's side-by-side view needs the real ones to find on the page.
    assert "S8417611G" not in finding.summary
    assert {(c.document_id, c.value) for c in finding.cited_values} == {("nric", "S8427269H"), ("cbr", "S8417611G")}


def test_a_different_birth_date_race_sex_or_nationality_is_flagged() -> None:
    docs = _person_docs(date_of_birth="14/02/1984", race="CHINESE", sex="M", nationality="Malaysian")
    findings = run_deterministic_checks(_context(*docs)).findings

    summaries = " ".join(f.summary for f in findings).lower()
    assert len(findings) == 4
    for label in ("date of birth", "race", "sex", "nationality"):
        assert label in summaries
    assert {f.severity for f in findings} == {"high", "medium"}


def test_a_detail_only_one_document_states_is_not_compared() -> None:
    docs = _person_docs()
    docs[1].extracted_fields.pop("date_of_birth")  # type: ignore[union-attr]

    assert run_deterministic_checks(_context(*docs)).findings == []


def test_personal_detail_differences_are_never_shown_to_a_model() -> None:
    from app.case_pipeline.cross_document.bias import WITHHELD, withhold_if_protected

    context = _context(*_person_docs(id_number="S8417611G", date_of_birth="14/02/1984", sex="M"))
    result = run_deterministic_checks(context)

    text = brief(result, context)
    assert "birth" not in text.lower() and "sex" not in text.lower()
    assert "ID number" not in text or "identifier" in text  # the ID difference is still told
    assert any("date of birth" in f.summary for f in result.findings)
    assert withhold_if_protected(next(f.summary for f in result.findings if "date of birth" in f.summary)) == WITHHELD
    assert withhold_if_protected("UEN 201298459R is not in ACRA's register") == "UEN 201298459R is not in ACRA's register"


def test_values_that_differ_only_by_case_or_line_breaks_are_one_value_for_the_agent() -> None:
    context = _context(
        _doc("a", "certificate_of_incorporation", {"company_name": "ACME PTE LTD", "company_address": "10 Bedok North, Singapore 460022"}),
        _doc("b", "company_constitution", {"company_name": "ACME PTE LTD", "company_address": "10 BEDOK NORTH,\nSINGAPORE 460022"}),
        _doc("c", "bank_statement", {"company_name": "ACME PTE LTD", "company_address": "20 Bedok North, Singapore 460023"}),
    )

    differs = [line for line in brief(run_deterministic_checks(context), context).splitlines() if "(address)" in line]

    assert len(differs) == 1
    assert differs[0].count('"') == 4  # two values, not three


# --- a person is compared with every document that names them, not only an NRIC ------


def _incumbency(**fields) -> DocumentRecord:
    return _doc("inc", "certificate_of_incumbency", {
        "company_name": "ACME PTE LTD", "uen": "201912345K", "director_name": "LAKSHMI D/O RAJENDRAN",
        "director_id_number": "S8427269H", **fields,
    })


def test_a_wrong_id_is_found_against_the_certificate_of_incumbency_when_there_is_no_nric() -> None:
    credit_report = _person_docs(id_number="S8417611G")[1]

    findings = run_deterministic_checks(_context(_incumbency(), credit_report)).findings

    assert [f.severity for f in findings] == ["high"]
    assert "ID number" in findings[0].summary


def test_one_wrong_id_against_an_nric_and_an_incumbency_is_one_finding_citing_all_three() -> None:
    nric, credit_report = _person_docs(id_number="S8417611G")

    findings = run_deterministic_checks(_context(nric, credit_report, _incumbency())).findings

    assert len(findings) == 1
    assert len(findings[0].document_ids) == 3


def test_an_nric_is_not_required_for_a_name_to_be_compared() -> None:
    payslip = _doc("pay", "payslip", {"individual_name": "Lakshmi Rajendran Pte", "company_name": "ACME PTE LTD"})
    credit_report = _person_docs(individual_name="Wei Ming Tan")[1]

    findings = run_deterministic_checks(_context(_incumbency(), credit_report, payslip)).findings

    assert any("name" in f.summary for f in findings)


# --- readable findings, one row for each fact -------------------------------------


def test_findings_read_as_sentences_about_the_documents() -> None:
    context = _context(*_person_docs(id_number="S8417611G"), _incumbency())

    [finding] = run_deterministic_checks(context).findings

    assert finding.summary == 'The ID number is "S****611G" on the credit report, but "S****269H" on the certificate of incumbency and the NRIC.'
    assert "same person" in finding.rationale and "cbr.pdf" in finding.rationale
    assert "_tool" not in finding.summary + finding.rationale


def test_addresses_that_differ_only_in_formatting_are_one_finding() -> None:
    context = _context(
        _doc("a", "certificate_of_incumbency", {"company_name": "ACME PTE LTD", "company_address": "7 Temasek Boulevard, #12-14, Singapore 038987"}),
        _doc("b", "company_constitution", {"company_name": "ACME PTE LTD", "company_address": "21 Media Circle, #03-05, Singapore 138562"}),
        _doc("c", "notice_of_assessment", {"company_name": "ACME PTE LTD", "company_address": "21 MEDIA CIRCLE\n#03-05\nSINGAPORE 138562"}),
    )

    findings = [f for f in run_deterministic_checks(context).findings if "address" in f.summary]

    assert len(findings) == 1
    assert "notice of assessment" in findings[0].summary and "company constitution" in findings[0].summary


def test_a_personal_address_finding_names_whose_address_it_is() -> None:
    # Unnamed ("The address is ..."), the evidence request read it as the company's address and asked for one.
    context = _context(
        _doc("a", "bank_statement", {"entity_type": "individual", "individual_name": "LAKSHMI D/O RAJENDRAN",
                                     "individual_address": "236 Serangoon Avenue 3, #07-118, Singapore 550236"}),
        _doc("b", "credit_report", {"individual_name": "Lakshmi d/o Rajendran", "id_number": "S8427269H",
                                    "individual_address": "4 Toh Yi Drive, #04-11, Singapore 590004"}),
    )

    [finding] = [f for f in run_deterministic_checks(context).findings if "address" in f.summary]

    assert finding.summary.startswith("Lakshmi d/o Rajendran's address is ")


_OLD = "236 Serangoon Avenue 3, #07-118, Singapore 550236"
_CURRENT = "4 Toh Yi Drive, #04-11, Singapore 590004"


def _personal_statement(doc_id: str, address: str, period_end: str | None) -> DocumentRecord:
    fields = {"entity_type": "individual", "individual_name": "LAKSHMI D/O RAJENDRAN", "individual_address": address}
    if period_end:
        fields["statement_period_end"] = period_end
    return _doc(doc_id, "bank_statement", fields)


def _credit_report(address: str = _CURRENT) -> DocumentRecord:
    return _doc("cbr", "credit_report", {"individual_name": "Lakshmi d/o Rajendran", "id_number": "S8427269H",
                                         "individual_address": address, "report_date": "29 Aug 2026"})


def _address_findings(context) -> list:
    return [f for f in run_deterministic_checks(context).findings if "address" in f.summary]


def test_an_old_statement_against_the_rest_of_the_case_is_a_finding_that_asks_for_recent_proof() -> None:
    context = _context(_personal_statement("old", _OLD, "28 FEB 2026"), _credit_report())

    [finding] = _address_findings(context)

    assert "recent proof of address" in finding.rationale


def test_a_newer_statement_with_the_current_address_makes_the_old_one_a_previous_address() -> None:
    context = _context(_personal_statement("old", _OLD, "28 FEB 2026"), _credit_report(),
                       _personal_statement("new", _CURRENT, "31 AUG 2026"))
    result = run_deterministic_checks(context)

    assert [f for f in result.findings if "address" in f.summary] == []
    assert any("is a previous address" in line for line in result.consistent)


def test_a_newer_utility_bill_with_the_current_address_also_settles_it() -> None:
    bill = _doc("bill", "proof_of_address", {"entity_type": "individual", "individual_name": "Lakshmi d/o Rajendran",
                                             "individual_address": _CURRENT, "bill_date": "07 Sep 2026"})
    context = _context(_personal_statement("old", _OLD, "28 FEB 2026"), _credit_report(), bill)

    assert _address_findings(context) == []


def test_the_odd_address_on_the_newest_document_is_still_a_finding() -> None:
    context = _context(_personal_statement("old", _CURRENT, "28 FEB 2026"), _credit_report(),
                       _personal_statement("new", _OLD, "31 AUG 2026"))

    assert len(_address_findings(context)) == 1


def test_an_old_address_also_on_a_credit_report_is_still_a_finding() -> None:
    # Only proofs of address can be superseded: a credit report keeping the other address is a real conflict.
    context = _context(_personal_statement("old", _OLD, "28 FEB 2026"), _credit_report(_OLD),
                       _personal_statement("new", _CURRENT, "31 AUG 2026"))

    assert len(_address_findings(context)) == 1


def test_an_undated_old_statement_is_still_a_finding() -> None:
    context = _context(_personal_statement("old", _OLD, None), _credit_report(),
                       _personal_statement("new", _CURRENT, "31 AUG 2026"))

    assert len(_address_findings(context)) == 1


def test_an_agent_finding_that_restates_a_code_finding_is_dropped() -> None:
    from app.case_pipeline.cross_document.deterministic import restates_a_code_finding
    from app.case_pipeline.models import CrossDocFinding, FindingCategory

    def finding(finding_id: str, flags: list[str], documents: list[str]) -> CrossDocFinding:
        return CrossDocFinding(
            id=finding_id, hypothesis_id="h", summary="s", rationale="r", severity="high",
            category=FindingCategory.IDENTITY_ENTITY_CONFLICT, document_ids=documents, breach_flags=flags,
        )

    code = [finding("xd-det-1", ["IDENTIFIER_CONFLICT"], ["cbr", "nric", "inc"])]

    assert restates_a_code_finding(code, finding("xd-h1", ["IDENTIFIER_CONFLICT"], ["cbr", "nric"]))
    assert not restates_a_code_finding(code, finding("xd-h2", ["IDENTIFIER_CONFLICT"], ["bank"]))  # other documents
    assert not restates_a_code_finding(code, finding("xd-h3", ["PERIOD_OVERLAP"], ["cbr"]))  # a different kind of fact
    assert not restates_a_code_finding(code, finding("xd-h4", [], ["cbr", "nric"]))  # nothing a tool flagged


def test_what_the_agent_writes_for_the_officer_has_no_tool_names_flags_or_ref_ids() -> None:
    from app.case_pipeline.cross_document.verify import plain_text

    text = plain_text(
        "compare_identifiers flagged an IDENTIFIER_CONFLICT. The address_consistency_tool confirmed it "
        "(ref:credit_report_1:enquiry_reference). The refutation check strengthens the claim."
    )

    assert text == "the check flagged the mismatch. The check confirmed it. The cross-check strengthens the claim."


def _harbor_light_context():
    """GLDB-16's real documents: its C014_CertIncorp spells the company "SHIPING"."""
    rows = json.loads((FIXTURES / "harbor-light.json").read_text())["rows"]
    return _context(*(DocumentRecord(**{**row, "status": DocumentStatus.DETECTED}) for row in rows))


def test_a_misspelt_company_name_is_flagged_against_the_cases_other_documents() -> None:
    # The spelling check leaves company names alone (coined brand words are normal in them), so a misspelt
    # one is caught by comparing it with the name on the case's other documents, as a company-name difference.
    result = run_deterministic_checks(_harbor_light_context())

    [mismatch] = [f for f in result.findings if "company name" in f.summary]
    assert "HARBOR LIGHT SHIPING PTE LTD" in mismatch.summary
    assert "HARBOR LIGHT SHIPPING PTE LTD" in mismatch.summary
    assert mismatch.category.value == "cross_document_variance"
    assert mismatch.severity == "medium"


async def test_a_company_name_mismatch_cannot_be_dropped_by_the_agent() -> None:
    # The code finding is added to the result whatever the agent proposes or concludes.
    context = _harbor_light_context()

    with patch.object(graph, "hypothesise", AsyncMock(return_value=([], 0.0))):
        state = await graph.run_cross_document_analysis(context)

    [mismatch] = [f for f in state["findings"] if "company name" in f.summary]
    assert mismatch.hypothesis_id == "deterministic"
    assert mismatch.severity == "medium"
