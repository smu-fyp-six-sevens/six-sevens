"""
Regression tests (SCRUM-246) for fraud detection — "Test Cases - Sprint
3.xlsx", Fraud Detection sheet, FD-001 – FD-026:

  SCRUM-65  Image manipulation: boundary, scaling & colour    FD-001, FD-002
  SCRUM-63  Text overlay                                      FD-003, FD-004
  SCRUM-62  Layout anomalies                                  FD-005, FD-006
  SCRUM-66  Document metadata                                 FD-007, FD-008
  SCRUM-68  Numerical validation                              FD-009, FD-010
  SCRUM-69  Date anomalies                                    FD-011, FD-012
  SCRUM-64  Image manipulation: resolution & compression      FD-013, FD-014
  SCRUM-119 Lead Detection Agent                              FD-015 – FD-020
  SCRUM-61  Font anomalies (type, size, emphasis)             FD-021 – FD-024
  SCRUM-70  Spelling anomalies                                FD-025, FD-026

The real detection graph runs end to end: load context -> lead plan -> the
three specialists in parallel (each a real tool-calling subgraph) -> lead
review -> second look (at most once) -> synthesize. The tools are the real
ones and run the real checks in fraud_detection/ on the document's bytes.
Only Claude's replies are scripted, by FakeClaude standing in for
build_chat_anthropic — no request ever leaves the test (conftest.py also
blocks real Claude calls). The database and storage are in-memory fakes.
The harness is tests/support/detection_harness.py, the generated documents
tests/support/detection_documents.py; other detection suites share both.

The scripted specialists behave like a well-behaved Claude: they call the
tools named in the script, then rate every finding those tools returned.
What a test controls is which tools run and how findings are rated; what the
tools find, and how the graph merges it into one report, is all real code.

Documents are generated in the test (pymupdf), each carrying the anomaly its
test case injects, because the Sprint 3 test files (C005, C028, C034...)
aren't in the repository. Whether Claude itself chooses the right tools and
ratings on the real documents is checked by the manual live run at the end
of each sprint.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.langgraph.detection.graph import run_detection
from app.langgraph.detection.specialists import SPECIALISTS
from tests.support import detection_harness
from tests.support.detection_documents import (
    CLEAN_METADATA, TAMPERED_METADATA, certificate_pdf, clauses_pdf, credit_report_with_font_and_metadata_tampering,
    id_card_pdf, image_only_pdf, payslip_pdf,
)
from tests.support.detection_harness import (
    CONTENT, DIGITAL, LAYOUT, DetectionAI, FakeStore, Review, Specialist, consulted, findings_from, indicators,
)

# The harness's fixtures: scripted Claude, the in-memory store, and the cleanup after each run.
ai = detection_harness.ai
store = detection_harness.store
_clear_detection_memory = detection_harness._clear_detection_memory

FIXTURES = Path(__file__).parent / "fixtures" / "regression"



# --- SCRUM-65 Image manipulation: boundary, scaling & colour ---------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-65", "FD-001")
async def test_an_image_painted_over_with_a_replacement_is_flagged(ai, store):
    document_id = store.add("IRAS_NOA_Company_20260617.pdf", "notice_of_assessment", id_card_pdf(replace_crest=True))
    ai.specialists[LAYOUT] = Specialist(tools=["image_tampering_tool"])

    report = await run_detection(document_id)

    assert report["verdict"] == "flagged"
    assert "Image Overlay" in indicators(report, "image_tampering_tool")
    assert all(f["specialists"] == [LAYOUT] for f in findings_from(report, "image_tampering_tool"))


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-65", "FD-002")
async def test_a_clean_image_is_not_flagged_for_tampering(ai, store):
    document_id = store.add("C020_NOA.pdf", "notice_of_assessment", id_card_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["image_tampering_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "image_tampering_tool") == []
    assert "image_tampering_tool" in consulted(report, LAYOUT)["tools_called"]


async def test_a_rerun_clears_the_error_an_earlier_attempt_left(ai, store):
    document_id = store.add("C020_NOA.pdf", "notice_of_assessment", id_card_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["image_tampering_tool"])
    store.errors[document_id] = "Cancelled by loan officer"

    await run_detection(document_id)

    assert document_id not in store.errors
    assert store.reports[document_id]["verdict"]


# --- SCRUM-63 Text overlay -----------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-63", "FD-003")
async def test_text_typed_over_other_text_is_flagged_as_a_text_overlay(ai, store):
    document_id = store.add("C005-P2_Payslip.pdf", "payslip", clauses_pdf(overlay="SGD 12,450.00 paid in full"))
    ai.specialists[LAYOUT] = Specialist(tools=["layout_anomalies_tool"])

    report = await run_detection(document_id)

    overlays = [f for f in findings_from(report, "layout_anomalies_tool") if f["type"] == "Text Overlay"]
    assert overlays
    assert "SGD 12,450.00" in overlays[0]["text"]
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-63", "FD-004")
async def test_a_clean_document_has_no_text_overlay(ai, store):
    document_id = store.add("C009-P2_Payslip.pdf", "payslip", clauses_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["layout_anomalies_tool"])

    report = await run_detection(document_id)

    assert [f for f in findings_from(report, "layout_anomalies_tool") if f["type"] == "Text Overlay"] == []


# --- SCRUM-62 Layout anomalies ---------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-62", "FD-005")
async def test_an_indented_clause_is_reported_as_a_layout_anomaly(ai, store):
    document_id = store.add("C018_Constitution.pdf", "company_constitution", clauses_pdf(indent_third=True))
    ai.specialists[LAYOUT] = Specialist(tools=["layout_anomalies_tool"])

    report = await run_detection(document_id)

    layout = findings_from(report, "layout_anomalies_tool")
    assert [f["type"] for f in layout] == ["Indentation"]
    assert report["verdict"] == "flagged"
    # "No unrelated fraud dimensions should be reported" (FD-005's expected result).
    assert {f["tool"] for f in report["findings"]} == {"layout_anomalies_tool"}


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-62", "FD-006")
async def test_a_clean_document_reports_no_layout_anomaly(ai, store):
    document_id = store.add("C035_NOA.pdf", "notice_of_assessment", clauses_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["layout_anomalies_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "layout_anomalies_tool") == []
    assert report["verdict"] == "clean"


# --- SCRUM-66 Document metadata --------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-66", "FD-007")
async def test_metadata_showing_an_editor_and_a_placeholder_author_is_flagged(ai, store):
    document_id = store.add("C028_CreditReport.pdf", "credit_report", payslip_pdf(), metadata=TAMPERED_METADATA)
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"], rate={"metadata_tool-1": "low"})

    report = await run_detection(document_id)

    metadata = {f["field"]: f for f in findings_from(report, "metadata_tool")}
    assert metadata["author"]["value"] == "FraudTests"
    assert "Adobe Acrobat" in metadata["creator"]["value"]
    assert all(f["specialists"] == [DIGITAL] for f in metadata.values())
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-66", "FD-008")
async def test_metadata_from_the_issuers_own_system_is_clean(ai, store):
    document_id = store.add("C027_CreditReport.pdf", "credit_report", payslip_pdf(), metadata=CLEAN_METADATA)
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "metadata_tool") == []
    assert "metadata_tool" in consulted(report, DIGITAL)["tools_called"]


# --- SCRUM-68 Numerical validation -----------------------------------------------------


def _transactions(tamper: bool) -> list[dict]:
    return [
        {"date": "01 Jun 2026", "description": "Balance B/F", "amount": 0, "balance": 46873.63},
        {"date": "05 Jun 2026", "description": "Inward credit", "amount": 18141.88, "balance": 65015.51},
        {"date": "12 Jun 2026", "description": "Supplier transfer", "amount": -5709.40,
         "balance": 69306.11 if tamper else 59306.11},
        {"date": "30 Jun 2026", "description": "Service charge", "amount": -35.00,
         "balance": 69271.11 if tamper else 59271.11},
    ]


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-68", "FD-009")
async def test_a_balance_that_does_not_reconcile_is_flagged(ai, store):
    document_id = store.add("C002_BankStatement_2026-06_to_2026-08.pdf", "bank_statement", payslip_pdf(),
                            fields={"transactions": _transactions(tamper=True)})
    ai.specialists[CONTENT] = Specialist(tools=["numerical_checks_tool"], severity="high")

    report = await run_detection(document_id)

    numerical = {f["check_code"]: f for f in findings_from(report, "numerical_checks_tool")}
    # The edited balance breaks the running balance, and so the opening/closing reconciliation too.
    assert set(numerical) == {"BS.RUNNING_BALANCE", "BS.OPEN_CLOSE"}
    assert numerical["BS.RUNNING_BALANCE"]["severity"] == "high"
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-68", "FD-010")
async def test_balances_that_reconcile_raise_no_numerical_finding(ai, store):
    document_id = store.add("C003_BankStatement_2026-06_to_2026-08.pdf", "bank_statement", payslip_pdf(),
                            fields={"transactions": _transactions(tamper=False)})
    ai.specialists[CONTENT] = Specialist(tools=["numerical_checks_tool"])

    report = await run_detection(document_id)

    assert [f for f in findings_from(report, "numerical_checks_tool") if f["check_code"] == "BS.RUNNING_BALANCE"] == []


# --- SCRUM-69 Date anomalies ------------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-69", "FD-011")
async def test_an_incorporation_date_in_the_future_is_flagged(ai, store):
    document_id = store.add("BizFile_112712.pdf", "certificate_of_incorporation", certificate_pdf("ACME PTE LTD"),
                            fields={"incorporation_date": "2027-04-18", "uen": "201634564N"})
    ai.specialists[CONTENT] = Specialist(tools=["date_consistency_tool"], severity="high")

    report = await run_detection(document_id)

    dates = findings_from(report, "date_consistency_tool")
    future = [f for f in dates if f["check_code"] == "GEN.DATE_VALID" and f["field"] == "incorporation_date"]
    assert len(future) == 1
    assert future[0]["stated"] == "2027-04-18"
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-69", "FD-011")
async def test_a_specialist_that_runs_no_tool_is_reported_as_incomplete_not_as_a_pass(ai, store):
    # FD-011's live run: the content specialist came back without running any
    # tool, so the future date was never checked. The report must say so —
    # "incomplete", never quietly "nothing found".
    document_id = store.add("BizFile_112712.pdf", "certificate_of_incorporation", certificate_pdf("ACME PTE LTD"),
                            fields={"incorporation_date": "2027-04-18", "uen": "201634564N"})
    ai.specialists[CONTENT] = Specialist(tools=[])

    report = await run_detection(document_id)

    content = consulted(report, CONTENT)
    assert content["tools_called"] == []
    assert content["uncertain"] is True


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-69", "FD-012")
async def test_consistent_dates_raise_no_date_finding(ai, store):
    document_id = store.add("C010_CertIncorp.pdf", "certificate_of_incorporation", certificate_pdf("ACME PTE LTD"),
                            fields={"incorporation_date": "2016-03-26", "certificate_date": "2016-03-26",
                                    "uen": "201634564N"})
    ai.specialists[CONTENT] = Specialist(tools=["date_consistency_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "date_consistency_tool") == []


# --- SCRUM-64 Image manipulation: resolution & compression ---------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-64", "FD-013")
async def test_a_crest_replaced_at_a_lower_resolution_and_quality_is_flagged(ai, store):
    document_id = store.add("C011-P1_NRICPassport.pdf", "passport", id_card_pdf(replace_crest=True))
    ai.specialists[LAYOUT] = Specialist(tools=["image_manipulation_tool"])

    report = await run_detection(document_id)

    assert indicators(report, "image_manipulation_tool") == {"Resolution Mismatch", "Compression Mismatch"}
    assert consulted(report, LAYOUT)["title"] == "Layout & Rendering Analyst"
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-64", "FD-014")
async def test_an_untouched_image_raises_no_resolution_or_compression_finding(ai, store):
    # Like C004 in the live run: one image, nothing to compare it with — a
    # pass with nothing to check, not a finding and not a failed check.
    document_id = store.add("C004-P1_NRICPassport.pdf", "passport", id_card_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["image_manipulation_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "image_manipulation_tool") == []
    [skipped] = [n for n in report["nothing_to_check"] if n["tool"] == "image_manipulation_tool"]
    assert skipped["reason"].startswith("Only 1 distinct image asset")
    assert report["unable_to_assess"] == []


# --- SCRUM-119 Lead Detection Agent ------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-015")
async def test_detection_produces_one_verdict_one_summary_and_one_findings_list(ai, store):
    document_id = credit_report_with_font_and_metadata_tampering(store)
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool", "layout_anomalies_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])
    ai.reviews = [Review("The font of one field and the file's metadata both point to editing.")]

    report = await run_detection(document_id)

    assert report["verdict"] == "flagged"
    assert report["summary"] == "The font of one field and the file's metadata both point to editing."
    tools = {f["tool"] for f in report["findings"]}
    assert {"font_consistency_tool", "metadata_tool"} <= tools
    for finding in report["findings"]:
        assert finding["specialists"], finding
        assert set(finding["specialists"]) <= set(SPECIALISTS)
    assert len(report["consulted"]) >= 2
    assert ai.calls.count("lead_plan") == 1
    assert store.statuses[document_id] == ["detecting", "detected"]


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-016")
async def test_the_report_lists_each_specialist_with_its_brief_and_the_tools_it_ran(ai, store):
    document_id = store.add("C030-P2_Payslip.pdf", "payslip", payslip_pdf())
    ai.briefs = {
        LAYOUT: "Native-text payslip: check fonts, spacing and images.",
        CONTENT: "Check the CPF arithmetic and dates.",
        DIGITAL: "Check the producing software and timestamps.",
    }
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool", "image_tampering_tool"])
    ai.specialists[CONTENT] = Specialist(tools=["date_consistency_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])

    report = await run_detection(document_id)

    # Every specialist reviews every document (the lead briefs, it doesn't
    # skip), so "each skipped specialist with its reason" is an empty list.
    assert {c["specialist"] for c in report["consulted"]} == set(SPECIALISTS)
    for name, brief in ai.briefs.items():
        assert consulted(report, name)["brief"] == brief
        assert consulted(report, name)["title"] == SPECIALISTS[name].title
    assert consulted(report, LAYOUT)["tools_called"] == ["hidden_text_tool", "font_consistency_tool", "image_tampering_tool"]
    assert consulted(report, CONTENT)["tools_called"] == ["date_consistency_tool"]
    # The injection check runs for digital_authenticity on every document, before its own choice of tools.
    assert consulted(report, DIGITAL)["tools_called"] == ["prompt_injection_tool", "metadata_tool"]
    # A check with nothing to look at is listed with its reason, as a pass.
    assert any(n["tool"] == "image_tampering_tool" for n in report["nothing_to_check"])


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-017")
@pytest.mark.parametrize("severity", ["low", "medium", "high"])
async def test_any_finding_rated_low_or_above_flags_the_document(ai, store, severity):
    document_id = store.add("C028_CreditReport.pdf", "credit_report", payslip_pdf(), metadata=TAMPERED_METADATA)
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"], severity="info",
                                         rate={"metadata_tool-1": severity})

    report = await run_detection(document_id)

    assert report["verdict"] == "flagged"
    assert report["findings"][0]["severity"] == severity


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-017")
async def test_findings_the_specialist_rates_info_or_dismisses_do_not_flag(ai, store):
    document_id = store.add("C028_CreditReport.pdf", "credit_report", payslip_pdf(), metadata=TAMPERED_METADATA)
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"], severity="info", dismiss={"metadata_tool-1"})

    report = await run_detection(document_id)

    assert report["verdict"] == "clean"
    assert {f["severity"] for f in report["findings"]} == {"info"}
    assert [d["id"] for d in consulted(report, DIGITAL)["dismissed"]] == ["metadata_tool-1"]


async def clean_report(ai: DetectionAI, store: FakeStore) -> dict:
    """FD-018: a clean certificate where every check runs."""
    document_id = store.add("C004-P1_NRICPassport.pdf", "certificate_of_incorporation",
                            certificate_pdf("ACME PTE LTD"), metadata=CLEAN_METADATA,
                            fields={"incorporation_date": "2016-03-26", "uen": "201634564N"})
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.specialists[CONTENT] = Specialist(tools=["date_consistency_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])
    return await run_detection(document_id)


async def inconclusive_report(ai: DetectionAI, store: FakeStore) -> dict:
    """FD-019: a page that is one picture, so the font check can't read any text."""
    document_id = store.add("clean_test.pdf", "certificate_of_incorporation", image_only_pdf(),
                            metadata=CLEAN_METADATA)
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=["metadata_tool"])
    ai.reviews = [Review("No findings. The metadata check passed, but the fonts could not be checked.")]
    return await run_detection(document_id)


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-018")
async def test_a_clean_document_where_every_check_runs_is_clean(ai, store):
    report = await clean_report(ai, store)

    assert report["findings"] == []
    assert report["unable_to_assess"] == []
    assert report["verdict"] == "clean"
    assert report["summary"] == "Nothing worth flagging was found."


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-019")
async def test_no_findings_but_a_check_that_could_not_run_is_inconclusive(ai, store):
    report = await inconclusive_report(ai, store)

    assert report["findings"] == []
    assert [u["tool"] for u in report["unable_to_assess"]] == ["font_consistency_tool"]
    assert report["verdict"] == "inconclusive"
    assert "could not be checked" in report["summary"]


def _second_look_request(finding_id: str, to: str) -> dict:
    return {"finding_id": finding_id, "to": to,
            "question": "Please examine the PDF metadata for the producing software.",
            "reason": "A restyled field alone could be a template quirk."}


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-020")
async def test_a_second_look_runs_once_on_a_different_specialist_and_shows_the_question(ai, store):
    document_id = credit_report_with_font_and_metadata_tampering(store)
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=[])  # first pass: nothing run
    ai.second_looks[DIGITAL] = Specialist(tools=["metadata_tool"])
    ai.reviews = [
        Review("A restyled field; asking for a second opinion.",
               _second_look_request("font_consistency_tool-1", DIGITAL)),
        # The lead asks again after the second look: refused, it's once per run.
        Review("Font and metadata both point to editing.", _second_look_request("font_consistency_tool-1", CONTENT)),
    ]

    report = await run_detection(document_id)

    assert report["second_look"] == {
        "from": LAYOUT,
        "to": DIGITAL,
        "question": "Please examine the PDF metadata for the producing software.",
        "reason": "A restyled field alone could be a template quirk.",
    }
    assert ai.calls.count(f"{DIGITAL}:second_look:tools") == 1
    assert not any(call.startswith(f"{CONTENT}:second_look") for call in ai.calls)
    assert consulted(report, DIGITAL)["second_look"] is True
    second_look_findings = findings_from(report, "metadata_tool")
    assert second_look_findings
    assert all(f["via_second_look"] for f in second_look_findings)
    assert ai.calls.count("lead_review") == 2


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-020")
async def test_a_second_look_back_to_the_specialist_that_raised_the_finding_is_refused(ai, store):
    document_id = credit_report_with_font_and_metadata_tampering(store)
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.reviews = [Review("Asking the same specialist again.", _second_look_request("font_consistency_tool-1", LAYOUT))]

    report = await run_detection(document_id)

    assert report["second_look"] is None
    assert not any("second_look" in call for call in ai.calls)


# --- SCRUM-61 Font anomalies -------------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-61", "FD-021")
async def test_a_label_in_another_typeface_is_flagged_and_kept_in_the_lead_report(ai, store):
    # FD-021's remark: the font finding was detected but missing from the
    # lead's report. The report's findings list is merged in code from the
    # tool results, so a finding the specialist didn't dismiss is always in it.
    document_id = store.add("C021_SourceWealth_BS.pdf", "source_of_wealth", payslip_pdf(font={2: "cour"}))
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])
    ai.reviews = [Review("Summary that does not mention the font.")]

    report = await run_detection(document_id)

    [finding] = findings_from(report, "font_consistency_tool")
    assert finding["deviation_type"] == "font"
    assert finding["locations"][0]["text"] == "Transport allowance"
    assert report["verdict"] == "flagged"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-61", "FD-022")
async def test_a_label_in_another_font_size_is_flagged(ai, store):
    document_id = store.add("C024-P1_ProofAddress.pdf", "proof_of_address", payslip_pdf(size={0: 12}))
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])

    report = await run_detection(document_id)

    [finding] = findings_from(report, "font_consistency_tool")
    assert finding["deviation_type"] == "size"
    assert finding["locations"][0]["text"] == "Basic salary"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-61", "FD-023")
async def test_an_underlined_label_is_flagged_as_an_emphasis_deviation(ai, store):
    document_id = store.add("C026_CertIncumbency.pdf", "certificate_of_incumbency", payslip_pdf(underline=3))
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])

    report = await run_detection(document_id)

    [finding] = findings_from(report, "font_consistency_tool")
    assert finding["deviation_type"] == "emphasis"
    assert finding["detected"]["underline"] is True
    assert finding["locations"][0]["text"] == "Employee CPF"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-61", "FD-024")
async def test_a_consistently_styled_document_has_no_font_finding(ai, store):
    document_id = store.add("C024_CertIncumbency.pdf", "certificate_of_incumbency", payslip_pdf())
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "font_consistency_tool") == []


# --- SCRUM-70 Spelling anomalies ---------------------------------------------------------


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-70", "FD-026")
async def test_a_correctly_spelt_certificate_has_no_spelling_finding(ai, store):
    document_id = store.add("C008_CertIncorp.pdf", "certificate_of_incorporation",
                            certificate_pdf("HARBOR LIGHT SHIPPING PTE LTD"),
                            fields={"company_name": "HARBOR LIGHT SHIPPING PTE LTD"})
    ai.specialists[CONTENT] = Specialist(tools=["spelling_tool"])

    report = await run_detection(document_id)

    assert findings_from(report, "spelling_tool") == []


# --- shared fixture for the Playwright suite ---------------------------------------
# frontend/e2e/specs/detection.spec.ts renders this report (FD-015 – FD-020),
# so the UI is tested against what the graph really produces. If the graph's
# output changes, this test fails until the fixture is regenerated:
#     REGENERATE_REGRESSION_FIXTURES=1 python -m pytest tests/test_regression_detection.py -k playwright

DETECTION_REPORT_FIXTURE = FIXTURES / "detection-report-c028.json"


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-015 – FD-020")
async def test_the_playwright_suites_detection_report_matches_the_graph(ai, store):
    document_id = credit_report_with_font_and_metadata_tampering(store)
    ai.briefs = {
        LAYOUT: "Credit report with printed figures: check fonts and layout.",
        CONTENT: "Check the dates and the credit report's internal consistency.",
        DIGITAL: "Check the producing software and authorship metadata.",
    }
    ai.specialists[LAYOUT] = Specialist(tools=["font_consistency_tool", "image_tampering_tool"])
    ai.specialists[CONTENT] = Specialist(tools=["spelling_tool"])
    ai.specialists[DIGITAL] = Specialist(tools=[])
    ai.second_looks[DIGITAL] = Specialist(tools=["metadata_tool"], rate={"metadata_tool-1": "low"})
    ai.reviews = [
        Review("A restyled field; asking for a second opinion.",
               _second_look_request("font_consistency_tool-1", DIGITAL)),
        Review("One field is set in a different typeface, and the file was last saved in Adobe Acrobat by "
               "\"FraudTests\". Together these suggest the report was edited after it was issued."),
    ]

    report = await run_detection(document_id)
    report = {**report, "generated_at": "2026-10-14T00:00:00+00:00"}

    if os.environ.get("REGENERATE_REGRESSION_FIXTURES"):
        DETECTION_REPORT_FIXTURE.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    assert json.loads(DETECTION_REPORT_FIXTURE.read_text()) == json.loads(json.dumps(report)), (
        "detection-report-c028.json is out of date with the detection graph — regenerate it (see above)"
    )


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-119", "FD-018 / FD-019")
@pytest.mark.parametrize("verdict, scenario", [("clean", clean_report), ("inconclusive", inconclusive_report)])
async def test_the_playwright_suites_clean_and_inconclusive_reports_match_the_graph(ai, store, verdict, scenario):
    """detection-report-clean.json and detection-report-inconclusive.json, which the browser suite renders."""
    fixture = FIXTURES / f"detection-report-{verdict}.json"
    report = {**await scenario(ai, store), "generated_at": "2026-10-14T00:00:00+00:00"}

    if os.environ.get("REGENERATE_REGRESSION_FIXTURES"):
        fixture.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    assert json.loads(fixture.read_text()) == json.loads(json.dumps(report)), (
        f"{fixture.name} is out of date with the detection graph — regenerate it (see above)"
    )
