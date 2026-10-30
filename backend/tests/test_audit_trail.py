"""
The case audit trail names the loan officer behind every entry: the actor for an officer's action, and
detail.requested_by for the agent run that action started. Field edits keep each changed value.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.case_pipeline.models import CaseAnalysis
from app.events import Event, types
from app.main import app
from app.fraud_detection.font_consistency import ExtractedField
from app.models import DocumentRecord, DocumentStatus
from app.document_service import field_changes as _field_changes
from app.workers import cases as case_worker
from app.workers import detection as detection_worker
from app.workers import documents as doc_worker
from tests.conftest import TEST_USER

client = TestClient(app)

_TIMESTAMP = datetime.fromisoformat("2026-10-21T02:00:00+00:00")
OFFICER = "demo"


def _document(status: DocumentStatus = DocumentStatus.QUEUED) -> DocumentRecord:
    return DocumentRecord(
        id="doc-1", case_id="case-1", filename="payslip.pdf", document_type="payslip",
        storage_path="case-1/doc-1_payslip.pdf", status=status, uploaded_at=_TIMESTAMP,
    )


def _by_officer(type: str, **fields) -> Event:
    return Event(type=type, actor_type="user", actor=OFFICER, case_id="case-1", **fields)


def _of_type(events: list, type: str) -> Event:
    [event] = [e for e in events if e.type == type]
    return event


# --- Agent runs name the officer who started them ------------------------------


async def test_classification_and_extraction_name_the_officer_who_uploaded(published_events) -> None:
    with (
        patch("app.workers.documents.document_repository") as repo,
        patch("app.workers.documents.langgraph_pipeline") as pipeline,
    ):
        repo.get_document.side_effect = [_document(), _document(DocumentStatus.ANALYZED)]
        repo.update_status.return_value = True
        pipeline.process_document = AsyncMock()

        await doc_worker.on_queued(_by_officer(types.DOCUMENT_UPLOADED, document_id="doc-1"))

    analyzed = _of_type(published_events, types.DOCUMENT_ANALYZED)
    assert analyzed.actor_type == "agent"
    assert analyzed.detail["requested_by"] == OFFICER


async def test_a_re_extraction_names_the_officer_who_changed_the_type(published_events) -> None:
    with (
        patch("app.workers.documents.document_repository") as repo,
        patch("app.workers.documents.langgraph_pipeline") as pipeline,
    ):
        repo.get_document.return_value = _document(DocumentStatus.FAILED)
        pipeline.rerun_extraction = AsyncMock()

        await doc_worker.on_retyped(_by_officer(types.DOCUMENT_RETYPED, document_id="doc-1"))

    failed = _of_type(published_events, types.DOCUMENT_FAILED)
    assert failed.detail["requested_by"] == OFFICER


async def test_a_run_no_officer_started_names_none(published_events) -> None:
    with (
        patch("app.workers.documents.document_repository") as repo,
        patch("app.workers.documents.langgraph_pipeline") as pipeline,
    ):
        repo.get_document.side_effect = [_document(), _document(DocumentStatus.ANALYZED)]
        repo.update_status.return_value = True
        pipeline.process_document = AsyncMock()

        await doc_worker.on_queued(Event(type=types.DOCUMENT_UPLOADED, document_id="doc-1", case_id="case-1"))

    assert "requested_by" not in _of_type(published_events, types.DOCUMENT_ANALYZED).detail


async def test_a_detection_run_names_the_officer_who_approved(published_events) -> None:
    with (
        patch("app.workers.detection.run_detection", AsyncMock(return_value={"verdict": "clean", "findings": []})),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
    ):
        await detection_worker.on_requested(_by_officer(types.DOCUMENT_APPROVED, document_id="doc-1"))

    detected = _of_type(published_events, types.DOCUMENT_DETECTED)
    assert detected.detail["requested_by"] == OFFICER
    assert detected.detail["verdict"] == "clean"


async def test_a_failed_detection_run_still_names_the_officer(published_events) -> None:
    with (
        patch("app.workers.detection.run_detection", AsyncMock(side_effect=RuntimeError("boom"))),
        patch("app.workers.detection.push_case_snapshot", AsyncMock()),
    ):
        await detection_worker.on_requested(_by_officer(types.DETECTION_RETRY_REQUESTED, document_id="doc-1"))

    failed = _of_type(published_events, types.DETECTION_FAILED)
    assert failed.outcome == "failed"
    assert failed.detail["requested_by"] == OFFICER


async def test_a_case_analysis_names_the_officer_who_requested_it(published_events) -> None:
    analysis = CaseAnalysis(run_id="run-1", case_id="case-1", status="complete", fingerprint="f", started_at=_TIMESTAMP)
    with patch.object(case_worker.analysis_service, "run_requested", AsyncMock(return_value=analysis)):
        await case_worker.on_analysis_requested(_by_officer(
            types.CASE_ANALYSIS_REQUESTED, detail={"run_id": "run-1", "fingerprint": "f"},
        ))

    completed = _of_type(published_events, types.CASE_ANALYSIS_COMPLETED)
    assert completed.outcome == "success"
    assert completed.detail["requested_by"] == OFFICER


# --- Field edits keep each changed value ---------------------------------------


def test_a_field_save_records_each_changed_value_from_and_to() -> None:
    before = {"net_pay": 5000, "employer": "Acme Pte Ltd", "remarks": "", "bonus": "200"}
    after = {"net_pay": "5000", "employer": "Acme Holdings", "allowance": "300"}

    assert _field_changes(before, after) == [
        {"field": "allowance", "from": None, "to": "300"},
        {"field": "bonus", "from": "200", "to": None},
        {"field": "employer", "from": "Acme Pte Ltd", "to": "Acme Holdings"},
    ]


def test_a_number_saved_back_as_the_page_shows_it_is_not_a_change() -> None:
    # The database keeps 5000.0 and -6300.0; the page displays and sends back 5000 and -6300.
    before = {"net_pay": 5000.0, "transactions": [{"amount": -6300.0, "date": "02-Jun-26"}]}
    after = {"net_pay": "5000", "transactions": [{"date": "02-Jun-26", "amount": -6300}]}

    assert _field_changes(before, after) == []


def test_a_yes_no_value_saved_back_as_the_page_shows_it_is_not_a_change() -> None:
    assert _field_changes({"id_theft": False, "bankrupt": True}, {"id_theft": "false", "bankrupt": "true"}) == []


def test_an_edited_list_item_is_recorded_as_removed_then_added() -> None:
    before = {"transactions": [{"amount": -6300.0, "counterparty": None}, {"amount": 10.0, "counterparty": "A"}]}
    after = {"transactions": [{"amount": -6350, "counterparty": None}, {"amount": 10, "counterparty": "A"}]}

    assert _field_changes(before, after) == [{
        "field": "transactions", "from": "2 items", "to": "2 items",
        "added": ['{"amount": -6350, "counterparty": null}'],
        "removed": ['{"amount": -6300, "counterparty": null}'],
    }]


def test_an_item_added_to_the_end_of_a_long_list_is_recorded() -> None:
    # Four accounts are already past the stored length: the new fifth one must still be caught, and shown.
    account = {"grantor": "DBS Bank Ltd", "account_type": "Credit Card", "date_opened": "01/01/2020",
               "date_closed": "", "current_balance": "1,234.56", "monthly_instalment": "200.00", "product_type": ""}
    before = {"accounts": [dict(account, grantor=f"Bank {i}") for i in range(4)]}
    after = {"accounts": [*before["accounts"], dict(account, grantor="test")]}

    [change] = _field_changes(before, after)
    assert (change["from"], change["to"], change["removed"]) == ("4 items", "5 items", [])
    [added] = change["added"]
    assert '"grantor": "test"' in added


def test_an_edit_past_the_stored_length_is_still_a_change() -> None:
    before = {"notes": "x" * 600 + "a"}
    after = {"notes": "x" * 600 + "b"}

    [change] = _field_changes(before, after)
    assert change["from"] == change["to"] == "x" * 500


def test_a_long_field_value_is_cut_in_the_trail() -> None:
    [change] = _field_changes({"address": ""}, {"address": "x" * 2000})
    assert change["to"] == "x" * 500


def test_saving_fields_records_the_officer_and_what_changed(published_events) -> None:
    with (
        patch("app.routers._dependencies.document_repository") as dep_repo,
        patch("app.document_service.document_repository") as repo,
        patch("app.document_service.extracted_fields_repository") as fields_repo,
    ):
        dep_repo.get_document.return_value = _document(DocumentStatus.ANALYZED)
        repo.update_extracted_fields.return_value = repo.get_document.return_value = _document(DocumentStatus.ANALYZED)
        fields_repo.get_extracted_fields.side_effect = [
            [ExtractedField(field_name="net_pay", field_type="scalar", field_value=5000, confidence=0.4)],
            [],
        ]

        response = client.patch("/documents/doc-1/extracted-fields", json={"extracted_fields": {"net_pay": "5200"}})

    assert response.status_code == 200
    edited = _of_type(published_events, types.DOCUMENT_FIELD_EDITED)
    assert edited.actor == TEST_USER
    assert edited.detail["action"] == "saved"
    assert edited.detail["changes"] == [{"field": "net_pay", "from": "5000", "to": "5200"}]


def test_adding_and_removing_a_field_record_its_value(published_events) -> None:
    field = ExtractedField(id="field-1", field_name="net_pay", field_type="number", field_value=5000, confidence=1.0)
    with (
        patch("app.routers._dependencies.document_repository") as dep_repo,
        patch("app.document_service.document_repository") as repo,
        patch("app.document_service.extracted_fields_repository") as fields_repo,
    ):
        dep_repo.get_document.return_value = _document(DocumentStatus.ANALYZED)
        repo.update_extracted_fields.return_value = repo.get_document.return_value = _document(DocumentStatus.ANALYZED)
        fields_repo.get_extracted_fields.side_effect = [[], [field], [field], [field]]

        assert client.post(
            "/documents/doc-1/extracted-fields", json={"field_name": "net_pay", "field_value": 5000}
        ).status_code == 200
        assert client.delete("/documents/doc-1/extracted-fields/field-1").status_code == 200

    added, removed = [e for e in published_events if e.type == types.DOCUMENT_FIELD_EDITED]
    assert added.detail["changes"] == [{"field": "net_pay", "from": None, "to": "5000"}]
    assert removed.detail["changes"] == [{"field": "net_pay", "from": "5000", "to": None}]


# --- Case creation -------------------------------------------------------------


def test_creating_a_case_records_the_officer_case_number_and_company(published_events) -> None:
    from app.models import CaseRecord

    created = CaseRecord(
        id="case-1", greenlink_case_id="GLDB-0042", company_name="Acme Pte Ltd",
        created_at=_TIMESTAMP, updated_at=_TIMESTAMP,
    )
    with patch("app.routers.cases.case_service") as case_service:
        case_service.create_case.return_value = created
        assert client.post("/cases", json={"company_name": "Acme Pte Ltd"}).status_code == 201

    event = _of_type(published_events, types.CASE_CREATED)
    assert event.actor_type == "user"
    assert event.actor == TEST_USER
    assert event.case_id == "case-1"
    assert event.detail["company_name"] == "Acme Pte Ltd"
    assert event.detail["greenlink_case_id"] == "GLDB-0042"


# --- Individual detection checks ---------------------------------------------


def _check_document() -> DocumentRecord:
    return DocumentRecord(
        id="doc-1", case_id="case-1", filename="statement.pdf", storage_path="case-1/doc-1_statement.pdf",
        document_type="bank_statement", status=DocumentStatus.DETECTED, uploaded_at=_TIMESTAMP,
    )


def test_running_one_check_records_the_officer_check_and_findings(published_events) -> None:
    from app.fraud_detection.font_consistency import FontConsistencyResult

    result = FontConsistencyResult(document_id="doc-1", anomalies=[], all_detected_styles=[], unable_to_assess=False)
    with (
        patch("app.routers._dependencies.document_repository") as repo,
        patch("app.routers.detection.storage_service") as storage,
        patch("app.routers.detection.extracted_fields_repository") as fields_repo,
        patch("app.routers.detection.check_font_consistency", return_value=result),
        patch("app.routers.detection.langgraph_trace") as trace,
    ):
        repo.get_document.return_value = _check_document()
        storage.download_file.return_value = b"%PDF-1.4"
        fields_repo.get_extracted_fields.return_value = []
        trace.load.return_value = {"detection_report": {"verdict": "clean"}}

        assert client.get("/documents/doc-1/detection/font-consistency").status_code == 200

    run = _of_type(published_events, types.DETECTION_CHECK_RUN)
    assert (run.actor_type, run.actor, run.case_id, run.document_id) == ("user", TEST_USER, "case-1", "doc-1")
    assert run.outcome == "success"
    assert run.duration_ms is not None
    assert run.detail["check"] == "font-consistency"
    assert run.detail["finding_count"] == 0


def test_a_check_that_fails_partway_is_recorded_as_failed(published_events) -> None:
    with (
        patch("app.routers._dependencies.document_repository") as repo,
        patch("app.routers.detection.storage_service") as storage,
        patch("app.routers.detection.langgraph_trace") as trace,
    ):
        repo.get_document.return_value = _check_document()
        storage.download_file.side_effect = RuntimeError("storage down")
        trace.load.return_value = {"detection_report": {"verdict": "clean"}}

        assert client.get("/documents/doc-1/detection/layout-anomalies").status_code == 502

    run = _of_type(published_events, types.DETECTION_CHECK_RUN)
    assert run.outcome == "failed"
    assert run.detail["check"] == "layout-anomalies"
    assert run.detail["error"]


def test_a_check_refused_before_it_runs_records_nothing(published_events) -> None:
    with (
        patch("app.routers._dependencies.document_repository") as repo,
        patch("app.routers.detection.langgraph_trace") as trace,
    ):
        repo.get_document.return_value = _check_document()
        trace.load.return_value = {}  # detection has not run yet

        assert client.get("/documents/doc-1/detection/font-consistency").status_code == 409

    assert [e for e in published_events if e.type == types.DETECTION_CHECK_RUN] == []
