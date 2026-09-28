from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.fraud_detection.metadata_analysis import MetadataAnalysisResult
from app.fraud_detection.layout_anomalies import CheckAssessment, LayoutAnomalyResult
from app.fraud_detection.numerical_validation import (
    BenfordAnalysis,
    NumericalValidationResult,
)
from app.fraud_detection.spelling_anomaly import SpellingAnomaly, SpellingAnomalyResult
from app.main import app

client = TestClient(app)


def test_test_spelling_anomaly_success() -> None:
    result = SpellingAnomalyResult(
        document_id="ignored",
        anomalies=[
            SpellingAnomaly(
                page=1, section=0, word="Helllo",
                surrounding_text="Helllo there", suggested_correction="hello",
            )
        ],
        unable_to_assess=False,
    )

    with patch(
        "app.routers.extraction.check_spelling_anomalies", new_callable=AsyncMock
    ) as mock_check:
        mock_check.return_value = result

        response = client.post(
            "/extraction/test-spelling-anomaly",
            files={"file": ("statement.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["anomalies"][0]["word"] == "Helllo"
    mock_check.assert_awaited_once()
    # Doesn't require or forward a real document/case — only the file bytes
    # and filename matter for this dev-tool endpoint.
    args = mock_check.call_args[0]
    assert args[1] == b"%PDF-1.4 fake"
    assert args[2] == "statement.pdf"


def test_test_spelling_anomaly_rejects_unsupported_content_type() -> None:
    with patch(
        "app.routers.extraction.check_spelling_anomalies", new_callable=AsyncMock
    ) as mock_check:
        response = client.post(
            "/extraction/test-spelling-anomaly",
            files={"file": ("virus.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        )

    assert response.status_code == 415
    mock_check.assert_not_called()


def test_test_spelling_anomaly_rejects_empty_file() -> None:
    with patch(
        "app.routers.extraction.check_spelling_anomalies", new_callable=AsyncMock
    ) as mock_check:
        response = client.post(
            "/extraction/test-spelling-anomaly",
            files={"file": ("statement.pdf", io.BytesIO(b""), "application/pdf")},
        )

    assert response.status_code == 400
    mock_check.assert_not_called()


def test_test_metadata_anomaly_success() -> None:
    result = MetadataAnalysisResult(
        document_id="ignored",
        extracted_metadata={"author": "Jane Tan"},
        anomalies=[],
        ai_generation_indicators=[],
        unable_to_assess=False,
    )

    with (
        patch(
            "app.routers.extraction.extract_metadata", return_value={"Author": "Jane Tan"}
        ) as mock_extract,
        patch(
            "app.routers.extraction.check_metadata_anomalies", return_value=result
        ) as mock_check,
    ):
        response = client.post(
            "/extraction/test-metadata-anomaly",
            files={"file": ("statement.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["extracted_metadata"]["author"] == "Jane Tan"
    mock_extract.assert_called_once_with(b"%PDF-1.4 fake", "statement.pdf")
    mock_check.assert_called_once()
    assert mock_check.call_args[0][1] == {"Author": "Jane Tan"}
    # No document_type supplied — only the type-independent rules apply.
    assert mock_check.call_args[0][2] is None


def test_test_metadata_anomaly_forwards_optional_document_type() -> None:
    result = MetadataAnalysisResult(
        document_id="ignored",
        extracted_metadata={},
        anomalies=[],
        ai_generation_indicators=[],
        unable_to_assess=False,
    )

    with (
        patch("app.routers.extraction.extract_metadata", return_value={}),
        patch(
            "app.routers.extraction.check_metadata_anomalies", return_value=result
        ) as mock_check,
    ):
        response = client.post(
            "/extraction/test-metadata-anomaly",
            files={"file": ("statement.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
            data={"document_type": "bank_statement"},
        )

    assert response.status_code == 200
    assert mock_check.call_args[0][2] == "bank_statement"


def test_test_metadata_anomaly_treats_blank_document_type_as_unset() -> None:
    # The dev tool's dropdown submits "" when left on its default option.
    result = MetadataAnalysisResult(
        document_id="ignored",
        extracted_metadata={},
        anomalies=[],
        ai_generation_indicators=[],
        unable_to_assess=False,
    )

    with (
        patch("app.routers.extraction.extract_metadata", return_value={}),
        patch(
            "app.routers.extraction.check_metadata_anomalies", return_value=result
        ) as mock_check,
    ):
        response = client.post(
            "/extraction/test-metadata-anomaly",
            files={"file": ("statement.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
            data={"document_type": ""},
        )

    assert response.status_code == 200
    assert mock_check.call_args[0][2] is None


def test_test_metadata_anomaly_rejects_unsupported_content_type() -> None:
    with patch("app.routers.extraction.check_metadata_anomalies") as mock_check:
        response = client.post(
            "/extraction/test-metadata-anomaly",
            files={"file": ("virus.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        )

    assert response.status_code == 415
    mock_check.assert_not_called()


def test_test_metadata_anomaly_rejects_empty_file() -> None:
    with patch("app.routers.extraction.check_metadata_anomalies") as mock_check:
        response = client.post(
            "/extraction/test-metadata-anomaly",
            files={"file": ("statement.pdf", io.BytesIO(b""), "application/pdf")},
        )

    assert response.status_code == 400
    mock_check.assert_not_called()
    
def test_test_layout_anomalies_success() -> None:
    assessed = CheckAssessment(unable_to_assess=False)
    result = LayoutAnomalyResult(
        document_id="ignored",
        anomalies=[],
        elements=[],
        page_margins=[],
        elements_analysed=0,
        indentation=assessed,
        alignment=assessed,
        margin=assessed,
        spacing=assessed,
        unable_to_assess=False,
    )

    with patch("app.routers.extraction.check_layout_anomalies", return_value=result) as mock_check:
        response = client.post(
            "/extraction/test-layout-anomalies",
            files={
                "file": ("C018_Constitution.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")
            },
        )

    assert response.status_code == 200
    assert response.json()["unable_to_assess"] is False
    mock_check.assert_called_once()
    assert mock_check.call_args[0][1] == b"%PDF-1.4 fake"


def test_test_numerical_validation_success() -> None:
    result = NumericalValidationResult(
        document_id="ignored",
        values_analysed=7,
        findings=[],
        benford=BenfordAnalysis(
            sample_size=0,
            digits=[],
            unable_to_assess=True,
            unable_to_assess_reason="Too few values",
        ),
        unable_to_assess=False,
    )

    with patch(
        "app.routers.extraction.check_numerical_validation", return_value=result
    ) as mock_check:
        response = client.post(
            "/extraction/test-numerical-validation",
            files={"file": ("statement.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        )

    assert response.status_code == 200
    assert response.json()["values_analysed"] == 7
    args = mock_check.call_args[0]
    assert args[1] == b"%PDF-1.4 fake"
    assert args[2] == "statement.pdf"
    # No classified document behind an ad-hoc upload, so no extracted
    # fields are forwarded and the arithmetic checks have nothing to run on.
    assert len(args) == 3


def test_test_numerical_validation_rejects_unsupported_content_type() -> None:
    with patch("app.routers.extraction.check_numerical_validation") as mock_check:
        response = client.post(
            "/extraction/test-numerical-validation",
            files={"file": ("virus.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        )

    assert response.status_code == 415
    mock_check.assert_not_called()


def test_test_numerical_validation_rejects_empty_file() -> None:
    with patch("app.routers.extraction.check_numerical_validation") as mock_check:
        response = client.post(
            "/extraction/test-numerical-validation",
            files={"file": ("statement.pdf", io.BytesIO(b""), "application/pdf")},
        )

    assert response.status_code == 400
    mock_check.assert_not_called()
