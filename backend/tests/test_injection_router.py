from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.injection.schemas import (
    DefectType,
    FieldValue,
    InjectableDocument,
    InjectDefectRequest,
    InjectDefectResponse,
    InjectedDefect,
)
from app.injection.service import InjectionService
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


def test_inject_rejects_whitespace_only_field_name() -> None:
    # min_length=1 alone lets a whitespace-only string through — the
    # model's own validator catches that on top of the Field constraint.
    response = client.post(
        "/api/dev/inject",
        json={
            "document_id": "C001_CertIncumbency",
            "defect_type": "spelling",
            "field_name": "   ",
            "new_text": "B",
        },
    )
    assert response.status_code == 422
    assert "field_name is required" in response.text


def test_inject_rejects_whitespace_only_required_string_field() -> None:
    # font_size requires "font", passed here as a blank string rather than
    # omitted entirely — distinct from the None case, which a different
    # branch of the same validator already covers.
    response = client.post(
        "/api/dev/inject",
        json={
            "document_id": "C001_CertIncumbency",
            "defect_type": "font",
            "field_name": "company",
            "font": "   ",
        },
    )
    assert response.status_code == 422
    assert "font requires font" in response.text


def _inject_request(**overrides) -> InjectDefectRequest:
    payload = {
        "document_id": "C001_CertIncumbency",
        "defect_type": "spelling",
        "field_name": "company",
        "new_text": "B",
    }
    payload.update(overrides)
    return InjectDefectRequest(**payload)


def test_inject_defect_raises_404_when_staged_document_file_is_missing() -> None:
    # stage_generated_document couldn't find/copy the underlying template
    # file on disk — a 404, distinct from "document_id not registered at
    # all" (get_template_document returning None) above it.
    with (
        patch("app.injection.service.get_template_document", return_value=_doc()),
        patch(
            "app.injection.service.stage_generated_document",
            side_effect=FileNotFoundError("template file missing on disk"),
        ),
    ):
        with pytest.raises(HTTPException) as exc_info:
            InjectionService().inject_defect(_inject_request())

    assert exc_info.value.status_code == 404


def test_inject_defect_raises_500_when_injector_returns_wrong_defect_type() -> None:
    # Defensive check: an injector claiming to be for one defect_type but
    # returning a result tagged with another would silently mislabel the
    # response — this should fail loudly instead.
    mismatched = InjectedDefect(
        defect_type=DefectType.FONT,
        field_name="company",
        description="changed",
        before={"text": "A"},
        after={"text": "B"},
    )
    mock_injector = type("MockInjector", (), {"inject": lambda self, *a, **k: mismatched})()

    with (
        patch("app.injection.service.get_template_document", return_value=_doc()),
        patch("app.injection.service.stage_generated_document", return_value=Path("out.pdf")),
        patch("app.injection.service.get_injector", return_value=mock_injector),
    ):
        with pytest.raises(HTTPException) as exc_info:
            InjectionService().inject_defect(_inject_request(defect_type="spelling"))

    assert exc_info.value.status_code == 500
    assert "unexpected defect type" in exc_info.value.detail


def test_inject_defect_raises_500_when_changed_keys_outside_expected() -> None:
    # A spelling injector is only supposed to change "text" — one that also
    # (or instead) changes something else indicates a bug in the injector,
    # not a legitimate defect, so this should be caught rather than returned
    # to the caller as if it were a normal result.
    unexpected_change = InjectedDefect(
        defect_type=DefectType.SPELLING,
        field_name="company",
        description="changed",
        before={"text": "A", "font": "helv"},
        after={"text": "A", "font": "cour"},
    )
    mock_injector = type("MockInjector", (), {"inject": lambda self, *a, **k: unexpected_change})()

    with (
        patch("app.injection.service.get_template_document", return_value=_doc()),
        patch("app.injection.service.stage_generated_document", return_value=Path("out.pdf")),
        patch("app.injection.service.get_injector", return_value=mock_injector),
    ):
        with pytest.raises(HTTPException) as exc_info:
            InjectionService().inject_defect(_inject_request(defect_type="spelling"))

    assert exc_info.value.status_code == 500
    assert "Expected changes within" in exc_info.value.detail
