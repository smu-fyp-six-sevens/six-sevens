from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.injection import pdf as injection_pdf
from app.injection.pdf import discover_fields_from_image, read_document_metadata
from app.injection.schemas import DefectType
from app.injection.store import get_document, get_document_file_path, reset_sample_documents
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_documents(isolated_injection_templates) -> None:
    reset_sample_documents()


def _inject(document_id: str, field_name: str | None, defect_type: DefectType):
    payload: dict = {
        "document_id": document_id,
        "defect_type": defect_type.value,
    }
    if field_name is not None:
        payload["field_name"] = field_name
    return client.post("/api/dev/inject", json=payload)


def test_image_templates_keep_original_format() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    assert document.filename.endswith(".png")
    path = Path(get_document_file_path("ocbc_sample") or "")
    assert path.exists()
    assert path.suffix.lower() == ".png"
    assert not path.read_bytes().startswith(b"%PDF")


def test_image_document_id_accepts_extension() -> None:
    with_ext = get_document("ocbc_sample.png")
    without_ext = get_document("ocbc_sample")
    assert with_ext is not None
    assert without_ext is not None
    assert with_ext.id == without_ext.id == "ocbc_sample"
    assert with_ext.filename == "ocbc_sample.png"


def test_ocr_discovers_text_on_png() -> None:
    fields = discover_fields_from_image(injection_pdf.TEMPLATES_DIR / "ocbc_sample.png")
    assert len(fields) > 5
    joined = " ".join(field.text for field in fields.values()).upper()
    assert "OCBC" in joined


def test_can_inject_into_png_and_stay_png() -> None:
    document = get_document("ocbc_sample")
    assert document is not None

    numeric = next(
        (
            name
            for name, field in document.fields.items()
            if any(ch.isdigit() for ch in field.text)
        ),
        None,
    )
    field_name = numeric or next(iter(document.fields))
    defect = DefectType.NUMERICAL if numeric else DefectType.SPELLING

    response = _inject("ocbc_sample.png", field_name, defect)

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_id"] == "ocbc_sample"
    assert payload["filename"] == "ocbc_sample.png"
    assert payload["field_name"] == field_name
    assert payload["defect_count"] == 1

    path = Path(get_document_file_path("ocbc_sample") or "")
    assert path.suffix.lower() == ".png"
    assert path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_can_inject_metadata_into_png() -> None:
    document = get_document("ocbc_sample")
    assert document is not None
    field_name = next(iter(document.fields))
    before_meta = read_document_metadata(Path(get_document_file_path("ocbc_sample") or ""))

    response = _inject("ocbc_sample.png", field_name, DefectType.METADATA)

    assert response.status_code == 200
    after_meta = read_document_metadata(Path(get_document_file_path("ocbc_sample") or ""))
    assert after_meta != before_meta


def test_fields_endpoint_works_for_image() -> None:
    response = client.get("/api/dev/documents/ocbc_sample.png/fields")

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) >= 1
    assert all(item["source"] == "ocr" for item in payload)
    assert all(item["field_name"] and item["text"] for item in payload)


def test_png_fields_include_recognised_names_and_text() -> None:
    response = client.get("/api/dev/documents/ocbc_sample.png/fields")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) > 5
    assert all(item["source"] == "ocr" for item in payload)

    by_name = {item["field_name"]: item["text"] for item in payload}
    assert "ocbc" in by_name
    assert "OCBC" in by_name["ocbc"].upper()
    joined = " ".join(by_name.values()).upper()
    assert "STATEMENT" in joined or "OCBC" in joined
