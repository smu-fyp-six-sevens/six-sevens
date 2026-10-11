from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.langgraph.detection.binder import BindingResult
from app.fraud_detection.font_consistency import ExtractedField, FontConsistencyResult
from app.fraud_detection.image_manipulation import (
    ImageManipulationResult,
    ImageTamperingResult,
    TamperingTolerances,
    Tolerances,
)
from app.fraud_detection.metadata_analysis import MetadataAnalysisResult
from app.fraud_detection.layout_anomalies import CheckAssessment, LayoutAnomalyResult
from app.fraud_detection.date_anomalies import DateAnomalyResult
from app.fraud_detection.spelling_anomaly import SpellingAnomaly, SpellingAnomalyResult
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


def _mark_detection_already_ran(mock_trace) -> None:
    """Satisfies _require_detection_has_run's gate for the individual-check
    endpoints below — a full detection pass must have produced a report
    before an on-demand single check is allowed to run."""
    mock_trace.get_persisted.return_value = {"detection_report": {"verdict": "clean"}}


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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        mock_fields.get_extracted_fields.return_value = fields
        _mark_detection_already_ran(mock_trace)

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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        mock_fields.get_extracted_fields.side_effect = RuntimeError("fields down")
        _mark_detection_already_ran(mock_trace)

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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/layout-anomalies")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "doc-1"
    assert body["unable_to_assess"] is False
    assert body["margin"]["unable_to_assess"] is True
    mock_check.assert_called_once()


def test_font_consistency_fetch_errors() -> None:
    with patch("app.routers._dependencies.document_repository") as mock_repo:
        mock_repo.get_document.side_effect = RuntimeError("db down")
        assert client.get("/documents/doc-1/detection/font-consistency").status_code == 502

    with patch("app.routers._dependencies.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None
        assert client.get("/documents/doc-1/detection/font-consistency").status_code == 404

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document(storage_path=None)
        _mark_detection_already_ran(mock_trace)
        assert client.get("/documents/doc-1/detection/font-consistency").status_code == 404

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.side_effect = RuntimeError("storage down")
        _mark_detection_already_ran(mock_trace)
        assert client.get("/documents/doc-1/detection/font-consistency").status_code == 502

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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.download_file.return_value = b"%PDF-1.4 fake bytes"
        mock_fields_repo.get_extracted_fields.return_value = []
        mock_check.return_value = result
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/spelling-anomaly")

    assert response.status_code == 200
    body = response.json()
    assert body["anomalies"][0]["word"] == "Helllo"
    assert body["unable_to_assess"] is False

    mock_check.assert_awaited_once_with("doc-1", b"%PDF-1.4 fake bytes", "statement.pdf", [])


def test_get_spelling_anomaly_fetch_document_failure_returns_502() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch(
            "app.routers.detection.check_spelling_anomalies", new_callable=AsyncMock
        ) as mock_check,
    ):
        mock_repo.get_document.side_effect = RuntimeError("Supabase table access failed")

        response = client.get("/documents/doc-1/detection/spelling-anomaly")

    assert response.status_code == 502
    mock_check.assert_not_called()


def test_get_spelling_anomaly_document_not_found() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch(
            "app.routers.detection.check_spelling_anomalies", new_callable=AsyncMock
        ) as mock_check,
    ):
        mock_repo.get_document.return_value = None

        response = client.get("/documents/missing-id/detection/spelling-anomaly")

    assert response.status_code == 404
    mock_check.assert_not_called()


def test_get_spelling_anomaly_storage_failure_returns_502() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch(
            "app.routers.detection.check_spelling_anomalies", new_callable=AsyncMock
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.download_file.side_effect = RuntimeError("bucket unreachable")
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/spelling-anomaly")

    assert response.status_code == 502
    mock_check.assert_not_called()


def test_get_metadata_anomaly_success_persists_result() -> None:
    result = MetadataAnalysisResult(
        document_id="doc-1",
        extracted_metadata={"author": "admin"},
        anomalies=[
            {"field": "author", "value": "admin", "reason": "placeholder", "severity": "low"}
        ],
        ai_generation_indicators=[],
        unable_to_assess=False,
    )

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch(
            "app.routers.detection.check_metadata_anomalies", return_value=result
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _make_document()
        _mark_detection_already_ran(mock_trace)

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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = document
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/metadata-anomaly")

    assert response.status_code == 200
    mock_check.assert_called_once_with("doc-1", {"Author": "Jane Tan"}, "bank_statement")


def test_get_metadata_anomaly_fetch_document_failure_returns_502() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.check_metadata_anomalies") as mock_check,
    ):
        mock_repo.get_document.side_effect = RuntimeError("Supabase table access failed")

        response = client.get("/documents/doc-1/detection/metadata-anomaly")

    assert response.status_code == 502
    mock_check.assert_not_called()


def test_get_metadata_anomaly_document_not_found() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.check_metadata_anomalies") as mock_check,
    ):
        mock_repo.get_document.return_value = None

        response = client.get("/documents/missing-id/detection/metadata-anomaly")

    assert response.status_code == 404
    mock_check.assert_not_called()


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
            "app.routers.detection.bind_numerical_checks",
            new=AsyncMock(return_value=BindingResult()),
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.return_value = fields
        _mark_detection_already_ran(mock_trace)

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
            "app.routers.detection.bind_numerical_checks",
            new=AsyncMock(return_value=BindingResult()),
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.side_effect = RuntimeError("fields down")
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    assert mock_check.call_args[0][1] == []
    assert response.json()["values_analysed"] == 0


def test_get_numerical_validation_writes_nothing() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.bind_numerical_checks",
            new=AsyncMock(return_value=BindingResult()),
        ),
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.return_value = []
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    # Result is returned as JSON for the lead agent; nothing is persisted.
    mock_repo.update_extracted_fields.assert_not_called()


def test_get_numerical_validation_fetch_and_not_found_errors() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.bind_numerical_checks", new=AsyncMock(return_value=BindingResult())) as mock_check,
    ):
        mock_repo.get_document.side_effect = RuntimeError("db down")
        assert (
            client.get("/documents/doc-1/detection/numerical-validation").status_code == 502
        )
        mock_check.assert_not_called()

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.bind_numerical_checks", new=AsyncMock(return_value=BindingResult())) as mock_check,
    ):
        mock_repo.get_document.return_value = None
        assert (
            client.get("/documents/missing/detection/numerical-validation").status_code == 404
        )
        mock_check.assert_not_called()


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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        _mark_detection_already_ran(mock_trace)
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/image-manipulation")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "doc-1"
    assert body["unable_to_assess"] is True
    mock_check.assert_called_once_with("doc-1", b"%PDF-1.4")


def test_image_manipulation_requires_full_detection_first() -> None:
    """Individual checks unlock only after a full detection pass, same as
    the other on-demand checks."""
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
        patch("app.routers.detection.check_image_manipulation") as mock_check,
    ):
        mock_repo.get_document.return_value = _document()
        mock_trace.get_persisted.return_value = None
        mock_trace.get.return_value = None
        response = client.get("/documents/doc-1/detection/image-manipulation")

    assert response.status_code == 409
    mock_check.assert_not_called()


def test_image_manipulation_document_missing() -> None:
    with patch("app.routers._dependencies.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None
        response = client.get("/documents/doc-1/detection/image-manipulation")
    assert response.status_code == 404


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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.return_value = fields
        _mark_detection_already_ran(mock_trace)

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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.return_value = []
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/date-anomalies")

    assert response.status_code == 200
    mock_repo.update_extracted_fields.assert_not_called()


def test_get_date_anomalies_fetch_and_not_found_errors() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.check_date_anomalies") as mock_check,
    ):
        mock_repo.get_document.side_effect = RuntimeError("db down")
        assert (
            client.get("/documents/doc-1/detection/date-anomalies").status_code == 502
        )
        mock_check.assert_not_called()

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.check_date_anomalies") as mock_check,
    ):
        mock_repo.get_document.return_value = None
        assert (
            client.get("/documents/missing/detection/date-anomalies").status_code == 404
        )
        mock_check.assert_not_called()


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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        _mark_detection_already_ran(mock_trace)
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/image-tampering")

    assert response.status_code == 200
    assert response.json()["unable_to_assess"] is False
    mock_check.assert_called_once_with("doc-1", b"%PDF-1.4")


def test_image_tampering_requires_full_detection_first() -> None:
    """Individual checks unlock only after a full detection pass, same as
    the other on-demand checks."""
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
        patch("app.routers.detection.check_image_tampering") as mock_check,
    ):
        mock_repo.get_document.return_value = _document()
        mock_trace.get_persisted.return_value = None
        mock_trace.get.return_value = None
        response = client.get("/documents/doc-1/detection/image-tampering")

    assert response.status_code == 409
    mock_check.assert_not_called()


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
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        _mark_detection_already_ran(mock_trace)
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


def test_image_integrity_requires_full_detection_first() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
        patch("app.routers.detection.check_image_manipulation") as mock_m,
        patch("app.routers.detection.check_image_tampering") as mock_t,
    ):
        mock_repo.get_document.return_value = _document()
        mock_trace.get_persisted.return_value = None
        mock_trace.get.return_value = None
        response = client.get("/documents/doc-1/detection/image-integrity")

    assert response.status_code == 409
    mock_m.assert_not_called()
    mock_t.assert_not_called()


def test_image_tampering_document_missing() -> None:
    with patch("app.routers._dependencies.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None
        response = client.get("/documents/doc-1/detection/image-tampering")
    assert response.status_code == 404


# --- detection/cancel, detection/retry --------------------------------------


def test_cancel_detection_cancels_the_tracked_task() -> None:
    from app.routers import documents as documents_router

    task = MagicMock()
    task.done.return_value = False
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch.dict(documents_router._detection_tasks, {"doc-1": task}, clear=True),
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        response = client.post("/documents/doc-1/detection/cancel")

    assert response.status_code == 200
    task.cancel.assert_called_once()


def test_cancel_detection_404_when_nothing_running() -> None:
    from app.routers import documents as documents_router

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch.dict(documents_router._detection_tasks, {}, clear=True),
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        response = client.post("/documents/doc-1/detection/cancel")

    assert response.status_code == 409


def test_retry_detection_relaunches_after_a_cancelled_run() -> None:
    from app.routers import documents as documents_router

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
        patch.object(documents_router, "_launch_detection") as mock_launch,
        patch.dict(documents_router._detection_tasks, {}, clear=True),
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        mock_trace.get_persisted.return_value = {"detection_error": "Cancelled by loan officer"}
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 200
    mock_launch.assert_called_once_with("doc-1", "case-1")


def test_retry_detection_rejected_when_already_running() -> None:
    from app.routers import documents as documents_router

    task = MagicMock()
    task.done.return_value = False
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch.object(documents_router, "_launch_detection") as mock_launch,
        patch.dict(documents_router._detection_tasks, {"doc-1": task}, clear=True),
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 409
    mock_launch.assert_not_called()


def test_retry_detection_rejected_when_a_report_already_exists() -> None:
    from app.routers import documents as documents_router

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
        patch.object(documents_router, "_launch_detection") as mock_launch,
        patch.dict(documents_router._detection_tasks, {}, clear=True),
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        mock_trace.get_persisted.return_value = {"detection_report": {"verdict": "clean"}}
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 409
    mock_launch.assert_not_called()


def test_retry_detection_rejected_when_nothing_ran_yet() -> None:
    from app.routers import documents as documents_router

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
        patch.object(documents_router, "_launch_detection") as mock_launch,
        patch.dict(documents_router._detection_tasks, {}, clear=True),
    ):
        mock_repo.get_document.return_value = _document(status=DocumentStatus.APPROVED)
        mock_trace.get_persisted.return_value = None
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 409
    mock_launch.assert_not_called()



def test_retry_detection_refused_for_unrecognised_document() -> None:
    from app.routers import documents as documents_router

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
        patch.object(documents_router, "_launch_detection") as mock_launch,
        patch.dict(documents_router._detection_tasks, {}, clear=True),
    ):
        mock_repo.get_document.return_value = _document(
            status=DocumentStatus.APPROVED, document_type="unrecognised"
        )
        mock_trace.get_persisted.return_value = {"detection_error": "crashed"}
        response = client.post("/documents/doc-1/detection/retry")

    assert response.status_code == 409
    assert response.json()["detail"] == "Detection doesn't run on unrecognised documents."
    mock_launch.assert_not_called()


def test_individual_checks_refused_for_unrecognised_document() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
        patch("app.routers.detection.check_image_manipulation") as mock_m,
        patch("app.routers.detection.check_image_tampering") as mock_t,
    ):
        mock_repo.get_document.return_value = _document(
            status=DocumentStatus.APPROVED, document_type="unrecognised"
        )
        _mark_detection_already_ran(mock_trace)
        response = client.get("/documents/doc-1/detection/image-integrity")

    assert response.status_code == 409
    assert response.json()["detail"] == "Detection doesn't run on unrecognised documents."
    mock_m.assert_not_called()
    mock_t.assert_not_called()


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
            "app.routers.detection.bind_numerical_checks",
            new=AsyncMock(return_value=BindingResult(error="api down")),
        ),
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document(document_type="source_of_wealth")
        mock_fields.get_extracted_fields.return_value = fields
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/numerical-validation")

    body = response.json()
    assert response.status_code == 200
    assert body["binding_error"] == "api down"
    # Exact-name matches still run: the balance identity is off by 100.
    assert [f["check_code"] for f in body["findings"]] == ["FS.BALANCES"]


# --- detection/ai-generation ---------------------------------------------------


def test_ai_generation_happy_path() -> None:
    from app.fraud_detection.ai_generation import AIGenerationConfig, AIGenerationResult

    result = AIGenerationResult(
        document_id="doc-1", score=0.12, likelihood="low", images=[],
        config=AIGenerationConfig(), unable_to_assess=False,
    )
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.check_ai_generation", return_value=result) as mock_check,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        _mark_detection_already_ran(mock_trace)
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/ai-generation")

    assert response.status_code == 200
    assert response.json()["score"] == 0.12
    mock_check.assert_called_once_with("doc-1", b"%PDF-1.4")


def test_ai_generation_requires_full_detection_first() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
        patch("app.routers.detection.check_ai_generation") as mock_check,
    ):
        mock_repo.get_document.return_value = _document()
        mock_trace.get_persisted.return_value = None
        mock_trace.get.return_value = None
        response = client.get("/documents/doc-1/detection/ai-generation")

    assert response.status_code == 409
    mock_check.assert_not_called()
