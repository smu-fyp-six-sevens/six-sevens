"""Every fraud defect type on PDF, PNG, and JPEG, plus shared validation."""

from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.injection.field_text import is_label_only
from app.injection.injectors import INJECTORS
from app.injection.pdf import MANIPULATION_INDICATORS, _save_raster_image
from app.injection.schemas import DefectType
from app.injection.service import CHANGED_KEYS
from app.injection.store import get_document, get_document_file_path, reset_sample_documents
from app.main import app

client = TestClient(app)

PDF_ID = "C001_CertIncumbency"
PNG_ID = "ocbc_sample"
JPEG_ID = "ocbc_jpeg"

_MAGIC = {
    ".pdf": b"%PDF",
    ".png": b"\x89PNG",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
}


@pytest.fixture(autouse=True)
def _reset_documents(isolated_injection_templates: Path) -> Path:
    reset_sample_documents()
    return isolated_injection_templates


def _register_jpeg(templates: Path) -> str:
    source = templates / f"{PNG_ID}.png"
    dest = templates / f"{JPEG_ID}.jpg"
    _save_raster_image(Image.open(source).convert("RGB"), dest)
    reset_sample_documents()
    return JPEG_ID


def _inject(document_id: str, field_name: str, defect_type: DefectType, **extra):
    payload = {
        "document_id": document_id,
        "defect_type": defect_type.value,
        "field_name": field_name,
        **extra,
    }
    return client.post("/api/dev/inject", json=payload)


def _pick_fields(document) -> tuple[str, str, str]:
    text = next(
        (
            name
            for name, field in document.fields.items()
            if field.kind != "image"
            and field.text.strip()
            and not is_label_only(field.text)
            and not any(ch.isdigit() for ch in field.text)
        ),
        None,
    )
    numeric = next(
        (
            name
            for name, field in document.fields.items()
            if field.kind != "image"
            and not is_label_only(field.text)
            and any(ch.isdigit() for ch in field.text)
        ),
        None,
    )
    image = next(
        (name for name, field in document.fields.items() if field.kind == "image"),
        None,
    )
    assert text, f"{document.id} has no injectable text field"
    assert numeric, f"{document.id} has no injectable numeric field"
    assert image, f"{document.id} has no image field"
    return text, numeric, image


def _payload_for(defect_type: DefectType, document, text: str, numeric: str, image: str):
    text_field = document.fields[text]
    num_field = document.fields[numeric]
    if defect_type is DefectType.FONT:
        font = "cour"
        if (text_field.font or "").lower() in {"cour", "courier"}:
            font = "tiro"
        return text, {"font": font}
    if defect_type is DefectType.FONT_SIZE:
        return text, {"font_size": round(text_field.font_size + 8.0, 1)}
    if defect_type is DefectType.NUMERICAL:
        replacement = "100001" if "100001" not in num_field.text else "100002"
        return numeric, {"new_text": replacement}
    if defect_type is DefectType.SPELLING:
        spelling = text_field.text + "X" if not text_field.text.endswith("X") else text_field.text + "Z"
        return text, {"new_text": spelling}
    if defect_type is DefectType.METADATA:
        return text, {"metadata": {"author": "FraudTests"}}
    if defect_type is DefectType.MISALIGNMENT:
        return text, {"x": text_field.x + 24.0}
    if defect_type is DefectType.EMPHASIS:
        return text, {"style": "italic"}
    if defect_type is DefectType.TEXT_OVERLAY:
        return text, {"new_text": "CERT COPY"}
    if defect_type is DefectType.IMAGE_MANIPULATION:
        return image, {"indicator": "compression_mismatch"}
    raise AssertionError(f"unhandled defect {defect_type}")


def _assert_changed_keys(document_id: str, field_name: str, defect_type: DefectType, before: dict) -> None:
    after_doc = get_document(document_id)
    assert after_doc is not None
    after = after_doc.fields[field_name].model_dump()
    changed = {key for key in before if before[key] != after[key]}
    expected = CHANGED_KEYS[defect_type]
    assert changed, f"{defect_type.value} did not change {field_name}"
    assert changed <= expected, f"{defect_type.value} changed {sorted(changed)}, expected subset of {sorted(expected)}"


@pytest.mark.parametrize("defect_type", list(DefectType))
def test_every_defect_type_has_an_injector_and_changed_keys(defect_type: DefectType) -> None:
    assert defect_type in INJECTORS
    assert defect_type in CHANGED_KEYS
    assert INJECTORS[defect_type].defect_type is defect_type


@pytest.mark.parametrize(
    "document_id,suffix",
    [
        (PDF_ID, ".pdf"),
        (PNG_ID, ".png"),
        (JPEG_ID, ".jpg"),
    ],
)
def test_all_defect_types_inject_on_each_format(
    isolated_injection_templates: Path, document_id: str, suffix: str
) -> None:
    if document_id == JPEG_ID:
        _register_jpeg(isolated_injection_templates)
    document = get_document(document_id)
    assert document is not None
    text, numeric, image = _pick_fields(document)

    for defect_type in DefectType:
        field_name, extra = _payload_for(defect_type, document, text, numeric, image)
        before = document.fields[field_name].model_dump()
        response = _inject(document_id, field_name, defect_type, **extra)
        assert response.status_code == 200, (defect_type.value, response.json())
        payload = response.json()
        assert payload["defect_type"] == defect_type.value
        assert payload["field_name"] == field_name
        assert payload["filename"].endswith(suffix)
        assert payload["generated_filename"].endswith(suffix)
        assert payload["description"]
        path = Path(get_document_file_path(document_id) or "")
        assert path.exists()
        assert path.read_bytes()[: len(_MAGIC[suffix])] == _MAGIC[suffix]
        _assert_changed_keys(document_id, field_name, defect_type, before)


def test_image_manipulation_accepts_every_indicator() -> None:
    document = get_document(PDF_ID)
    assert document is not None
    image = next(name for name, field in document.fields.items() if field.kind == "image")
    for indicator in MANIPULATION_INDICATORS:
        extra = {"indicator": indicator}
        if indicator == "scaling_anomaly":
            extra.update(scale_x=1.6, scale_y=0.5)
        response = _inject(PDF_ID, image, DefectType.IMAGE_MANIPULATION, **extra)
        assert response.status_code == 200, (indicator, response.json())
        assert indicator.replace("_", " ") in response.json()["description"].lower()


@pytest.mark.parametrize(
    "defect_type,body",
    [
        (DefectType.FONT, {}),
        (DefectType.FONT_SIZE, {}),
        (DefectType.NUMERICAL, {}),
        (DefectType.SPELLING, {}),
        (DefectType.TEXT_OVERLAY, {}),
        (DefectType.METADATA, {}),
        (DefectType.EMPHASIS, {}),
        (DefectType.IMAGE_MANIPULATION, {}),
        (DefectType.MISALIGNMENT, {}),
        (DefectType.METADATA, {"metadata": {}}),
    ],
)
def test_missing_required_control_is_rejected(defect_type: DefectType, body: dict) -> None:
    document = get_document(PDF_ID)
    assert document is not None
    field_name = next(iter(document.fields))
    response = client.post(
        "/api/dev/inject",
        json={"document_id": PDF_ID, "defect_type": defect_type.value, "field_name": field_name, **body},
    )
    assert response.status_code == 422


def test_same_value_overrides_are_rejected() -> None:
    document = get_document(PDF_ID)
    assert document is not None
    text, numeric, _image = _pick_fields(document)
    cases = [
        (text, DefectType.FONT, {"font": document.fields[text].font or "helv"}),
        (text, DefectType.FONT_SIZE, {"font_size": document.fields[text].font_size}),
        (numeric, DefectType.NUMERICAL, {"new_text": document.fields[numeric].text}),
        (text, DefectType.SPELLING, {"new_text": document.fields[text].text}),
        (text, DefectType.MISALIGNMENT, {"x": document.fields[text].x, "y": document.fields[text].y}),
    ]
    for field_name, defect_type, extra in cases:
        response = _inject(PDF_ID, field_name, defect_type, **extra)
        assert response.status_code == 400, (defect_type.value, response.json())
        assert "match" in response.json()["detail"].lower()


def test_numerical_rejects_a_non_numeric_value() -> None:
    document = get_document(PDF_ID)
    assert document is not None
    text, _numeric, _image = _pick_fields(document)
    response = _inject(PDF_ID, text, DefectType.NUMERICAL, new_text="999")
    assert response.status_code == 400
    assert "numeric" in response.json()["detail"].lower()


def test_emphasis_rejects_unknown_style() -> None:
    document = get_document(PDF_ID)
    assert document is not None
    text, _numeric, _image = _pick_fields(document)
    response = _inject(PDF_ID, text, DefectType.EMPHASIS, style="wavy")
    assert response.status_code == 400
    assert "style" in response.json()["detail"].lower()


def test_text_defects_reject_image_fields() -> None:
    document = get_document(PDF_ID)
    assert document is not None
    _text, _numeric, image = _pick_fields(document)
    blocked = [
        (DefectType.FONT, {"font": "cour"}),
        (DefectType.FONT_SIZE, {"font_size": 18.0}),
        (DefectType.SPELLING, {"new_text": "logoX"}),
        (DefectType.NUMERICAL, {"new_text": "1"}),
        (DefectType.MISALIGNMENT, {"x": 10.0}),
        (DefectType.EMPHASIS, {"style": "bold"}),
        (DefectType.TEXT_OVERLAY, {"new_text": "COPY"}),
    ]
    for defect_type, extra in blocked:
        response = _inject(PDF_ID, image, defect_type, **extra)
        assert response.status_code == 400, (defect_type.value, response.json())
        assert "image" in response.json()["detail"].lower()


def test_image_manipulation_rejects_text_fields_on_pdf_and_png() -> None:
    for document_id in (PDF_ID, PNG_ID):
        document = get_document(document_id)
        assert document is not None
        text, _numeric, _image = _pick_fields(document)
        response = _inject(
            document_id,
            text,
            DefectType.IMAGE_MANIPULATION,
            indicator="compression_mismatch",
        )
        assert response.status_code == 400, response.json()
        assert "image" in response.json()["detail"].lower()


def test_header_fields_can_be_injected() -> None:
    document = get_document(PDF_ID)
    assert document is not None
    label = next(name for name, field in document.fields.items() if is_label_only(field.text))
    for defect_type, extra in (
        (DefectType.SPELLING, {"new_text": "Changed Header"}),
        (DefectType.FONT, {"font": "cour"}),
        (DefectType.FONT_SIZE, {"font_size": 20.0}),
        (DefectType.MISALIGNMENT, {"x": document.fields[label].x + 24.0}),
        (DefectType.EMPHASIS, {"style": "bold"}),
    ):
        response = _inject(PDF_ID, label, defect_type, **extra)
        assert response.status_code == 200, (defect_type.value, response.json())


def test_numerical_on_a_header_still_needs_digits() -> None:
    document = get_document(PDF_ID)
    assert document is not None
    label = next(name for name, field in document.fields.items() if is_label_only(field.text))
    response = _inject(PDF_ID, label, DefectType.NUMERICAL, new_text="1")
    assert response.status_code == 400
    assert "numeric" in response.json()["detail"].lower()


def test_mixed_span_keeps_the_label_prefix() -> None:
    document = get_document(PDF_ID)
    assert document is not None
    mixed = next(
        name
        for name, field in document.fields.items()
        if field.text.startswith("Account No.")
    )
    response = _inject(PDF_ID, mixed, DefectType.SPELLING, new_text="87654321")
    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["before_text"].startswith("Account No.")
    assert payload["after_text"] == "Account No. 87654321"


def test_font_inject_on_total_equity_line(isolated_injection_templates: Path) -> None:
    path = isolated_injection_templates / "equity_line.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=200)
    page.insert_text((72, 100), "TOTAL EQUITY AND LIABILITIES", fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()
    reset_sample_documents()

    document = get_document("equity_line")
    assert document is not None
    field_name = next(
        name
        for name, field in document.fields.items()
        if "equity" in field.text.lower() and "liab" in field.text.lower()
    )
    response = _inject("equity_line", field_name, DefectType.FONT, font="cour")
    assert response.status_code == 200, response.json()
    after = get_document("equity_line")
    assert after is not None
    assert after.fields[field_name].font == "cour"


def test_unknown_document_and_field_are_rejected() -> None:
    missing_doc = _inject("no_such_doc", "company", DefectType.SPELLING, new_text="X")
    assert missing_doc.status_code == 404
    missing_field = _inject(PDF_ID, "no_such_field", DefectType.SPELLING, new_text="X")
    assert missing_field.status_code == 400
    assert "not found" in missing_field.json()["detail"].lower()


def test_image_manipulation_rejects_unknown_indicator() -> None:
    document = get_document(PDF_ID)
    assert document is not None
    image = next(name for name, field in document.fields.items() if field.kind == "image")
    response = _inject(PDF_ID, image, DefectType.IMAGE_MANIPULATION, indicator="not_real")
    assert response.status_code == 400
    assert "indicator" in response.json()["detail"].lower()
