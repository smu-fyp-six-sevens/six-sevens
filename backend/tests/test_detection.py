from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.langgraph.detection.field_checks.binder import BindingResult
from app.fraud_detection.font_consistency import ExtractedField, FontConsistencyResult
from app.fraud_detection.images.image_manipulation import (
    ImageManipulationResult,
    ImageTamperingResult,
    TamperingTolerances,
    Tolerances,
)
from app.fraud_detection.authenticity.metadata_analysis import MetadataAnalysisResult
from app.fraud_detection.layout_anomalies import CheckAssessment, LayoutAnomalyResult
from app.fraud_detection.dates.date_anomalies import DateAnomalyResult
from app.fraud_detection.spelling.spelling_anomaly import SpellingAnomaly, SpellingAnomalyResult
from app.main import app
from app.models import DocumentRecord, DocumentStatus

client = TestClient(app)


def _document(**kwargs) -> DocumentRecord:
    values = dict(
        id="doc-1",
        case_id="case-1",
        filename="statement.pdf",
        storage_path="case-1/doc-1_statement.pdf",
        document_type="bank_statement",
        status=DocumentStatus.ANALYZED,
        uploaded_at="2026-08-26T00:00:00Z",
    )
    values.update(kwargs)
    return DocumentRecord(**values)


def _mark_detection_already_ran(mock_results) -> None:
    """Satisfies _require_detection_has_run's gate for the individual-check
    endpoints below — a full detection pass must have produced a report
    before an on-demand single check is allowed to run."""
    mock_results.get.return_value = ({"verdict": "clean"}, None)


# --- the gate every on-demand check shares ------------------------------------------

DOCUMENTS = "app.routers._dependencies.document_repository"
# (endpoint, the check a refused request must never reach)
GATED_CHECKS = [
    ("font-consistency", "check_font_consistency"),
    ("layout-anomalies", "check_layout_anomalies"),
    ("spelling-anomaly", "check_spelling_anomalies"),
    ("page-consistency", "check_page_consistency"),
    ("metadata-anomaly", "check_metadata_anomalies"),
    ("numerical-validation", "bind_checks"),
    ("external-validation", "run_external_validation"),
    ("date-anomalies", "check_date_anomalies"),
    ("image-manipulation", "check_image_manipulation"),
    ("image-tampering", "check_image_tampering"),
    ("image-integrity", "check_image_manipulation"),
    ("ai-generation", "check_ai_generation"),
]
# The ones that read the stored file.
DOWNLOADING = [pair for pair in GATED_CHECKS if pair[0] in {
    "font-consistency", "layout-anomalies", "spelling-anomaly", "page-consistency", "image-manipulation",
    "image-tampering", "image-integrity", "ai-generation",
}]


def _get_check(endpoint: str):
    return client.get(f"/documents/doc-1/detection/{endpoint}")


@pytest.mark.parametrize(("endpoint", "check"), GATED_CHECKS)
def test_a_check_returns_502_when_the_document_cannot_be_fetched(endpoint, check) -> None:
    with patch(DOCUMENTS) as repo, patch(f"app.routers.detection.{check}") as mock_check:
        repo.get_document.side_effect = RuntimeError("db down")
        response = _get_check(endpoint)

    assert response.status_code == 502
    mock_check.assert_not_called()


@pytest.mark.parametrize(("endpoint", "check"), GATED_CHECKS)
def test_a_check_returns_404_for_a_document_that_does_not_exist(endpoint, check) -> None:
    with patch(DOCUMENTS) as repo, patch(f"app.routers.detection.{check}") as mock_check:
        repo.get_document.return_value = None
        response = _get_check(endpoint)

    assert response.status_code == 404
    mock_check.assert_not_called()


@pytest.mark.parametrize(("endpoint", "check"), GATED_CHECKS)
def test_a_check_is_refused_until_a_full_detection_has_run(endpoint, check) -> None:
    with (
        patch(DOCUMENTS) as repo,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
        patch(f"app.routers.detection.{check}") as mock_check,
    ):
        repo.get_document.return_value = _document()
        mock_results.get.return_value = (None, None)
        response = _get_check(endpoint)

    assert response.status_code == 409
    mock_check.assert_not_called()


@pytest.mark.parametrize(("endpoint", "check"), GATED_CHECKS)
def test_a_check_is_refused_for_a_document_with_no_type(endpoint, check) -> None:
    with (
        patch(DOCUMENTS) as repo,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
        patch(f"app.routers.detection.{check}") as mock_check,
    ):
        repo.get_document.return_value = _document(status=DocumentStatus.APPROVED, document_type=None)
        _mark_detection_already_ran(mock_results)
        response = _get_check(endpoint)

    assert response.status_code == 409
    assert response.json()["detail"] == "Detection doesn't run on documents with no type."
    mock_check.assert_not_called()


@pytest.mark.parametrize(("endpoint", "check"), DOWNLOADING)
def test_a_check_returns_404_when_the_document_has_no_stored_file(endpoint, check) -> None:
    with (
        patch(DOCUMENTS) as repo,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
        patch(f"app.routers.detection.{check}") as mock_check,
    ):
        repo.get_document.return_value = _document(storage_path=None)
        _mark_detection_already_ran(mock_results)
        response = _get_check(endpoint)

    assert response.status_code == 404
    mock_check.assert_not_called()


@pytest.mark.parametrize(("endpoint", "check"), DOWNLOADING)
def test_a_check_returns_502_when_the_stored_file_cannot_be_downloaded(endpoint, check) -> None:
    with (
        patch(DOCUMENTS) as repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
        patch(f"app.routers.detection.{check}") as mock_check,
    ):
        repo.get_document.return_value = _document()
        mock_storage.download_file.side_effect = RuntimeError("storage down")
        _mark_detection_already_ran(mock_results)
        response = _get_check(endpoint)

    assert response.status_code == 502
    mock_check.assert_not_called()


def test_font_consistency_uses_extracted_fields() -> None:
    result = FontConsistencyResult(
        document_id="doc-1",
        anomalies=[],
        all_detected_styles=[],
        unable_to_assess=False,
    )
    fields = [ExtractedField(field_name="name", field_type="string", field_value="Jane", confidence=0.9)]
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch("app.routers.detection.check_font_consistency", return_value=result) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        mock_fields.get_extracted_fields.return_value = fields
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/font-consistency")

    assert response.status_code == 200
    mock_check.assert_called_once()
    assert mock_check.call_args[0][2] == fields


def test_font_consistency_degrades_when_fields_fetch_fails() -> None:
    result = FontConsistencyResult(
        document_id="doc-1",
        anomalies=[],
        all_detected_styles=[],
        unable_to_assess=False,
    )
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch("app.routers.detection.check_font_consistency", return_value=result) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        mock_fields.get_extracted_fields.side_effect = RuntimeError("fields down")
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/font-consistency")

    assert response.status_code == 200
    assert mock_check.call_args[0][2] == []


def test_layout_anomalies_happy_path() -> None:
    assessed = CheckAssessment(unable_to_assess=False)
    result = LayoutAnomalyResult(
        document_id="doc-1",
        anomalies=[],
        page_margins=[],
        elements_analysed=4,
        indentation=assessed,
        alignment=assessed,
        margin=CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason="Need at least 2 pages",
        ),
        spacing=assessed,
        unable_to_assess=False,
    )
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.check_layout_anomalies", return_value=result) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/layout-anomalies")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "doc-1"
    assert body["unable_to_assess"] is False
    assert body["margin"]["unable_to_assess"] is True
    mock_check.assert_called_once()


def _make_document(document_id: str = "doc-1") -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename="statement.pdf",
        storage_path=f"case-1/{document_id}_statement.pdf",
        document_type="bank_statement",
        status=DocumentStatus.ANALYZED,
        uploaded_at="2026-08-26T00:00:00Z",
    )


def test_get_spelling_anomaly_success_returns_result_with_no_persistence() -> None:
    result = SpellingAnomalyResult(
        document_id="doc-1",
        anomalies=[
            SpellingAnomaly(
                word="Helllo",
                surrounding_text="Helllo there", suggested_correction="hello",
            )
        ],
        unable_to_assess=False,
    )

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields_repo,
        patch(
            "app.routers.detection.check_spelling_anomalies", new_callable=AsyncMock
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.download_file.return_value = b"%PDF-1.4 fake bytes"
        mock_fields_repo.get_extracted_fields.return_value = []
        mock_check.return_value = result
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/spelling-anomaly")

    assert response.status_code == 200
    body = response.json()
    assert body["anomalies"][0]["word"] == "Helllo"
    assert body["unable_to_assess"] is False

    mock_check.assert_awaited_once_with("doc-1", b"%PDF-1.4 fake bytes", "statement.pdf", [])


def test_get_metadata_anomaly_success_persists_result() -> None:
    result = MetadataAnalysisResult(
        document_id="doc-1",
        extracted_metadata={"author": "admin"},
        anomalies=[
            {"code": "META.PLACEHOLDER", "field": "author", "value": "admin", "reason": "placeholder", "severity": "low"}
        ],
        ai_generation_indicators=[],
        unable_to_assess=False,
    )

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch(
            "app.routers.detection.check_metadata_anomalies", return_value=result
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _make_document()
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/metadata-anomaly")

    assert response.status_code == 200
    body = response.json()
    assert body["anomalies"][0]["field"] == "author"
    mock_check.assert_called_once_with("doc-1", None, "bank_statement")


def test_get_metadata_anomaly_passes_stored_metadata_and_document_type_through() -> None:
    result = MetadataAnalysisResult(
        document_id="doc-1",
        extracted_metadata={},
        anomalies=[],
        ai_generation_indicators=[],
        unable_to_assess=True,
        unable_to_assess_reason="No metadata was captured for this document",
    )
    document = _make_document()
    document.metadata = {"Author": "Jane Tan"}
    # The officer-confirmed type decides which rules apply — see
    # metadata_analysis.INSTITUTION_ORIGINATED_DOCUMENT_TYPES.
    document.document_type = "bank_statement"

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch(
            "app.routers.detection.check_metadata_anomalies", return_value=result
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = document
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/metadata-anomaly")

    assert response.status_code == 200
    mock_check.assert_called_once_with("doc-1", {"Author": "Jane Tan"}, "bank_statement")


def test_get_numerical_validation_passes_extracted_fields_and_document_type() -> None:
    fields = [
        ExtractedField(
            field_name="transactions", field_type="array", field_value=[], confidence=0.9
        )
    ]
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.bind_checks",
            new=AsyncMock(return_value=BindingResult()),
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.return_value = fields
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    assert response.json()["values_analysed"] == 0
    # No file download — works from extracted_fields and document_type alone.
    args = mock_check.call_args[0]
    assert args[0] == "bank_statement"
    assert args[1] == fields


def test_get_numerical_validation_degrades_when_fields_fetch_fails() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.bind_checks",
            new=AsyncMock(return_value=BindingResult()),
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.side_effect = RuntimeError("fields down")
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    assert mock_check.call_args[0][1] == []
    assert response.json()["values_analysed"] == 0


def test_get_numerical_validation_writes_nothing() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.bind_checks",
            new=AsyncMock(return_value=BindingResult()),
        ),
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.return_value = []
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    # Result is returned as JSON for the lead agent; nothing is persisted.
    mock_repo.update_extracted_fields.assert_not_called()


def test_image_manipulation_happy_path() -> None:
    result = ImageManipulationResult(
        document_id="doc-1",
        regions=[],
        indicators=[],
        pages_checked=1,
        tolerances=Tolerances(),
        unable_to_assess=True,
        unable_to_assess_reason="No embedded images found across 1 page(s)",
    )
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch(
            "app.routers.detection.check_image_manipulation", return_value=result
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        _mark_detection_already_ran(mock_results)
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/image-manipulation")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "doc-1"
    assert body["unable_to_assess"] is True
    mock_check.assert_called_once_with("doc-1", b"%PDF-1.4")


def _date_anomaly_result() -> DateAnomalyResult:
    return DateAnomalyResult(
        document_id="doc-1",
        dates_extracted=[],
        findings=[],
        skipped_checks=[],
        unable_to_assess=False,
    )


def test_get_date_anomalies_passes_extracted_fields_and_document_type() -> None:
    fields = [
        ExtractedField(
            field_name="incorporation_date", field_type="string", field_value="2020-01-01", confidence=0.9
        )
    ]
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.check_date_anomalies",
            return_value=_date_anomaly_result(),
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.return_value = fields
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/date-anomalies")

    assert response.status_code == 200
    # No file download — works from extracted_fields and document_type alone.
    args = mock_check.call_args[0]
    assert args[1] == fields
    assert args[2] == "bank_statement"


def test_get_date_anomalies_writes_nothing() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.check_date_anomalies",
            return_value=_date_anomaly_result(),
        ),
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.return_value = []
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/date-anomalies")

    assert response.status_code == 200
    mock_repo.update_extracted_fields.assert_not_called()


def test_image_tampering_happy_path() -> None:
    result = ImageTamperingResult(
        document_id="doc-1",
        regions=[],
        indicators=[],
        pages_checked=1,
        tolerances=TamperingTolerances(),
        unable_to_assess=False,
    )
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch(
            "app.routers.detection.check_image_tampering", return_value=result
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        _mark_detection_already_ran(mock_results)
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/image-tampering")

    assert response.status_code == 200
    assert response.json()["unable_to_assess"] is False
    mock_check.assert_called_once_with("doc-1", b"%PDF-1.4")


def _image_integrity_results():
    manipulation = ImageManipulationResult(
        document_id="doc-1", regions=[], indicators=[], pages_checked=1,
        tolerances=Tolerances(), unable_to_assess=False,
    )
    tampering = ImageTamperingResult(
        document_id="doc-1", regions=[], indicators=[], pages_checked=1,
        tolerances=TamperingTolerances(), unable_to_assess=False,
    )
    return manipulation, tampering


def test_image_integrity_runs_both_checks_on_one_download() -> None:
    manipulation, tampering = _image_integrity_results()
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.check_image_manipulation", return_value=manipulation) as mock_m,
        patch("app.routers.detection.check_image_tampering", return_value=tampering) as mock_t,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        _mark_detection_already_ran(mock_results)
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/image-integrity")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"report", "manipulation", "tampering"}
    assert body["report"]["status"] == "clear"
    mock_storage.download_file.assert_called_once()
    mock_m.assert_called_once_with("doc-1", b"%PDF-1.4")
    mock_t.assert_called_once_with("doc-1", b"%PDF-1.4")


# --- detection/cancel, detection/retry --------------------------------------


def test_cancel_detection_publishes_a_cancel_request(published_events) -> None:
    with patch("app.routers._dependencies.document_repository") as mock_repo:
        mock_repo.get_document.return_value = _document(status=DocumentStatus.DETECTING)
        response = client.post("/documents/doc-1/detection/cancel")

    assert response.status_code == 200
    assert [(e.type, e.document_id) for e in published_events] == [("detection.cancel_requested", "doc-1")]


def test_cancel_detection_409_when_nothing_running(published_events) -> None:
    with patch("app.routers._dependencies.document_repository") as mock_repo:
        mock_repo.get_document.return_value = _document(status=DocumentStatus.DETECTED)
        response = client.post("/documents/doc-1/detection/cancel")

    assert response.status_code == 409
    assert published_events == []


def test_cancel_detection_409_when_an_approved_document_already_has_a_report(published_events) -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        mock_results.get.return_value = ({"verdict": "clean"}, None)
        response = client.post("/documents/doc-1/detection/cancel")

    assert response.status_code == 409
    assert published_events == []


def test_cancelling_a_run_still_waiting_for_a_worker_records_it_at_once(published_events) -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.detection_results") as mock_results,
        patch("app.routers.detection.push_case_snapshot") as push,
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        mock_results.get.return_value = (None, None)
        response = client.post("/documents/doc-1/detection/cancel")

    assert response.status_code == 200
    assert response.json() == {"status": "cancelled"}
    mock_results.save_error.assert_called_once_with("doc-1", "Cancelled by loan officer")
    push.assert_awaited_once_with("case-1")
    assert [(e.type, e.document_id) for e in published_events] == [("detection.cancel_requested", "doc-1")]


def test_retry_detection_relaunches_after_a_cancelled_run(published_events) -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        mock_results.get.return_value = (None, "Cancelled by loan officer")
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 200
    assert [(e.type, e.document_id, e.case_id) for e in published_events] == [
        ("detection.retry_requested", "doc-1", "case-1")
    ]


def test_retry_detection_rejected_when_already_running(published_events) -> None:
    with patch("app.routers._dependencies.document_repository") as mock_repo:
        mock_repo.get_document.return_value = _document(status=DocumentStatus.DETECTING)
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 409
    assert published_events == []


def test_detection_runs_again_over_a_finished_report(published_events) -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.DETECTED)
        mock_results.get.return_value = ({"verdict": "clean"}, None)
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 200
    assert [e.type for e in published_events] == ["detection.retry_requested"]


def test_detection_is_not_run_on_a_document_not_yet_approved(published_events) -> None:
    with patch("app.routers._dependencies.document_repository") as mock_repo:
        mock_repo.get_document.return_value = _document(status=DocumentStatus.ANALYZED)
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 409
    assert published_events == []


def test_retry_detection_rejected_when_nothing_ran_yet(published_events) -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        mock_results.get.return_value = (None, None)
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 409
    assert published_events == []


def test_retry_detection_refused_for_untyped_document(published_events) -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document(
            status=DocumentStatus.APPROVED, document_type=None
        )
        mock_results.get.return_value = (None, "crashed")
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 409
    assert response.json()["detail"] == "Detection doesn't run on documents with no type."
    assert published_events == []


def test_get_numerical_validation_reports_a_binder_failure_and_still_runs() -> None:
    fields = [
        ExtractedField(field_name="total_assets", field_type="number", field_value="1000", confidence=0.95),
        ExtractedField(field_name="total_liabilities", field_type="number", field_value="400", confidence=0.95),
        ExtractedField(field_name="total_equity", field_type="number", field_value="500", confidence=0.95),
    ]
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.bind_checks",
            new=AsyncMock(return_value=BindingResult(error="api down")),
        ),
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        mock_repo.get_document.return_value = _document(document_type="source_of_wealth")
        mock_fields.get_extracted_fields.return_value = fields
        _mark_detection_already_ran(mock_results)

        response = client.get("/documents/doc-1/detection/numerical-validation")

    body = response.json()
    assert response.status_code == 200
    assert body["binding_error"] == "api down"
    # Exact-name matches still run: the balance identity is off by 100.
    assert [f["check_code"] for f in body["findings"]] == ["FS.BALANCES"]


# --- detection/ai-generation ---------------------------------------------------


def test_ai_generation_happy_path() -> None:
    from app.fraud_detection.authenticity.ai_generation import AIGenerationConfig, AIGenerationResult

    result = AIGenerationResult(
        document_id="doc-1", score=0.12, likelihood="low", images=[],
        config=AIGenerationConfig(), unable_to_assess=False,
    )
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.check_ai_generation", return_value=result) as mock_check,
        patch("app.routers.detection.langgraph_trace"),
        patch("app.routers.detection.detection_results") as mock_results,
    ):
        _mark_detection_already_ran(mock_results)
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/ai-generation")

    assert response.status_code == 200
    assert response.json()["score"] == 0.12
    mock_check.assert_called_once_with("doc-1", b"%PDF-1.4")


