"""
Keeps the regression suite's shared fixture data in step with the backend
(SCRUM-206).

The Playwright suite's mocked backend (frontend/e2e/support/mockBackend.ts)
can't import Python, so it reads each document type's required extraction
fields from tests/fixtures/regression/required_fields.json to enforce the
same "can't approve while a required field is missing" rule as
routers/documents.py. If EXTRACTION_SCHEMAS changes, this test fails until the JSON is regenerated with
scripts/regenerate_regression_fixtures.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app import case_service, document_service
from app.fraud_detection.font_consistency import ExtractedField
from app.langgraph.extraction_prompts import EXTRACTION_SCHEMAS
from app.models import DocumentRecord, DocumentStatus
from tests.support.regression_fixtures import check_fixture

REQUIRED_FIELDS = Path(__file__).parent / "fixtures" / "regression" / "required_fields.json"
APPROVAL_RULES = Path(__file__).parent / "fixtures" / "regression" / "approval_rules.json"


def test_mocked_backend_required_fields_match_extraction_schemas():
    expected = {
        t: {
            "required": s.get("required", []),
            "allow_empty": s.get("allow_empty", []),
            "required_when_individual": s.get("required_when_individual", []),
        }
        for t, s in sorted(EXTRACTION_SCHEMAS.items())
    }
    check_fixture(REQUIRED_FIELDS, expected, sort_keys=False)


def _field(value, confidence=0.95) -> dict:
    return {"value": value, "confidence": confidence}


_PAYSLIP = {"individual_name": _field("Jane Tan"), "company_name": _field("Acme Pte Ltd"), "net_pay": _field("1000")}
_REVIEWED = "2026-11-01T03:00:00+00:00"
# Each case: the document as the page and the mock see it, and its fields. The expected answers come from the backend.
APPROVAL_CASES: list[tuple[str, dict, dict]] = [
    ("a confident, complete payslip", {"document_type": "payslip", "classification_confidence": 0.97}, _PAYSLIP),
    ("a document still extracting", {"status": "extracting", "document_type": "payslip", "classification_confidence": 0.97}, _PAYSLIP),
    ("an approved document", {"status": "approved", "document_type": "payslip", "classification_confidence": 0.97}, _PAYSLIP),
    ("a type the classifier is unsure of", {"document_type": "payslip", "classification_confidence": 0.5}, _PAYSLIP),
    ("a type exactly at the classifier threshold", {"document_type": "payslip", "classification_confidence": 0.85}, _PAYSLIP),
    ("no type and no confidence", {"document_type": None, "classification_confidence": None}, _PAYSLIP),
    ("nothing extracted", {"document_type": "payslip", "classification_confidence": 0.97},
     {name: _field("") for name in _PAYSLIP}),
    ("a required field missing", {"document_type": "payslip", "classification_confidence": 0.97},
     {k: v for k, v in _PAYSLIP.items() if k != "net_pay"}),
    ("a required field empty", {"document_type": "payslip", "classification_confidence": 0.97},
     {**_PAYSLIP, "net_pay": _field("  ")}),
    ("a field under the extraction threshold", {"document_type": "payslip", "classification_confidence": 0.97},
     {**_PAYSLIP, "company_name": _field("Acme Pte Ltd", 0.55)}),
    ("a field exactly at the extraction threshold", {"document_type": "payslip", "classification_confidence": 0.97},
     {**_PAYSLIP, "company_name": _field("Acme Pte Ltd", 0.9)}),
    ("two low-confidence fields", {"document_type": "payslip", "classification_confidence": 0.97},
     {**_PAYSLIP, "net_pay": _field("1000", 0.3), "company_name": _field("Acme Pte Ltd", 0.4)}),
    ("Other, picked by the officer, fields not saved",
     {"document_type": "unrecognised", "classification_confidence": 1.0}, {"note": _field("hello")}),
    ("Other, picked by the officer, fields saved",
     {"document_type": "unrecognised", "classification_confidence": 1.0, "fields_reviewed_at": _REVIEWED},
     {"note": _field("hello")}),
    ("Other, as the classifier left it",
     {"document_type": "unrecognised", "classification_confidence": 0.97}, {"note": _field("hello")}),
    ("a personal source of wealth (a name, no UEN)",
     {"document_type": "source_of_wealth", "classification_confidence": 0.97}, {"account_holder_name": _field("Jane Tan")}),
    ("a company source of wealth without its UEN",
     {"document_type": "source_of_wealth", "classification_confidence": 0.97}, {"company_name": _field("Acme Pte Ltd")}),
    ("a credit report with empty lists", {"document_type": "credit_report", "classification_confidence": 0.97},
     {"individual_name": _field("Jane Tan"), "id_number": _field("S1234567D"), "date_of_birth": _field("1990-01-01"),
      "nationality": _field("SG"), "individual_address": _field("1 Road"), "defaults": _field([]),
      "bankruptcy_proceedings": _field([])}),
    ("a payslip with an empty list where a value is required",
     {"document_type": "payslip", "classification_confidence": 0.97}, {**_PAYSLIP, "net_pay": _field([])}),
]


def _approval_entry(name: str, document: dict, fields: dict) -> dict:
    record = DocumentRecord(
        id="doc-1", case_id="case-1", filename="a.pdf", uploaded_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        status=DocumentStatus(document.get("status", "analyzed")), document_type=document["document_type"],
        classification_confidence=document["classification_confidence"],
        fields_reviewed_at=datetime.fromisoformat(document["fields_reviewed_at"]) if document.get("fields_reviewed_at") else None,
    )
    rows = [
        ExtractedField(field_name=key, field_type="string", field_value=item["value"], confidence=item["confidence"])
        for key, item in fields.items()
    ]
    return {
        "name": name,
        "document": {
            "status": record.status.value, "document_type": record.document_type,
            "classification_confidence": record.classification_confidence,
            "fields_reviewed_at": document.get("fields_reviewed_at"),
        },
        "fields": fields,
        # What GET /documents/{id} sends the page, and the refusal approve gives: both from the backend's own rules.
        "missing_required_fields": case_service.missing_required_fields(record.document_type, rows),
        "refusal": document_service.approval_refusal(record, rows),
    }


def test_mocked_backend_approval_rules_match_the_backend():
    """
    The approval rules, case by case: what the page (canApproveDocument) and the Playwright mock (approvalRefusal) must
    answer. approvalRules.test.ts runs both against this file, so the copies can't drift apart unnoticed.
    """
    check_fixture(APPROVAL_RULES, [_approval_entry(*case) for case in APPROVAL_CASES], sort_keys=False)
