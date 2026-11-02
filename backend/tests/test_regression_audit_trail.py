"""
Regression tests (SCRUM-323) for the case audit trail — "Test Cases - Sprint 5.xlsx", Case Management sheet, Audit
Trail (SCRUM-298): CM-064 – CM-074.

The trail is the one an officer leaves working on the captured Anchorpoint case (GLDB-02): uploading a utility bill,
the doc-worker classifying and extracting it, confirming its low-confidence type, adding a field, editing one,
cancelling a second upload while it is extracted, approving the bill, and noting it. Each step goes through the real
endpoint or worker handler, and the test reads the events they publish: those are the audit_events rows GET
/audit/events returns. Only the database is in memory, and the classification itself (the LangGraph pipeline,
covered by Sprint 2's suite) is stood in for by the result it saved on the live case.

The Audit Trail's Playwright tests (audit-trail.spec.ts) are served this trail, from
fixtures/regression/audit-trail-sprint5.json; a test below fails if it drifts. Regenerate with
REGENERATE_REGRESSION_FIXTURES=1.
"""
from __future__ import annotations

import asyncio
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.events import Event, types
from app.fraud_detection.font_consistency import ExtractedField
from app.main import app
from app.models import CaseNote, CaseRecord, DocumentRecord, DocumentStatus
from app.workers import documents as doc_worker
from tests.conftest import TEST_USER

client = TestClient(app)
FIXTURES = Path(__file__).parent / "fixtures" / "regression"
ANCHORPOINT = json.loads((FIXTURES / "anchorpoint.json").read_text())
TRAIL_FIXTURE = FIXTURES / "audit-trail-sprint5.json"

CASE = CaseRecord(**ANCHORPOINT["case"])
BILL = next(d for d in ANCHORPOINT["documents"].values() if d["filename"] == "POR_UtilityBill_20251028.jpg")
SECOND_BILL = next(d for d in ANCHORPOINT["documents"].values() if d["filename"] == "Company_UtilityBill_45040.pdf")
# The trail's clock: one step a minute, so the fixture is the same on every run.
STARTED = datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc)


class TrailStore:
    """The documents and extracted fields in memory, as document_repository and extracted_fields_repository."""

    def __init__(self) -> None:
        self.documents: dict[str, DocumentRecord] = {}
        self.fields: dict[str, dict[str, ExtractedField]] = {}

    def create_document(self, case_id, filename, storage_path):
        row = next(d for d in ANCHORPOINT["documents"].values() if d["filename"] == filename)
        self.documents[row["id"]] = DocumentRecord(
            id=row["id"], case_id=case_id, filename=filename, storage_path=storage_path,
            status=DocumentStatus.UPLOADED, uploaded_at=STARTED,
        )
        return self.documents[row["id"]]

    def get_document(self, document_id, **kwargs):
        document = self.documents.get(document_id)
        if document is None:
            return None
        values = {name: f.field_value for name, f in self.fields.get(document_id, {}).items()}
        return document.model_copy(update={"extracted_fields": values or None})

    def update_status(self, document_id, status, *, expected_current_statuses=None, **kwargs):
        document = self.documents[document_id]
        if expected_current_statuses and document.status not in expected_current_statuses:
            return False
        self.documents[document_id] = document.model_copy(update={"status": status})
        return True

    def finish_extraction(self, document_id) -> None:
        """What the pipeline saved for the document on the live case: its type, confidence and fields."""
        row = ANCHORPOINT["documents"][document_id]
        self.documents[document_id] = self.documents[document_id].model_copy(update={
            "status": DocumentStatus.ANALYZED, "document_type": row["document_type"],
            "classification_confidence": row["classification_confidence"], "routing_status": row["routing_status"],
        })
        self.fields[document_id] = {
            name: ExtractedField(field_name=name, field_type="string", field_value=f["value"], confidence=f["confidence"])
            for name, f in row["extracted_fields"].items()
        }

    def record_classification(self, document_id, *, document_type, classification_confidence, routing_status, **kwargs):
        self.documents[document_id] = self.documents[document_id].model_copy(update={
            "document_type": document_type, "classification_confidence": classification_confidence,
            "routing_status": routing_status,
        })

    def update_extracted_fields(self, document_id, *args, **kwargs):
        return self.get_document(document_id)

    def approve_document(self, document_id):
        self.update_status(document_id, DocumentStatus.APPROVED)
        return self.get_document(document_id)

    def get_extracted_fields(self, document_id):
        return list(self.fields.get(document_id, {}).values())

    def add_extracted_field(self, document_id, field_name, value, **kwargs):
        self.fields[document_id][field_name] = ExtractedField(
            field_name=field_name, field_type="string", field_value=value, confidence=1.0)

    def replace_extracted_fields(self, document_id, values, *, field_confidences, **kwargs):
        self.fields[document_id] = {
            name: ExtractedField(field_name=name, field_type="string", field_value=value, confidence=field_confidences[name])
            for name, value in values.items()
        }


def officer_trail(published_events: list) -> list[Event]:
    """Every step of the officer's work on the case, through the real endpoints; returns the trail it recorded."""
    store = TrailStore()
    with (
        patch("app.routers.upload.case_service.get_case", return_value=CASE),
        patch("app.routers.upload.storage_service.upload_file", side_effect=lambda case_id, name, *a: f"{case_id}/{name}"),
        patch("app.routers.upload.document_repository", store),
        patch("app.workers.documents.document_repository", store),
        patch("app.workers.documents.langgraph_pipeline.process_document",
              AsyncMock(side_effect=lambda document_id, *a, **k: store.finish_extraction(document_id))),
        patch("app.routers._dependencies.document_repository", store),
        patch("app.routers._dependencies.case_service.is_case_finalised", return_value=False),
        patch("app.routers._dependencies.case_service.get_case", return_value=CASE),
        patch("app.document_service.document_repository", store),
        patch("app.document_service.extracted_fields_repository", store),
        patch("app.routers.notes.document_repository", store),
        patch("app.routers.notes.notes_repository.add_note",
              side_effect=lambda **kw: CaseNote(id="note-1", created_at=STARTED, **kw)),
    ):
        for bill in (BILL, SECOND_BILL):
            uploaded = client.post("/documents/upload-batch", data={"case_id": CASE.id},
                                   files=[("files", (bill["filename"], io.BytesIO(b"\xff\xd8\xff"), "image/jpeg"))])
            assert uploaded.status_code == 202
        # The doc-worker takes the first upload; the officer cancels the second while it is still extracting.
        asyncio.run(doc_worker.on_queued(next(e for e in published_events if e.document_id == BILL["id"])))
        store.update_status(SECOND_BILL["id"], DocumentStatus.EXTRACTING)
        steps = [
            client.patch(f"/documents/{BILL['id']}/document-type", json={"document_type": "proof_of_address"}),
            client.post(f"/documents/{BILL['id']}/extracted-fields",
                        json={"field_name": "customer_reference", "field_value": "KE-8801-2234"}),
            client.patch(f"/documents/{BILL['id']}/extracted-fields", json={"extracted_fields": {
                **{f.field_name: f.field_value for f in store.get_extracted_fields(BILL["id"])},
                "individual_address": "21 Anchorvale Link #08-12 Singapore 545021",
            }}),
            client.post(f"/documents/{SECOND_BILL['id']}/cancel"),
            client.post(f"/documents/{BILL['id']}/approve"),
            client.post(f"/cases/{CASE.id}/notes",
                        json={"body": "Utility bill address matches the NRIC.", "document_id": BILL["id"]}),
        ]
    assert [r.status_code for r in steps] == [200, 200, 200, 200, 200, 201]
    return [restamped(event, n) for n, event in enumerate(published_events)]


def restamped(event: Event, n: int) -> Event:
    """The event at minute n of the trail's clock, with a fixed id: a run takes the time it recorded, ending then."""
    occurred_at = STARTED + timedelta(minutes=n)
    started_at = occurred_at - timedelta(milliseconds=event.duration_ms or 0) if event.started_at else None
    return event.model_copy(update={"event_id": f"trail-{n:02d}", "occurred_at": occurred_at, "started_at": started_at})


@pytest.fixture
def trail(published_events) -> list[Event]:
    return officer_trail(published_events)


def entries(trail: list[Event], type: str) -> list[Event]:
    return [e for e in trail if e.type == type]


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-298", "CM-064")
def test_the_trail_holds_only_this_cases_actions_in_the_order_they_happened(trail):
    assert {e.case_id for e in trail} == {CASE.id}
    assert [e.type for e in trail] == [
        types.DOCUMENT_UPLOADED, types.DOCUMENT_UPLOADED, types.DOCUMENT_ANALYZED, types.DOCUMENT_TYPE_CORRECTED,
        types.DOCUMENT_FIELD_EDITED, types.DOCUMENT_FIELD_EDITED, types.DOCUMENT_CANCELLED, types.DOCUMENT_APPROVED,
        types.NOTE_ADDED,
    ]


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-298", "CM-065")
def test_an_upload_is_recorded_with_the_file_and_the_officer(trail):
    first, second = entries(trail, types.DOCUMENT_UPLOADED)

    assert (first.document_id, first.detail["filename"]) == (BILL["id"], BILL["filename"])
    assert (first.actor_type, first.actor) == ("user", TEST_USER)
    assert second.detail["filename"] == SECOND_BILL["filename"]


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-298", "CM-066")
def test_classification_is_recorded_as_the_system_started_by_the_uploading_officer(trail):
    [analyzed] = entries(trail, types.DOCUMENT_ANALYZED)

    assert analyzed.actor_type == "agent"
    assert analyzed.detail["requested_by"] == TEST_USER
    assert analyzed.document_id == BILL["id"]


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-298", "CM-067")
def test_confirming_the_document_type_is_recorded(trail):
    [confirmed] = entries(trail, types.DOCUMENT_TYPE_CORRECTED)

    assert confirmed.actor == TEST_USER
    assert confirmed.detail == {"from_type": "proof_of_address", "to_type": "proof_of_address"}


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-298", "CM-068 / CM-069")
def test_adding_and_editing_a_field_are_recorded_with_their_values(trail):
    added, edited = entries(trail, types.DOCUMENT_FIELD_EDITED)

    assert (added.detail["action"], added.detail["changes"]) == (
        "added", [{"field": "customer_reference", "from": None, "to": "KE-8801-2234"}])
    assert edited.detail["action"] == "saved"
    assert edited.detail["changes"] == [{
        "field": "individual_address", "from": BILL["extracted_fields"]["individual_address"]["value"],
        "to": "21 Anchorvale Link #08-12 Singapore 545021",
    }]
    assert added.actor == edited.actor == TEST_USER


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-298", "CM-070")
def test_cancelling_processing_is_recorded_with_the_step_it_stopped(trail):
    [cancelled] = entries(trail, types.DOCUMENT_CANCELLED)

    assert (cancelled.document_id, cancelled.actor) == (SECOND_BILL["id"], TEST_USER)
    assert cancelled.detail == {"from_status": "extracting"}


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-298", "CM-071 / CM-072")
def test_a_note_and_an_approval_name_the_officer(trail):
    [approved] = entries(trail, types.DOCUMENT_APPROVED)
    [note] = entries(trail, types.NOTE_ADDED)

    assert (approved.actor_type, approved.actor, approved.document_id) == ("user", TEST_USER, BILL["id"])
    assert (note.actor, note.document_id) == (TEST_USER, BILL["id"])


@pytest.mark.sprint5
@pytest.mark.story("SCRUM-298", "CM-073 / CM-074")
def test_edits_take_their_place_in_time_order(trail):
    times = [e.occurred_at for e in trail]
    assert times == sorted(times)
    # The field edits sit between the type confirmation and the approval, where they happened.
    order = [e.type for e in trail]
    assert order.index(types.DOCUMENT_TYPE_CORRECTED) < order.index(types.DOCUMENT_FIELD_EDITED)
    assert max(i for i, t in enumerate(order) if t == types.DOCUMENT_FIELD_EDITED) < order.index(types.DOCUMENT_APPROVED)


def test_the_playwright_suites_audit_trail_matches_the_backend(trail):
    rows = [e.model_dump(mode="json") for e in trail]

    if os.environ.get("REGENERATE_REGRESSION_FIXTURES"):
        TRAIL_FIXTURE.write_text(json.dumps(rows, indent=2, sort_keys=True) + "\n")
    assert json.loads(TRAIL_FIXTURE.read_text()) == rows, (
        "audit-trail-sprint5.json is out of date with the audit events — regenerate it (see above)"
    )
