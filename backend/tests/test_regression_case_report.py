"""
Regression tests (SCRUM-323) for the case report — "Test Cases - Sprint 5.xlsx", Case Management sheet, Export Case
Report (SCRUM-82): CM-075 – CM-085.

Each test downloads the report through the real GET /cases/{id}/report.pdf, which runs the real gather() and
render() (case_report.py), and reads the PDF it returns. The case is the real GLDB-02 Anchorpoint case captured from
Supabase, with its documents and their extracted fields; its summary (checklist, stage) is built by the real
case_service.build_summary from those documents. Only the database reads are in memory. A finished case analysis
carries the risk the real scoring computes (test_regression_case_views.py's scored case).
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from app import case_report, case_service
from app.case_pipeline.models import CaseAnalysis
from app.fraud_detection.font_consistency import ExtractedField
from app.main import app
from app.models import CaseNote, CaseRecord, CaseRisk, DocumentRecord, DocumentStatus, RoutingStatus

client = TestClient(app)
FIXTURES = Path(__file__).parent / "fixtures" / "regression"
ANCHORPOINT = json.loads((FIXTURES / "anchorpoint.json").read_text())
SCORED_RISK = CaseRisk(**json.loads((FIXTURES / "case-views.json").read_text())["scored"]["risk"])
NOW = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)


class ReportCase:
    """One case as the report reads it: the case row, its documents and their fields, its analysis and notes."""

    def __init__(self, *, company_name: str | None = None, documents: list[DocumentRecord] | None = None) -> None:
        self.case = CaseRecord(**ANCHORPOINT["case"])
        if company_name is not None:
            self.case = self.case.model_copy(update={"company_name": company_name})
        rows = [DocumentRecord(**row) for row in ANCHORPOINT["rows"]]
        # Officer-confirmed, as on the live case (see test_regression_checklist._officer_confirmed).
        self.documents = documents if documents is not None else [
            d.model_copy(update={"classification_confidence": max(d.classification_confidence or 0, 0.9)}) for d in rows
        ]
        self.fields = {
            d["id"]: [ExtractedField(field_name=name, field_type="string", field_value=f["value"],
                                     confidence=f["confidence"] or 0) for name, f in (d["extracted_fields"] or {}).items()]
            for d in ANCHORPOINT["documents"].values()
        }
        self.analysis: CaseAnalysis | None = None
        self.notes: list[CaseNote] = []

    def summary(self, case_id: str):
        documents = [d.model_copy(update={"extracted_fields": {f.field_name: f.field_value for f in self.fields.get(d.id, [])}})
                     for d in self.documents]
        return case_service.build_summary(self.case, documents)

    def list_documents(self, case_id=None, include_fields=True, **kwargs):
        return [d.model_copy() for d in self.documents]

    def fields_for(self, document_ids):
        return {i: self.fields.get(i, []) for i in document_ids}

    def finish_analysis(self) -> None:
        self.analysis = CaseAnalysis(run_id="run-1", case_id=self.case.id, status="complete", fingerprint="f",
                                     started_at=NOW, finished_at=NOW, risk=SCORED_RISK)


def wire(monkeypatch, case: ReportCase) -> ReportCase:
    """Puts the case in place of the database reads gather() makes."""
    monkeypatch.setattr(case_report.case_service, "get_case_summary", lambda case_id: case.summary(case_id))
    monkeypatch.setattr(case_report.document_repository, "list_documents", case.list_documents)
    monkeypatch.setattr(case_report.extracted_fields_repository, "get_extracted_fields_for_documents", case.fields_for)
    monkeypatch.setattr(case_report.analysis_service, "get_current", lambda case_id: case.analysis)
    monkeypatch.setattr(case_report.analysis_repository, "list_runs", lambda case_id: [])
    monkeypatch.setattr(case_report.notes_repository, "list_notes", lambda case_id: case.notes)
    monkeypatch.setattr(case_report.audit, "list_events", lambda **kwargs: [])
    return case


@pytest.fixture
def report_case(monkeypatch) -> ReportCase:
    return wire(monkeypatch, ReportCase())


def export(case: ReportCase) -> str:
    """Downloads the report and returns its text, after checking it is a PDF that opens."""
    response = client.get(f"/cases/{case.case.id}/report.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    reader = PdfReader(io.BytesIO(response.content))
    assert len(reader.pages) >= 1
    return "\n".join(page.extract_text() for page in reader.pages)


def flat(text: str) -> str:
    """The text with its line breaks as spaces, so a wrapped name or sentence reads as one."""
    return " ".join(text.split())


def without_export_time(text: str) -> str:
    return re.sub(r"Generated \d+ \w+ \d{4}, \d+:\d{2} SGT", "Generated <time>", text)


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-075")
def test_a_pdf_report_is_exported_for_the_case(report_case):
    text = flat(export(report_case))

    # The name the browser saves it under (the Playwright mock serves the same).
    disposition = client.get(f"/cases/{report_case.case.id}/report.pdf").headers["content-disposition"]
    assert disposition == 'attachment; filename="GLDB-02 Case Report.pdf"'
    assert "GLDB-02" in text
    assert "ANCHORPOINT INSURANCE BROKERS" in text.upper()
    assert "Anchorpoint_IRAS_NOA.pdf" in text


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-076 / CM-081")
def test_before_an_analysis_the_report_shows_only_what_is_available(report_case):
    text = flat(export(report_case))

    assert text.count("No case analysis has been run yet.") == 1
    for section in ("Risk Assessment", "Cross-Document Analysis", "Additional Documents to Request"):
        assert section not in text  # nothing that only an analysis produces is shown as done
    assert "Document Checklist" in text


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-077 / CM-079")
def test_a_report_exported_after_the_case_progresses_shows_the_latest_stage(report_case):
    before = flat(export(report_case))
    report_case.finish_analysis()

    after = flat(export(report_case))

    assert "No case analysis has been run yet." in before and "Risk Assessment" not in before
    assert "No case analysis has been run yet." not in after
    assert "Risk Assessment" in after
    assert f"{SCORED_RISK.level} Risk" in after


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-078")
def test_repeated_exports_of_an_unchanged_case_are_identical_apart_from_the_export_time(report_case):
    first, second = export(report_case), export(report_case)

    assert without_export_time(first) == without_export_time(second)


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-080")
def test_a_case_with_no_documents_exports_without_document_details(monkeypatch):
    case = wire(monkeypatch, ReportCase(company_name="Brightwave Analytics Pte Ltd", documents=[]))

    text = flat(export(case))

    assert "No documents have been uploaded to this case" in text
    assert "Anchorpoint_IRAS_NOA.pdf" not in text


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-081")
def test_missing_documents_are_not_shown_as_received(report_case):
    complete = flat(export(report_case))
    report_case.documents = [d for d in report_case.documents if "IRAS_NOA" not in d.filename]

    text = flat(export(report_case))

    assert "REQUIRED DOCUMENTS · 11 OF 11 PRESENT" in complete
    assert "REQUIRED DOCUMENTS · 10 OF 11 PRESENT" in text
    assert "Anchorpoint_IRAS_NOA.pdf" not in text


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-082")
def test_the_report_shows_the_latest_saved_case_details_and_notes(report_case):
    report_case.case = report_case.case.model_copy(update={"company_name": "Anchorpoint Insurance Brokers (Asia) Pte Ltd"})
    report_case.notes = [CaseNote(id="n1", case_id=report_case.case.id, author="demo", body="Called the broker: confirmed.",
                                  created_at=NOW)]

    text = flat(export(report_case))

    assert "Anchorpoint Insurance Brokers (Asia) Pte Ltd" in text
    assert "Called the broker: confirmed." in text


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-083")
def test_a_newly_uploaded_document_is_in_the_report(report_case):
    report_case.documents.append(DocumentRecord(
        id="new-doc", case_id=report_case.case.id, filename="CM-083-test.pdf", status=DocumentStatus.QUEUED,
        uploaded_at=NOW, routing_status=RoutingStatus.ACCEPTED,
    ))

    assert "CM-083-test.pdf" in flat(export(report_case))


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-084")
def test_a_long_company_name_is_shown_in_full(report_case):
    name = "Brightwave Analytics and Data Consultancy Services Holdings Pte Ltd"
    report_case.case = report_case.case.model_copy(update={"company_name": name})

    assert name in flat(export(report_case))


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-82", "CM-085")
def test_special_characters_are_shown_as_typed(report_case):
    name = "O'Brien & Lée (Asia) Pte. Ltd."
    report_case.case = report_case.case.model_copy(update={"company_name": name})
    report_case.notes = [CaseNote(id="n1", case_id=report_case.case.id, author="demo",
                                  body="Checked <the> \"statement\" & payslip — 100% matched.", created_at=NOW)]

    text = flat(export(report_case))

    assert name in text
    assert "Checked <the> \"statement\" & payslip — 100% matched." in text
