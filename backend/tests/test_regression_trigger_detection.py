"""
Regression tests (SCRUM-246) for approving/rejecting a document and what
that does to fraud detection — "Test Cases - Sprint 3.xlsx", Multi-Agent
sheet, Trigger Detection (SCRUM-26): MA-005 – MA-012.

Runs the real approve/reject endpoints and the real approval event with
only the database faked, using real extraction results captured from
Supabase (tests/fixtures/regression/anchorpoint.json). Detection itself is
replaced by a recorder here — the detection graph is covered by
test_regression_detection.py.
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.fraud_detection.font_consistency import ExtractedField
from app.main import app
from app.models import DocumentRecord, DocumentStatus, RoutingStatus
from app.events import bus
from app.routers import documents as documents_router
from app.workers import detection as detection_worker

client = TestClient(app)
ANCHORPOINT = json.loads((Path(__file__).parent / "fixtures" / "regression" / "anchorpoint.json").read_text())


def _real(
    filename_part: str, status: DocumentStatus = DocumentStatus.ANALYZED
) -> tuple[DocumentRecord, list[ExtractedField]]:
    captured = next(d for d in ANCHORPOINT["documents"].values() if filename_part in d["filename"])
    document = DocumentRecord(
        id=captured["id"],
        case_id=captured["case_id"],
        filename=captured["filename"],
        document_type=captured["document_type"],
        classification_confidence=captured["classification_confidence"],
        routing_status=RoutingStatus.ACCEPTED,
        status=status,
        uploaded_at=datetime.now(timezone.utc),
    )
    fields = [
        ExtractedField(field_name=name, field_type="string", field_value=f["value"], confidence=f["confidence"] or 0)
        for name, f in captured["extracted_fields"].items()
    ]
    # Captured before SCRUM-26 made these required on a certificate of
    # incorporation; a fresh extraction returns them, so supply them as it would.
    if document.document_type == "certificate_of_incorporation":
        fields += [
            ExtractedField(field_name="incorporating_act", field_type="string",
                           field_value="Companies Act 1967", confidence=0.98),
            ExtractedField(field_name="certificate_date", field_type="string",
                           field_value="26 Mar 2016", confidence=0.98),
        ]
    return document, fields


class Repo:
    """Stands in for document_repository: approve/reject change the one document's status."""

    def __init__(self, document: DocumentRecord) -> None:
        self.document = document

    def get_document(self, document_id, include_fields=True, include_metadata=False):
        return self.document

    def approve_document(self, document_id):
        self.document.status = DocumentStatus.APPROVED
        return self.document

    def reject_document(self, document_id):
        self.document.status = DocumentStatus.REJECTED
        return self.document


def _call(document: DocumentRecord, fields: list[ExtractedField], method: str, url: str, body: dict | None = None):
    """Calls the real endpoint; returns the response and the (mocked) publisher of the events that start work."""
    repo = Repo(document)
    with (
        patch("app.routers._dependencies.document_repository", repo),
        patch("app.routers.documents.document_repository", repo),
        patch("app.routers.documents.extracted_fields_repository") as extracted,
        patch("app.events.publish", new_callable=AsyncMock) as launch,
    ):
        extracted.get_extracted_fields.return_value = fields
        response = getattr(client, method)(url, **({"json": body} if body is not None else {}))
    return response, launch


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-005")
def test_approving_a_document_marks_it_approved():
    document, fields = _real("BizFile_333996")

    response, _ = _call(document, fields, "post", f"/documents/{document.id}/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


_EDITS = [
    ("patch", "/extracted-fields", {"extracted_fields": {"company_name": "Edited"}}),
    ("post", "/extracted-fields", {"field_name": "note", "field_value": "x"}),
    ("delete", "/extracted-fields/field-1", None),
    ("patch", "/document-type", {"document_type": "payslip"}),
]


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-007")
@pytest.mark.parametrize(("method", "path", "body"), _EDITS)
def test_an_approved_document_can_no_longer_be_edited(method, path, body):
    document, fields = _real("AnchorpointInsuranceBrokers_Constitution", DocumentStatus.APPROVED)

    response, _ = _call(document, fields, method, f"/documents/{document.id}{path}", body)

    assert response.status_code == 409
    assert "has been approved" in response.json()["detail"]


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-007")
def test_an_approved_document_cannot_be_deleted():
    document, fields = _real("AnchorpointInsuranceBrokers_Constitution", DocumentStatus.APPROVED)

    response, _ = _call(document, fields, "delete", f"/documents/{document.id}")

    assert response.status_code == 409


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-008")
def test_a_document_with_an_unreviewed_low_confidence_field_cannot_be_approved():
    document, fields = _real("MuhammadD_Payslip_Jan2026")
    fields = [f.model_copy(update={"confidence": 0.4}) if f.field_name == "net_pay" else f for f in fields]

    response, launch = _call(document, fields, "post", f"/documents/{document.id}/approve")

    assert response.status_code == 400
    assert "Low-confidence fields must be reviewed" in response.json()["detail"]
    assert "net_pay" in response.json()["detail"]
    assert document.status == DocumentStatus.ANALYZED
    launch.assert_not_called()


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-009")
async def test_detection_starts_within_five_seconds_of_approval(monkeypatch):
    document, fields = _real("BizFile_333996")
    repo = Repo(document)
    started: list[float] = []
    detection_started = asyncio.Event()

    async def detection(document_id):
        started.append(time.monotonic())
        detection_started.set()

    tasks: list[asyncio.Task] = []

    def dispatch(event):
        # The in-process transport, with only the detection-worker installed.
        handler = detection_worker.HANDLERS.get(event.type)
        if handler is not None:
            tasks.append(asyncio.get_running_loop().create_task(handler(event)))

    monkeypatch.setattr(bus, "_dispatch_in_process", dispatch)
    with (
        patch("app.routers.documents.document_repository", repo),
        patch("app.routers.documents.extracted_fields_repository") as extracted,
        patch("app.workers.detection.run_detection", detection),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
    ):
        extracted.get_extracted_fields.return_value = fields
        approved_at = time.monotonic()
        await documents_router.approve_document(document.id, document=document)
        await asyncio.wait_for(detection_started.wait(), timeout=5)
        await asyncio.gather(*tasks)

    assert document.status == DocumentStatus.APPROVED
    [start] = started
    assert start - approved_at < 5.0


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-009")
def test_approval_launches_detection_for_that_document():
    document, fields = _real("BizFile_333996")

    _, launch = _call(document, fields, "post", f"/documents/{document.id}/approve")

    [event] = [c.args[0] for c in launch.await_args_list]
    assert (event.type, event.document_id, event.case_id) == ("document.approved", document.id, document.case_id)


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-010")
@pytest.mark.parametrize(("method", "path", "body"), _EDITS)
def test_a_rejected_document_can_no_longer_be_edited(method, path, body):
    document, fields = _real("AnchorpointInsuranceBrokers_Constitution", DocumentStatus.REJECTED)

    response, _ = _call(document, fields, method, f"/documents/{document.id}{path}", body)

    assert response.status_code == 409
    assert "has been rejected" in response.json()["detail"]


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-011")
def test_rejecting_a_document_does_not_start_detection():
    document, fields = _real("AnchorpointInsuranceBrokers_Constitution")

    response, launch = _call(document, fields, "post", f"/documents/{document.id}/reject")

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    launch.assert_not_called()


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-011")
def test_a_rejected_document_cannot_then_be_approved():
    document, fields = _real("AnchorpointInsuranceBrokers_Constitution", DocumentStatus.REJECTED)

    response, launch = _call(document, fields, "post", f"/documents/{document.id}/approve")

    assert response.status_code == 400
    launch.assert_not_called()


@pytest.mark.sprint3
@pytest.mark.story("SCRUM-26", "MA-012")
def test_once_low_confidence_fields_are_saved_the_document_can_be_approved():
    # "Save fields" marks every reviewed field 1.0 (update_extracted_fields).
    document, fields = _real("MuhammadD_Payslip_Jan2026")
    low = [f.model_copy(update={"confidence": 0.4}) if f.field_name == "net_pay" else f for f in fields]
    blocked, _ = _call(document, low, "post", f"/documents/{document.id}/approve")
    assert blocked.status_code == 400

    reviewed = [f.model_copy(update={"confidence": 1.0}) if f.field_name == "net_pay" else f for f in low]
    response, launch = _call(document, reviewed, "post", f"/documents/{document.id}/approve")

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    launch.assert_called_once()
