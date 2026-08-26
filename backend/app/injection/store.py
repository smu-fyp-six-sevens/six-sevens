from __future__ import annotations

import shutil
from copy import deepcopy
from pathlib import Path

from .pdf import (
    IMAGE_SUFFIXES,
    TEMPLATES_DIR,
    WORKING_DIR,
    discover_fields_from_image,
    discover_fields_from_pdf,
    ensure_data_dirs,
    find_template_path,
    is_image_template,
    read_document_metadata,
    working_file_path,
)
from .schemas import InjectableDocument

DOCUMENTS: dict[str, InjectableDocument] = {}

_IMAGE_EXTS = tuple(IMAGE_SUFFIXES) + (".pdf",)


def _normalize_document_id(document_id: str) -> str:
    """Accept 'ocbc_sample', 'ocbc_sample.png', or 'C001_CertIncumbency.pdf'."""
    name = document_id.strip()
    lowered = name.lower()
    for suffix in _IMAGE_EXTS:
        if lowered.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _build_document(
    document_id: str,
    *,
    document_type: str,
    filename: str,
    field_source: str = "pdf_text",
) -> InjectableDocument:
    working_path = working_file_path(filename)
    if is_image_template(working_path):
        metadata = read_document_metadata(working_path)
        fields = discover_fields_from_image(working_path, metadata=metadata)
        field_source = "ocr"
    else:
        fields = discover_fields_from_pdf(working_path)
        field_source = "pdf_text"

    return InjectableDocument(
        id=document_id,
        document_type=document_type,
        filename=filename,
        fields=fields,
        defects=[],
        field_source=field_source,
    )


def _clear_stale_working_files(document_id: str, keep_filename: str) -> None:
    keep_path = working_file_path(keep_filename).resolve()
    for path in WORKING_DIR.glob(f"{document_id}.*"):
        if path.resolve() == keep_path:
            continue
        if path.suffix.lower() in IMAGE_SUFFIXES or path.suffix.lower() == ".pdf":
            path.unlink(missing_ok=True)
        if path.name.endswith(".meta.json"):
            path.unlink(missing_ok=True)


def _register_file(
    path: Path,
    *,
    document_id: str | None = None,
    document_type: str = "custom",
) -> InjectableDocument:
    document_id = document_id or path.stem
    filename = path.name if path.stem == document_id else f"{document_id}{path.suffix.lower()}"
    if path.stem == document_id:
        filename = path.name

    _clear_stale_working_files(document_id, filename)
    dest = working_file_path(filename)
    if path.resolve() != dest.resolve():
        shutil.copy2(path, dest)

    DOCUMENTS[document_id] = _build_document(
        document_id,
        document_type=document_type,
        filename=filename,
    )
    return DOCUMENTS[document_id]


def reset_sample_documents() -> None:
    """Load files from data/templates/ for fraud injection."""
    ensure_data_dirs()
    DOCUMENTS.clear()

    for path in sorted(TEMPLATES_DIR.iterdir()):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if not (is_image_template(path) or path.suffix.lower() == ".pdf"):
            continue
        try:
            _register_file(path, document_type="custom")
        except Exception:
            for leftover in WORKING_DIR.glob(f"{path.stem}.*"):
                leftover.unlink(missing_ok=True)
            continue


def _ensure_loaded() -> None:
    if not DOCUMENTS:
        reset_sample_documents()


def get_document(document_id: str) -> InjectableDocument | None:
    _ensure_loaded()
    normalized = _normalize_document_id(document_id)
    document = DOCUMENTS.get(normalized)
    if document is None:
        template = find_template_path(normalized)
        if template is None:
            matches = list(TEMPLATES_DIR.glob(f"{normalized}.*"))
            template = matches[0] if matches else None
        if template is None:
            return None
        try:
            _register_file(template, document_type="custom")
            document = DOCUMENTS.get(normalized)
        except ValueError:
            return None
    return deepcopy(document) if document else None


def save_document(document: InjectableDocument) -> InjectableDocument:
    DOCUMENTS[document.id] = deepcopy(document)
    return deepcopy(document)


def get_document_file_path(document_id: str) -> str | None:
    _ensure_loaded()
    normalized = _normalize_document_id(document_id)
    document = DOCUMENTS.get(normalized)
    if document is None:
        document = get_document(normalized)
        if document is None:
            return None
    path = working_file_path(document.filename)
    if not path.exists():
        return None
    return str(path)


get_document_pdf_path = get_document_file_path
SAMPLE_DOCUMENTS = DOCUMENTS
