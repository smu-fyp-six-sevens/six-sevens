from __future__ import annotations

from unittest.mock import AsyncMock,patch

from fastapi.testclient import TestClient

from app.fraud_detection.font_consistency import ExtractedField, FontConsistencyResult
from app.fraud_detection.image_manipulation import ImageManipulationResult, Tolerances
from app.fraud_detection.image_tampering import ImageTamperingResult, Tolerances as TamperingTolerances
from app.fraud_detection.metadata_analysis import MetadataAnalysisResult
from app.fraud_detection.layout_anomalies import CheckAssessment, LayoutAnomalyResult
from app.fraud_detection.numerical_validation import (
    BenfordAnalysis,
    NumericalValidationResult,
)
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
    mock_check.assert_called_once_with("doc-1", None, None)


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


def _numerical_result() -> NumericalValidationResult:
    return NumericalValidationResult(
        document_id="doc-1",
        values_analysed=12,
        skipped_checks=[],
        findings=[],
        benford=BenfordAnalysis(
            sample_size=0,
            digits=[],
            unable_to_assess=True,
            unable_to_assess_reason="Too few values",
        ),
        unable_to_assess=False,
    )


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
            "app.routers.detection.check_numerical_validation",
            return_value=_numerical_result(),
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.return_value = fields
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    assert response.json()["values_analysed"] == 12
    # No file download — works from extracted_fields and document_type alone.
    args = mock_check.call_args[0]
    assert args[1] == fields
    assert args[2] is None


def test_get_numerical_validation_degrades_when_fields_fetch_fails() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.check_numerical_validation",
            return_value=_numerical_result(),
        ) as mock_check,
        patch("app.routers.detection.langgraph_trace") as mock_trace,
    ):
        mock_repo.get_document.return_value = _document()
        mock_fields.get_extracted_fields.side_effect = RuntimeError("fields down")
        _mark_detection_already_ran(mock_trace)

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    assert mock_check.call_args[0][1] == []


def test_get_numerical_validation_writes_nothing() -> None:
    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.check_numerical_validation",
            return_value=_numerical_result(),
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
        patch("app.routers.detection.check_numerical_validation") as mock_check,
    ):
        mock_repo.get_document.side_effect = RuntimeError("db down")
        assert (
            client.get("/documents/doc-1/detection/numerical-validation").status_code == 502
        )
        mock_check.assert_not_called()

    with (
        patch("app.routers._dependencies.document_repository") as mock_repo,
        patch("app.routers.detection.check_numerical_validation") as mock_check,
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
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/image-manipulation")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "doc-1"
    assert body["unable_to_assess"] is True
    mock_check.assert_called_once_with("doc-1", b"%PDF-1.4")


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
    assert args[2] is None


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
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/image-tampering")

    assert response.status_code == 200
    assert response.json()["unable_to_assess"] is False
    mock_check.assert_called_once_with("doc-1", b"%PDF-1.4")


def test_image_tampering_document_missing() -> None:
    with patch("app.routers._dependencies.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None
        response = client.get("/documents/doc-1/detection/image-tampering")
    assert response.status_code == 404

