from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from starlette.datastructures import Headers

from app.main import app
from app.routers import extraction as extraction_router

client = TestClient(app)

client = TestClient(app)

_INGEST_RESULT = {
    "file_name": "statement.pdf",
    "file_type": "application/pdf",
    "metadata": {"Author": "QA"},
    "extraction_method": "native_pdf",
    "raw_text": "Golden Harvest",
    "pages": [{"page_number": 1, "text": "Golden Harvest", "elements": []}],
    "needs_review": False,
    "status": "completed",
}


def test_extraction_test_happy_path() -> None:
    with patch("app.routers.extraction.ingest_document", return_value=_INGEST_RESULT) as mock_ingest:
        response = client.post(
            "/extraction/test",
            files={"file": ("statement.pdf", io.BytesIO(b"%PDF-1.4 text"), "application/pdf")},
        )

    assert response.status_code == 200
    assert response.json()["raw_text"] == "Golden Harvest"
    mock_ingest.assert_called_once()


def test_extraction_test_rejects_bad_type_missing_name_empty_and_too_large() -> None:
    unsupported = client.post(
        "/extraction/test",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
    )
    assert unsupported.status_code == 415

    no_name = client.post(
        "/extraction/test",
        files={"file": ("", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
    )
    assert no_name.status_code in {400, 422}

    empty = client.post(
        "/extraction/test",
        files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
    )
    assert empty.status_code == 400

    with patch("app.routers.extraction.settings.MAX_FILE_SIZE_BYTES", 4):
        too_big = client.post(
            "/extraction/test",
            files={"file": ("big.pdf", io.BytesIO(b"%PDF-too-big"), "application/pdf")},
        )
    assert too_big.status_code == 413


async def test_extraction_test_rejects_blank_filename() -> None:
    upload = UploadFile(
        file=io.BytesIO(b"%PDF-1.4"),
        filename="",
        headers=Headers({"content-type": "application/pdf"}),
    )
    with pytest.raises(HTTPException) as exc:
        await extraction_router.test_extraction(upload)
    assert exc.value.status_code == 400


def test_extraction_test_ingest_failure_returns_422() -> None:
    with patch("app.routers.extraction.ingest_document", side_effect=RuntimeError("ocr crashed")):
        response = client.post(
            "/extraction/test",
            files={"file": ("scan.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert response.status_code == 422
    assert "ocr crashed" in response.json()["detail"]
