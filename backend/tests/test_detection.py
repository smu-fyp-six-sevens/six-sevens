from __future__ import annotations

from unittest.mock import AsyncMock,patch

from fastapi.testclient import TestClient

from app.fraud_detection.font_consistency_v2 import ExtractedField, FontConsistencyResultV2
from app.fraud_detection.metadata_analysis import MetadataAnalysisResult
from app.fraud_detection.layout_anomalies import CheckAssessment, LayoutAnomalyResult
from app.fraud_detection.numerical_validation import (
    BenfordAnalysis,
    NumericalValidationResult,
)
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


def test_font_consistency_v2_uses_extracted_fields() -> None:
    result = FontConsistencyResultV2(
        document_id="doc-1",
        anomalies=[],
        all_detected_styles=[],
        unable_to_assess=False,
    )
    fields = [ExtractedField(field_name="name", field_type="string", field_value="Jane", confidence=0.9)]
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch("app.routers.detection.check_font_consistency_v2", return_value=result) as mock_check,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        mock_fields.get_extracted_fields.return_value = fields

        response = client.get("/documents/doc-1/detection/font-consistency-v2")

    assert response.status_code == 200
    mock_check.assert_called_once()
    assert mock_check.call_args[0][2] == fields


def test_font_consistency_v2_degrades_when_fields_fetch_fails() -> None:
    result = FontConsistencyResultV2(
        document_id="doc-1",
        anomalies=[],
        all_detected_styles=[],
        unable_to_assess=False,
    )
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch("app.routers.detection.check_font_consistency_v2", return_value=result) as mock_check,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        mock_fields.get_extracted_fields.side_effect = RuntimeError("fields down")

        response = client.get("/documents/doc-1/detection/font-consistency-v2")

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
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.check_layout_anomalies", return_value=result) as mock_check,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/layout-anomalies")

    assert response.status_code == 200
    body = response.json()
    assert body["document_id"] == "doc-1"
    assert body["unable_to_assess"] is False
    assert body["margin"]["unable_to_assess"] is True
    mock_check.assert_called_once()


def test_font_consistency_v2_fetch_errors() -> None:
    with patch("app.routers.detection.document_repository") as mock_repo:
        mock_repo.get_document.side_effect = RuntimeError("db down")
        assert client.get("/documents/doc-1/detection/font-consistency-v2").status_code == 502

    with patch("app.routers.detection.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None
        assert client.get("/documents/doc-1/detection/font-consistency-v2").status_code == 404

    with patch("app.routers.detection.document_repository") as mock_repo:
        mock_repo.get_document.return_value = _document(storage_path=None)
        assert client.get("/documents/doc-1/detection/font-consistency-v2").status_code == 404

    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.side_effect = RuntimeError("storage down")
        assert client.get("/documents/doc-1/detection/font-consistency-v2").status_code == 502

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
                page=1, section=0, word="Helllo",
                surrounding_text="Helllo there", suggested_correction="hello",
            )
        ],
        unable_to_assess=False,
    )

    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields_repo,
        patch(
            "app.routers.detection.check_spelling_anomalies", new_callable=AsyncMock
        ) as mock_check,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.download_file.return_value = b"%PDF-1.4 fake bytes"
        mock_fields_repo.get_extracted_fields.return_value = []
        mock_check.return_value = result

        response = client.get("/documents/doc-1/detection/spelling-anomaly")

    assert response.status_code == 200
    body = response.json()
    assert body["anomalies"][0]["word"] == "Helllo"
    assert body["unable_to_assess"] is False

    mock_check.assert_awaited_once_with("doc-1", b"%PDF-1.4 fake bytes", "statement.pdf", [])


def test_get_spelling_anomaly_fetch_document_failure_returns_502() -> None:
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
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
        patch("app.routers.detection.document_repository") as mock_repo,
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
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch(
            "app.routers.detection.check_spelling_anomalies", new_callable=AsyncMock
        ) as mock_check,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.download_file.side_effect = RuntimeError("bucket unreachable")

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
        patch("app.routers.detection.document_repository") as mock_repo,
        patch(
            "app.routers.detection.check_metadata_anomalies", return_value=result
        ) as mock_check,
    ):
        mock_repo.get_document.return_value = _make_document()

        response = client.get("/documents/doc-1/detection/metadata-anomaly")

    assert response.status_code == 200
    body = response.json()
    assert body["anomalies"][0]["field"] == "author"
    mock_check.assert_called_once_with("doc-1", None, None)
    # Result is returned as JSON for the lead agent; nothing is persisted.
    mock_repo.update_metadata_anomalies.assert_not_called()


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
        patch("app.routers.detection.document_repository") as mock_repo,
        patch(
            "app.routers.detection.check_metadata_anomalies", return_value=result
        ) as mock_check,
    ):
        mock_repo.get_document.return_value = document

        response = client.get("/documents/doc-1/detection/metadata-anomaly")

    assert response.status_code == 200
    mock_check.assert_called_once_with("doc-1", {"Author": "Jane Tan"}, "bank_statement")


def test_get_metadata_anomaly_fetch_document_failure_returns_502() -> None:
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.check_metadata_anomalies") as mock_check,
    ):
        mock_repo.get_document.side_effect = RuntimeError("Supabase table access failed")

        response = client.get("/documents/doc-1/detection/metadata-anomaly")

    assert response.status_code == 502
    mock_check.assert_not_called()


def test_get_metadata_anomaly_document_not_found() -> None:
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
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
        findings=[],
        benford=BenfordAnalysis(
            sample_size=0,
            digits=[],
            unable_to_assess=True,
            unable_to_assess_reason="Too few values",
        ),
        unable_to_assess=False,
    )


def test_get_numerical_validation_passes_file_and_extracted_fields() -> None:
    fields = [
        ExtractedField(
            field_name="transactions", field_type="array", field_value=[], confidence=0.9
        )
    ]
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.check_numerical_validation",
            return_value=_numerical_result(),
        ) as mock_check,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        mock_fields.get_extracted_fields.return_value = fields

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    assert response.json()["values_analysed"] == 12
    # Both sources reach the check: the file for figures in the text, the
    # extracted fields for the ones that must reconcile.
    args = mock_check.call_args[0]
    assert args[1] == b"%PDF-1.4"
    assert args[2] == "statement.pdf"
    assert args[3] == fields


def test_get_numerical_validation_degrades_when_fields_fetch_fails() -> None:
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.check_numerical_validation",
            return_value=_numerical_result(),
        ) as mock_check,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        mock_fields.get_extracted_fields.side_effect = RuntimeError("fields down")

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    assert mock_check.call_args[0][3] == []


def test_get_numerical_validation_writes_nothing() -> None:
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields,
        patch(
            "app.routers.detection.check_numerical_validation",
            return_value=_numerical_result(),
        ),
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"
        mock_fields.get_extracted_fields.return_value = []

        response = client.get("/documents/doc-1/detection/numerical-validation")

    assert response.status_code == 200
    # Result is returned as JSON for the lead agent; nothing is persisted.
    mock_repo.update_extracted_fields.assert_not_called()
    mock_repo.update_metadata_anomalies.assert_not_called()


def test_get_numerical_validation_fetch_and_not_found_errors() -> None:
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.check_numerical_validation") as mock_check,
    ):
        mock_repo.get_document.side_effect = RuntimeError("db down")
        assert (
            client.get("/documents/doc-1/detection/numerical-validation").status_code == 502
        )
        mock_check.assert_not_called()

    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.check_numerical_validation") as mock_check,
    ):
        mock_repo.get_document.return_value = None
        assert (
            client.get("/documents/missing/detection/numerical-validation").status_code == 404
        )
        mock_check.assert_not_called()


