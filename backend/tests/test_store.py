"""Store fallbacks the inject API tests never hit."""

from pathlib import Path

import pymupdf
import pytest

from app.injection import store
from app.injection.store import (
    get_document,
    get_document_file_path,
    get_template_document,
    list_documents,
    reset_sample_documents,
    stage_generated_document,
)
from app.injection.schemas import FieldValue

PDF_ID = "C001_CertIncumbency"


def _tiny_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=120)
    page.insert_text((40, 60), "Store Fixture", fontsize=12, fontname="helv")
    doc.save(path)
    doc.close()
    return path


def test_normalize_id_strips_known_suffixes(isolated_injection_templates: Path) -> None:
    reset_sample_documents()
    by_pdf = get_document(f"{PDF_ID}.pdf")
    by_png = get_document("ocbc_sample.png")
    assert by_pdf is not None and by_pdf.id == PDF_ID
    assert by_png is not None and by_png.id == "ocbc_sample"


def test_reset_skips_junk_and_survives_a_bad_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    templates = tmp_path / "templates"
    generated = tmp_path / "generated"
    templates.mkdir()
    generated.mkdir()
    (templates / "nested").mkdir()
    (templates / ".hidden.pdf").write_bytes(b"%PDF-1.4")
    (templates / "notes.txt").write_text("ignore")
    (templates / "broken.pdf").write_bytes(b"not a pdf")
    _tiny_pdf(templates / "ok.pdf")

    monkeypatch.setattr(store, "TEMPLATES_DIR", templates)
    monkeypatch.setattr(store, "DOCUMENTS_GENERATED_DIR", generated)
    monkeypatch.setattr("app.injection.pdf.TEMPLATES_DIR", templates)
    monkeypatch.setattr("app.injection.pdf.DOCUMENTS_GENERATED_DIR", generated)

    reset_sample_documents()
    ids = {doc.id for doc in list_documents()}
    assert "ok" in ids
    assert "broken" not in ids
    assert "notes" not in ids
    assert "hidden" not in ids


def test_list_documents_lazy_loads_when_cache_is_empty(
    isolated_injection_templates: Path,
) -> None:
    reset_sample_documents()
    store.DOCUMENTS.clear()
    store.TEMPLATES.clear()
    docs = list_documents()
    assert any(doc.id == PDF_ID for doc in docs)


def test_get_document_registers_a_file_added_after_load(
    isolated_injection_templates: Path,
) -> None:
    reset_sample_documents()
    store.DOCUMENTS.pop(PDF_ID)
    store.TEMPLATES.pop(PDF_ID, None)
    found = get_document(PDF_ID)
    assert found is not None
    assert found.id == PDF_ID


def test_get_document_returns_none_when_late_register_fails(
    isolated_injection_templates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_sample_documents()
    store.DOCUMENTS.pop(PDF_ID)
    store.TEMPLATES.pop(PDF_ID, None)
    monkeypatch.setattr(
        store,
        "_register_file",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad template")),
    )
    assert get_document(PDF_ID) is None


def test_get_template_document_missing_and_without_cached_template(
    isolated_injection_templates: Path,
) -> None:
    reset_sample_documents()
    assert get_template_document("no_such_doc") is None
    store.TEMPLATES.pop(PDF_ID, None)
    fallback = get_template_document(PDF_ID)
    assert fallback is not None
    assert fallback.id == PDF_ID


def test_stage_generated_document_requires_a_template_file(
    isolated_injection_templates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_sample_documents()
    document = get_document(PDF_ID)
    assert document is not None
    monkeypatch.setattr(store, "find_template_path", lambda *_: None)
    with pytest.raises(FileNotFoundError, match="not found"):
        stage_generated_document(document, "spelling")


def test_get_document_file_path_fallbacks(
    isolated_injection_templates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reset_sample_documents()
    assert get_document_file_path("no_such_doc") is None

    template_path = get_document_file_path(PDF_ID)
    assert template_path is not None
    assert Path(template_path).name.startswith(PDF_ID)

    stored = store.DOCUMENTS[PDF_ID]
    stored.inject_path = "/tmp/does-not-exist.pdf"
    again = get_document_file_path(PDF_ID)
    assert again == template_path

    monkeypatch.setattr(store, "find_template_path", lambda *_: None)
    stored.inject_path = None
    assert get_document_file_path(PDF_ID) is None


def test_save_document_roundtrip(isolated_injection_templates: Path) -> None:
    reset_sample_documents()
    document = get_document(PDF_ID)
    assert document is not None
    document.fields["extra"] = FieldValue(text="added")
    saved = store.save_document(document)
    assert "extra" in saved.fields
    assert "extra" in store.DOCUMENTS[PDF_ID].fields
