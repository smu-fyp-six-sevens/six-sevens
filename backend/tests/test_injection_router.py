from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from app.injection.schemas import (
    DefectType,
    FieldValue,
    InjectableDocument,
    InjectDefectResponse,
)
from app.main import app

client = TestClient(app)


def _doc() -> InjectableDocument:
    return InjectableDocument(
        id="C001_CertIncumbency",
        document_type="custom",
        filename="C001_CertIncumbency.pdf",
        fields={
            "company": FieldValue(text="Golden Harvest", page=0, kind="text"),
            "logo": FieldValue(text="[image]", page=0, kind="image", rect=[1, 2, 3, 4]),
        },
        field_source="pdf_text",
    )


def test_list_injection_documents() -> None:
    with patch("app.routers.injection.list_documents", return_value=[_doc()]):
        response = client.get("/api/dev/documents")

    assert response.status_code == 200
    assert response.json() == [
        {
            "document_id": "C001_CertIncumbency",
            "filename": "C001_CertIncumbency.pdf",
            "field_count": 2,
        }
    ]


def test_get_document_fields_found_and_missing() -> None:
    with patch("app.routers.injection.get_document", return_value=_doc()):
        response = client.get("/api/dev/documents/C001_CertIncumbency/fields")

    assert response.status_code == 200
    names = {item["field_name"] for item in response.json()}
    assert names == {"company", "logo"}
    assert response.json()[0]["source"] == "pdf_text"

    with patch("app.routers.injection.get_document", return_value=None):
        missing = client.get("/api/dev/documents/missing/fields")
    assert missing.status_code == 404


def test_inject_delegates_to_the_service() -> None:
    payload = InjectDefectResponse(
        document_id="C001_CertIncumbency",
        document_type="custom",
        filename="C001_CertIncumbency.pdf",
        defect_type=DefectType.SPELLING,
        field_name="company",
        description="changed",
        before_text="A",
        after_text="B",
        defect_count=1,
    )
    with patch("app.routers.injection.injection_service") as mock_service:
        mock_service.inject_defect.return_value = payload
        response = client.post(
            "/api/dev/inject",
            json={
                "document_id": "C001_CertIncumbency",
                "defect_type": "spelling",
                "field_name": "company",
                "new_text": "B",
            },
        )
    assert response.status_code == 200
    assert response.json()["after_text"] == "B"
    mock_service.inject_defect.assert_called_once()
