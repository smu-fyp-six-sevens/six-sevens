from __future__ import annotations

import io
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import case_report
from app.case_pipeline.models import AnalysisSummary, CaseAnalysis, CrossDocFinding, FindingCategory, SummaryReason
from app.case_report import ReportData
from app.events import types
from app.main import app
from app.models import (
    AdditionalDocument,
    AdditionalDocuments,
    CaseDocumentsStatus,
    CaseNote,
    CaseRisk,
    CaseSummary,
    ContributingFinding,
    DocumentRecord,
    DocumentStatus,
    UnassessedCheck,
)

client = TestClient(app)
NOW = datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)


def _summary(**overrides) -> CaseSummary:
    overrides.setdefault("fulfilled_requirements", ["id_document", "bank_statement", "certificate_of_incorporation"])
    return CaseSummary(
        id="case-1", greenlink_case_id="GLDB-137", company_name="Acme Holdings Pte Ltd",
        created_at=NOW, updated_at=NOW, documents_status=CaseDocumentsStatus.NO_DOCUMENTS, **overrides,
    )


def _document(doc_id: str, filename: str, document_type: str, status: DocumentStatus) -> DocumentRecord:
    return DocumentRecord(
        id=doc_id, case_id="case-1", filename=filename, document_type=document_type, status=status, uploaded_at=NOW,
    )


DOCUMENTS = [
    _document("d1", "uob_statement_aug.pdf", "bank_statement", DocumentStatus.DETECTED),
    _document("d2", "nric_tan_wei_ming.pdf", "nric", DocumentStatus.DETECTED),
    _document("d3", "coi_acme.pdf", "certificate_of_incorporation", DocumentStatus.ANALYZED),
]


def _analysis(status: str = "complete", stale: bool = False) -> CaseAnalysis:
    return CaseAnalysis(
        run_id="run-1", case_id="case-1", status=status, fingerprint="f", started_at=NOW,
        finished_at=NOW if status != "running" else None, stale=stale,
        error="Model timed out" if status == "failed" else None,
        findings=[
            CrossDocFinding(
                id="x1", hypothesis_id="h1", summary="Company name differs between the COI and the bank statement",
                rationale="The COI names Acme Holdings Pte Ltd; the statement names Acme Holding Pte Ltd.",
                severity="medium", category=FindingCategory.CROSS_DOCUMENT_VARIANCE,
            ),
        ],
        structured_summary=AnalysisSummary(
            headline="High risk: the bank statement shows signs of editing",
            reasons=[SummaryReason(text="Fonts change mid-page on the bank statement", finding_id="f1", document_id="d1")],
            would_help="A bank statement downloaded directly from the bank's portal.",
        ),
        risk=CaseRisk(
            state="scored", level="High", score=62, points=11, documents_scanned=2, documents_total=2,
            reasons=["Two high-severity findings on the bank statement"],
            contributing_findings=[
                ContributingFinding(
                    document_id="d1", filename="uob_statement_aug.pdf", document_type="bank_statement",
                    finding_id="f1", tool="font_consistency_tool", severity="high",
                    summary="Fonts change mid-page", rationale="Line 14 uses a different font from the rest.",
                    points=8,
                ),
                ContributingFinding(
                    document_id="", filename="", finding_id="x1", severity="medium", source="cross_document",
                    category="cross_document_variance", summary="Company name differs", rationale="", points=3,
                ),
            ],
            unassessed_checks=[UnassessedCheck(document_id="d2", filename="nric_tan_wei_ming.pdf", tool="ai_generation_tool")],
        ),
        additional_documents=AdditionalDocuments(
            items=[AdditionalDocument(
                title="Bank statement from the bank's portal", why="Rules out editing of the uploaded statement.",
                source="finding", affects_score=True, score_if_supports=30, level_if_supports="Medium",
            )],
            current_score=62, current_level="High", best_case_score=30, best_case_level="Medium",
        ),
        clean_checks=["spelling_anomaly_tool"],
        consistent_checks=["UEN is the same on 3 documents"],
    )


NOTES = [
    CaseNote(id="n1", case_id="case-1", author="demo", body="Called the applicant <re> statement.", created_at=NOW),
    CaseNote(id="n2", case_id="case-1", parent_id="n1", author="bob", body="Agreed.", created_at=NOW),
]


def _text_of(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


def _render(**overrides) -> str:
    data = ReportData(summary=_summary(), documents=DOCUMENTS, latest=None, completed=None, notes=NOTES,
                      generated_by="demo", generated_at=NOW)
    for key, value in overrides.items():
        setattr(data, key, value)
    pdf = case_report.render(data)
    assert pdf.startswith(b"%PDF")
    return _text_of(pdf)


def test_report_without_an_analysis_says_so_once_and_leaves_out_the_analysis_sections() -> None:
    text = _render()

    assert "Acme Holdings Pte Ltd" in text
    assert "GLDB-137" in text
    assert "Green Link Digital Bank" in text
    assert "OPEN" in text  # the case's stage
    assert text.count("The case analysis has not been run yet") == 1
    assert "recommended additional documents appear once it has run" in text.replace("\n", " ")
    assert "trigger fraud detection" in text.replace("\n", " ")
    assert "Confidential · Generated 30 Oct 2026, 14:00 SGT by K. Lim" in text
    for section in ("Summary", "Risk Assessment", "Cross-Document Analysis", "Additional Documents to Request"):
        assert section not in text
    # Sections that don't need an analysis still show, numbered from 1.
    assert "1 Document Checklist" in text or "1\nDocument Checklist" in text
    assert "uob_statement_aug.pdf" in text
    assert "Corporate Bank Statement" in text
    assert "Called the applicant <re> statement." in text


def test_not_run_notice_says_when_the_analysis_is_ready_to_run() -> None:
    text = _render(summary=_summary(analysis_ready=True))

    assert "ready to run from the Case Analysis section" in text


def test_complete_report_shows_the_analysis_results() -> None:
    analysis = _analysis()
    text = _render(latest=analysis, completed=analysis)

    assert "not been run" not in text
    assert "Case analysis complete and up to date" in text
    assert "Risk Assessment" in text
    assert "High risk: the bank statement shows signs of editing" in text
    assert "Fonts change mid-page" in text
    assert "Font consistency" in text
    assert "Company name differs between the COI and the bank statement" in text
    assert "Bank statement from the bank's portal" in text
    # The "checked and fine" list is left out of the report.
    assert "UEN is the same on 3 documents" not in text
    assert "raised nothing" not in text
    assert "High Risk" in text
    assert "1 in single documents, 1" in text.replace("\n", " ")
    assert "could not run" in text


def test_running_analysis_without_a_previous_result() -> None:
    text = _render(latest=_analysis(status="running"))

    assert text.count("The case analysis is still running") == 1
    assert "Download the report again once it finishes" in text
    assert "Risk Assessment" not in text


def test_failed_analysis_shows_the_last_finished_result_with_a_warning() -> None:
    text = _render(latest=_analysis(status="failed"), completed=_analysis())

    assert "The case analysis did not finish" in text
    assert "Model timed out" in text
    assert "The results below come from the analysis finished" in text
    assert "Fonts change mid-page" in text


def test_failed_analysis_without_a_previous_result() -> None:
    text = _render(latest=_analysis(status="failed"))

    assert text.count("The case analysis did not finish") == 1
    assert "Risk Assessment" not in text


def test_stale_analysis_is_flagged_out_of_date() -> None:
    analysis = _analysis(stale=True)
    text = _render(latest=analysis, completed=analysis)

    assert "Case analysis is out of date" in text


def test_empty_case() -> None:
    text = _render(documents=[], notes=[], summary=_summary(fulfilled_requirements=[]))

    assert "No documents have been uploaded to this case" in text
    assert "Officer Notes" not in text


def test_notes_section_only_when_there_are_notes() -> None:
    assert "Officer Notes" in _render()
    assert "Not required" in _render(documents=[])
    assert "Officer Notes" not in _render(notes=[])


def test_report_is_titled_with_the_case_number() -> None:
    data = ReportData(summary=_summary(), documents=[], latest=None, completed=None, notes=[])
    assert PdfReader(io.BytesIO(case_report.render(data))).metadata.title == "GLDB-137 Case Report"


def test_notes_that_could_not_be_loaded() -> None:
    assert "The case's notes could not be loaded" in _render(notes=None)


def test_download_endpoint_returns_a_pdf_and_records_the_export(published_events) -> None:
    data = ReportData(summary=_summary(), documents=DOCUMENTS, latest=None, completed=None, notes=[])
    with patch("app.routers.cases.case_report.gather", return_value=data) as gather:
        response = client.get("/cases/case-1/report.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == 'attachment; filename="GLDB-137 Case Report.pdf"'
    assert response.content.startswith(b"%PDF")
    gather.assert_called_once_with("case-1", "test-user")
    exported = [e for e in published_events if e.type == types.CASE_REPORT_EXPORTED]
    assert len(exported) == 1
    assert exported[0].detail["analysis_state"] == "not_run"


def test_download_endpoint_404s_for_an_unknown_case() -> None:
    with patch("app.routers.cases.case_report.gather", return_value=None):
        response = client.get("/cases/missing/report.pdf")

    assert response.status_code == 404



def _routed(status: DocumentStatus, routing: str | None, confidence: float | None = 0.95, **extra) -> DocumentRecord:
    return DocumentRecord(
        id="r", case_id="case-1", filename="f.pdf", document_type="payslip", status=status, uploaded_at=NOW,
        routing_status=routing, classification_confidence=confidence,
        extracted_fields=extra.pop("fields", {"net_pay": "1000"}), **extra,
    )


def test_routing_outcome_matches_the_case_page() -> None:
    assert case_report.routing_outcome(_routed(DocumentStatus.ANALYZED, "accepted")) == "Accepted"
    assert case_report.routing_outcome(_routed(DocumentStatus.ANALYZED, "manual_review")) == "Manual Review"
    # An unconfirmed type, nothing extracted, a low-confidence field or a case mismatch each need the officer.
    assert case_report.routing_outcome(_routed(DocumentStatus.ANALYZED, "accepted", confidence=0.5)) == "Manual Review"
    assert case_report.routing_outcome(_routed(DocumentStatus.ANALYZED, "accepted", fields={})) == "Manual Review"
    low = _routed(DocumentStatus.ANALYZED, "accepted", fields={"net_pay": {"value": "1", "confidence": 0.4}})
    assert case_report.routing_outcome(low) == "Manual Review"
    assert case_report.routing_outcome(_routed(DocumentStatus.ANALYZED, "accepted"), "manual_review") == "Manual Review"
    assert case_report.routing_outcome(_routed(DocumentStatus.DETECTED, "manual_review")) == "Approved"
    assert case_report.routing_outcome(_routed(DocumentStatus.REJECTED, "accepted")) == "Rejected"


def test_cards_count_documents_needing_manual_review() -> None:
    documents = [_routed(DocumentStatus.ANALYZED, "accepted"), _routed(DocumentStatus.ANALYZED, "manual_review")]
    data = ReportData(summary=_summary(), documents=documents, latest=None, completed=None, notes=[])
    assert case_report.manual_review_count(data) == 1

    text = _text_of(case_report.render(data))
    assert "NEED MANUAL REVIEW" in text
    assert "Waiting for an officer" in text
    assert "EXTRACTED" in text and "Accepted" in text and "Manual Review" in text


def test_gather_routes_a_low_confidence_document_to_manual_review() -> None:
    from app.fraud_detection.font_consistency import ExtractedField

    payslip = _routed(DocumentStatus.ANALYZED, "accepted", fields=None)
    payslip.id = "slip"
    fields = {"slip": [
        ExtractedField(id="f1", field_name="net_pay", field_type="number", field_value="1000", confidence=0.6),
    ]}
    with (
        patch.object(case_report.case_service, "get_case_summary", return_value=_summary()),
        patch.object(case_report.document_repository, "list_documents", return_value=[payslip]),
        patch.object(case_report.extracted_fields_repository, "get_extracted_fields_for_documents", return_value=fields),
        patch.object(case_report.analysis_service, "get_current", return_value=None),
        patch.object(case_report.notes_repository, "list_notes", return_value=[]),
        patch.object(case_report, "check_case_match", side_effect=RuntimeError),
    ):
        data = case_report.gather("case-1")

    assert data.routing == {"slip": "Manual Review"}
    assert case_report.manual_review_count(data) == 1
    # The record the checklist reads keeps plain values.
    assert data.documents[0].extracted_fields == {"net_pay": "1000"}


def test_long_file_names_wrap_at_underscores() -> None:
    wrapped = case_report._wrap_filename("BrightwaveAnalytics_Incorporation_Cert_F.pdf", 40 * case_report.mm)
    assert wrapped.split("<br/>") == ["BrightwaveAnalytics_", "Incorporation_Cert_F.pdf"]
    assert case_report._wrap_filename("short.pdf", 40 * case_report.mm) == "short.pdf"


def test_an_older_runs_prose_summary_is_split_into_headline_points_and_what_would_help() -> None:
    analysis = _analysis()
    analysis.structured_summary = None
    analysis.summary = (
        "This application presents critical fraud risk. The balance sheet doesn't add up. "
        "The payslip's net pay exceeds gross pay. To meaningfully change this assessment, you would need "
        "a corrected audited balance sheet."
    )
    text = _render(latest=analysis, completed=analysis).replace("\n", " ")

    assert "KEY POINTS" in text
    assert "What would help" in text
    assert "you would need a corrected audited balance sheet" in text
    assert "WHY THIS LEVEL" not in text


def test_an_out_of_date_analysis_names_the_documents_changed_since() -> None:
    analysis = _analysis(stale=True)
    changed = [("BrightwaveAnalytics_Incorporation_Cert.pdf", ["uploaded", "approved", "scanned"])]
    text = _render(latest=analysis, completed=analysis, changed_documents=changed).replace("\n", " ")

    assert "Changed since then" in text
    assert "BrightwaveAnalytics_Incorporation_Cert.pdf: uploaded, approved, scanned" in text


def test_changed_since_reads_document_events_after_the_run_started() -> None:
    from app.events.envelope import Event

    analysis = _analysis(stale=True)
    later = datetime(2026, 10, 31, tzinfo=timezone.utc)
    events = [  # newest first, as the audit trail returns them
        Event(type=types.DOCUMENT_APPROVED, case_id="case-1", document_id="d1", occurred_at=later),
        Event(type=types.NOTE_ADDED, case_id="case-1", document_id="d1", occurred_at=later),
        Event(type=types.DOCUMENT_UPLOADED, case_id="case-1", document_id="d1", occurred_at=later),
        Event(type=types.DOCUMENT_APPROVED, case_id="case-1", document_id="d2", occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ]
    with patch.object(case_report.audit, "list_events", return_value=events):
        changed = case_report._changed_since("case-1", analysis, DOCUMENTS)

    assert changed == [("uob_statement_aug.pdf", ["uploaded", "approved"])]


def test_the_title_shows_the_cases_stage() -> None:
    assert "PENDING REVIEW" in _render(summary=_summary(status="pending_review"))
    assert "FINALISED" in _render(summary=_summary(status="finalised"))
