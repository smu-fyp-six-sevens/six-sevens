from __future__ import annotations

import io
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.fraud_detection.image_manipulation import ImageManipulationResult, Tolerances
from app.main import app

client = TestClient(app)


def test_test_image_manipulation_success() -> None:
    result = ImageManipulationResult(
        document_id="ignored",
        regions=[],
        indicators=[],
        pages_checked=1,
        tolerances=Tolerances(),
        unable_to_assess=False,
    )

    with patch(
        "app.routers.extraction.check_image_manipulation", return_value=result
    ) as mock_check:
        response = client.post(
            "/extraction/test-image-manipulation",
            files={"file": ("statement.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
        )

    assert response.status_code == 200
    assert response.json()["unable_to_assess"] is False
    mock_check.assert_called_once()
    assert mock_check.call_args[0][1] == b"%PDF-1.4 fake"
