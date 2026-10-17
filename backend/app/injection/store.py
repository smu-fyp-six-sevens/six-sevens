from __future__ import annotations

import logging
import shutil
from copy import deepcopy
from pathlib import Path

from .pdf import (
    IMAGE_SUFFIXES,
    DOCUMENTS_GENERATED_DIR,
    TEMPLATES_DIR,
    discover_fields_from_image,
    discover_fields_from_pdf,
    ensure_data_dirs,
    find_template_path,
    is_image_template,
    read_document_metadata,
    slugify_document_id,
)
from .schemas import InjectableDocument

logger = logging.getLogger(__name__)

DOCUMENTS: dict[str, InjectableDocument] = {}
TEMPLATES: dict[str, InjectableDocument] = {}

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
    source: Path,
) -> InjectableDocument:
    if is_image_template(source):
        metadata = read_document_metadata(source)
        fields = discover_fields_from_image(source, metadata=metadata)
        field_source = "ocr"
    else:
        fields = discover_fields_from_pdf(source)
        field_source = "pdf_text"

    return InjectableDocument(
        id=document_id,
        document_type=document_type,
        filename=filename,
        fields=fields,
        defects=[],
        field_source=field_source,
    )


def _register_file(
    path: Path,
    *,
    document_id: str | None = None,
    document_type: str = "custom",
) -> InjectableDocument:
    document_id = document_id or slugify_document_id(path.stem)
    filename = f"{document_id}{path.suffix.lower()}"
    document = _build_document(
        document_id,
        document_type=document_type,
        filename=filename,
        source=path,
    )
    DOCUMENTS[document_id] = document
    TEMPLATES[document_id] = deepcopy(document)
    return document


def reset_sample_documents() -> None:
    """Load files from document_templates/ for fraud injection."""
    ensure_data_dirs()
    DOCUMENTS.clear()
    TEMPLATES.clear()

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
            logger.exception("Failed to register injection template %s", path.name)
            continue


def list_documents() -> list[InjectableDocument]:
    """Registered templates (ids are slugs, e.g. Bank_Statement-1)."""
    _ensure_loaded()
    return [deepcopy(doc) for doc in DOCUMENTS.values()]


def _ensure_loaded() -> None:
    if not DOCUMENTS:
        reset_sample_documents()


def get_document(document_id: str) -> InjectableDocument | None:
    _ensure_loaded()
    normalized = slugify_document_id(_normalize_document_id(document_id))
    document = DOCUMENTS.get(normalized)
    if document is None:
        template = find_template_path(normalized) or find_template_path(
            _normalize_document_id(document_id)
        )
        if template is None:
            return None
        try:
            _register_file(template, document_type="custom")
            document = DOCUMENTS.get(normalized) or DOCUMENTS.get(
                slugify_document_id(template.stem)
            )
        except ValueError:
            return None
    return deepcopy(document) if document else None


def get_template_document(document_id: str) -> InjectableDocument | None:
    """Clean template fields (ignores previous injects in this process)."""
    document = get_document(document_id)
    if document is None:
        return None
    template = TEMPLATES.get(document.id)
    return deepcopy(template) if template else document


def stage_generated_document(document: InjectableDocument, defect_type: str) -> Path:
    """Copy the clean template into documents_generated/ for this inject."""
    ensure_data_dirs()
    template = find_template_path(document.id) or find_template_path(document.filename)
    if template is None or not template.exists():
        raise FileNotFoundError(f"Template for '{document.id}' not found")
    suffix = Path(document.filename).suffix
    dest = DOCUMENTS_GENERATED_DIR / f"{document.id}__{defect_type}{suffix}"
    shutil.copy2(template, dest)
    document.inject_path = str(dest)
    return dest


def save_document(document: InjectableDocument) -> InjectableDocument:
    DOCUMENTS[document.id] = deepcopy(document)
    return deepcopy(document)
