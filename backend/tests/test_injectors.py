"""Injector validation the HTTP layer never reaches (it 422s first)."""

from pathlib import Path

import pytest

from app.injection.injectors import (
    EmphasisInjector,
    FontSizeInjector,
    ImageManipulationInjector,
    MetadataInjector,
    MisalignmentInjector,
    _clean_text,
    _replacement_text,
    _require_text,
    _working_path,
)
from app.injection.schemas import FieldValue, InjectOverrides, InjectableDocument


def _doc(
    fields: dict[str, FieldValue],
    *,
    inject_path: str | None = "/tmp/doc.pdf",
) -> InjectableDocument:
    return InjectableDocument(
        id="doc",
        document_type="pdf",
        filename="doc.pdf",
        fields=fields,
        inject_path=inject_path,
    )


def _text(**kwargs) -> FieldValue:
    values = dict(text="HELLO", font="helv", font_size=12.0, x=10.0, y=20.0, rect=[10, 10, 80, 30])
    values.update(kwargs)
    return FieldValue(**values)


@pytest.fixture
def _noop_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.injection.injectors.replace_field_text", lambda *a, **k: None)
    monkeypatch.setattr("app.injection.injectors.apply_text_emphasis", lambda *a, **k: None)
    monkeypatch.setattr("app.injection.injectors.manipulate_field_region", lambda *a, **k: None)
    monkeypatch.setattr("app.injection.injectors.set_document_metadata", lambda *a, **k: None)


def test_clean_and_require_text_helpers() -> None:
    assert _clean_text(None) is None
    assert _clean_text("  ") is None
    assert _clean_text("string") is None
    assert _clean_text(" Courier ") == "Courier"
    with pytest.raises(ValueError, match="font is required"):
        _require_text(None, "font")
    with pytest.raises(ValueError, match="must not be empty"):
        _replacement_text(_text(), "  ")
    assert _replacement_text(_text(text="Account No. 111"), "222") == "Account No. 222"
    assert _replacement_text(_text(text="HELLO"), "HELLOX") == "HELLOX"


def test_working_path_requires_inject_path() -> None:
    with pytest.raises(ValueError, match="inject_path"):
        _working_path(_doc({"name": _text()}, inject_path=None))
    assert _working_path(_doc({"name": _text()})) == Path("/tmp/doc.pdf")


def test_font_size_requires_a_value(_noop_pdf) -> None:
    with pytest.raises(ValueError, match="font_size is required"):
        FontSizeInjector().inject(_doc({"name": _text()}), "name", InjectOverrides())


def test_metadata_rejects_empty_and_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    current = {"author": "QA"}
    monkeypatch.setattr(
        "app.injection.injectors.read_document_metadata", lambda path: dict(current)
    )
    monkeypatch.setattr("app.injection.injectors.set_document_metadata", lambda *a, **k: None)
    field = _text(metadata=dict(current))
    document = _doc({"name": field})
    with pytest.raises(ValueError, match="metadata is required"):
        MetadataInjector().inject(document, "name", InjectOverrides())
    with pytest.raises(ValueError, match="matches the current metadata"):
        MetadataInjector().inject(
            document, "name", InjectOverrides(metadata={"author": "QA"})
        )


def test_misalignment_y_only_and_missing_coords(_noop_pdf) -> None:
    field = _text()
    document = _doc({"name": field})
    with pytest.raises(ValueError, match="requires x and/or y"):
        MisalignmentInjector().inject(document, "name", InjectOverrides())
    result = MisalignmentInjector().inject(document, "name", InjectOverrides(y=40.0))
    assert "y " in result.description
    assert field.y == 40.0


def test_emphasis_rejects_the_current_style(_noop_pdf) -> None:
    field = _text(style="bold")
    with pytest.raises(ValueError, match="matches the current style"):
        EmphasisInjector().inject(
            _doc({"name": field}), "name", InjectOverrides(style="bold")
        )


def test_image_manipulation_requires_an_image_box(_noop_pdf) -> None:
    text_only = _doc({"name": _text()})
    with pytest.raises(ValueError, match="no image regions"):
        ImageManipulationInjector().inject(
            text_only, "name", InjectOverrides(indicator="compression_mismatch")
        )
    image = _text(kind="image", rect=[])
    with pytest.raises(ValueError, match="bounding box"):
        ImageManipulationInjector().inject(
            _doc({"logo": image}), "logo", InjectOverrides(indicator="visible_boundary")
        )
