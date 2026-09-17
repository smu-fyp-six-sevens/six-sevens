from __future__ import annotations

import io
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

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
