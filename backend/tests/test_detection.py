from __future__ import annotations

from unittest.mock import AsyncMock,patch

from fastapi.testclient import TestClient

from app.fraud_detection.font_consistency import FontConsistencyResult
from app.fraud_detection.font_consistency_v2 import ExtractedField, FontConsistencyResultV2
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


def test_font_consistency_happy_path() -> None:
    result = FontConsistencyResult(
        document_id="doc-1",
        anomalies=[],
        all_detected_styles=[],
        unable_to_assess=False,
    )
    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.check_font_consistency", return_value=result) as mock_check,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.return_value = b"%PDF-1.4"

        response = client.get("/documents/doc-1/detection/font-consistency")

    assert response.status_code == 200
    assert response.json()["document_id"] == "doc-1"
    mock_check.assert_called_once()


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


def test_font_consistency_fetch_errors() -> None:
    with patch("app.routers.detection.document_repository") as mock_repo:
        mock_repo.get_document.side_effect = RuntimeError("db down")
        assert client.get("/documents/doc-1/detection/font-consistency").status_code == 502

    with patch("app.routers.detection.document_repository") as mock_repo:
        mock_repo.get_document.return_value = None
        assert client.get("/documents/doc-1/detection/font-consistency").status_code == 404

    with patch("app.routers.detection.document_repository") as mock_repo:
        mock_repo.get_document.return_value = _document(storage_path=None)
        assert client.get("/documents/doc-1/detection/font-consistency").status_code == 404

    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
    ):
        mock_repo.get_document.return_value = _document()
        mock_storage.download_file.side_effect = RuntimeError("storage down")
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


def test_get_spelling_anomaly_success_persists_and_returns_result() -> None:
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
        patch("app.routers.detection.spelling_anomaly_repository") as mock_anomaly_repo,
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
    mock_anomaly_repo.replace_spelling_anomalies.assert_called_once_with(
        "doc-1", "case-1", result.anomalies
    )


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


def test_get_spelling_anomaly_still_returns_result_when_persistence_fails() -> None:
    result = SpellingAnomalyResult(document_id="doc-1", anomalies=[], unable_to_assess=False)

    with (
        patch("app.routers.detection.document_repository") as mock_repo,
        patch("app.routers.detection.storage_service") as mock_storage,
        patch("app.routers.detection.extracted_fields_repository") as mock_fields_repo,
        patch("app.routers.detection.spelling_anomaly_repository") as mock_anomaly_repo,
        patch(
            "app.routers.detection.check_spelling_anomalies", new_callable=AsyncMock
        ) as mock_check,
    ):
        mock_repo.get_document.return_value = _make_document()
        mock_storage.download_file.return_value = b"%PDF-1.4 fake bytes"
        mock_fields_repo.get_extracted_fields.return_value = []
        mock_check.return_value = result
        mock_anomaly_repo.replace_spelling_anomalies.side_effect = RuntimeError("db down")

        response = client.get("/documents/doc-1/detection/spelling-anomaly")

    # Persistence is best-effort here — a storage hiccup shouldn't hide a
    # check result the caller already has.
    assert response.status_code == 200
    assert response.json()["anomalies"] == []

