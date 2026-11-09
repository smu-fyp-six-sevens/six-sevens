"""
Where each line of a photo's or a scan's text sits on its pages, so a value can be boxed on it: the findings that
name only a field and a value (numerical, date, registry) and a cross-document finding's values in the compare view
are placed through these lines (value_locations), since extracted fields carry no position and the file has no text
layer to search.

A text PDF is read directly (positions in points); a scanned PDF or an image is read with OCR (positions in pixels of
the image the OCR read). Every page carries its own size, so a box is placed as fractions of the page whatever its
units. Worked out on first request and cached as JSON beside the documents in storage; the cache is removed with the
document (document_repository) and rebuilt if the document's file changes.
"""

from __future__ import annotations

import json
import logging
from io import BytesIO
from typing import Any, Iterable, cast

from PIL import Image, ImageOps

from . import storage_service
from .document_repository import text_lines_cache_path
from .ingestion.ingestion_service import content_type_from_filename
from .ingestion.ocr_extractor import extract_image_with_ocr
from .ingestion.pdf_extractor import pdf_contains_text
from .models import DocumentRecord

logger = logging.getLogger(__name__)

# Bump when the shape or the extraction changes, so stale caches are rebuilt.
VERSION = 1
# A scanned PDF page is rendered at this zoom before OCR (as at ingest).
_SCAN_ZOOM = 2


def cache_path(document_id: str) -> str:
    return text_lines_cache_path(document_id)


def _line(text: str, bbox: Iterable[float]) -> dict[str, Any] | None:
    text = " ".join(text.split())
    box = [round(float(v), 1) for v in bbox]
    if not text or len(box) != 4 or box[2] <= box[0] or box[3] <= box[1]:
        return None
    return {"text": text, "bbox": box}


def _ocr_page(image: Image.Image, page: int) -> dict[str, Any]:
    result = extract_image_with_ocr(image, page)
    lines = [line for e in result["elements"] if e.get("bbox") and (line := _line(e["text"], e["bbox"]))]
    return {"page": page, "units": "px", "size": [image.width, image.height], "lines": lines}


def extract_text_lines(file_bytes: bytes, content_type: str) -> list[dict[str, Any]]:
    """Each page: its number (1-based), units, size and lines ({text, bbox: [x0, y0, x1, y1] from the top-left})."""
    if content_type == "application/pdf":
        import pymupdf

        document = pymupdf.open(stream=file_bytes, filetype="pdf")
        pages: list[dict[str, Any]] = []
        scanned = not pdf_contains_text(file_bytes)
        for index, page in enumerate(cast(Iterable, document)):
            if scanned:
                pix = page.get_pixmap(matrix=pymupdf.Matrix(_SCAN_ZOOM, _SCAN_ZOOM))
                pages.append(_ocr_page(Image.open(BytesIO(pix.tobytes("png"))), index + 1))
                continue
            lines = []
            for block in page.get_text("dict")["blocks"]:
                for raw in block.get("lines", []):
                    line = _line("".join(span["text"] for span in raw["spans"]), raw["bbox"])
                    if line:
                        lines.append(line)
            pages.append({"page": index + 1, "units": "pt", "size": [page.rect.width, page.rect.height], "lines": lines})
        return pages
    if content_type.startswith("image/"):
        # Turned the way the browser shows it (by its EXIF orientation), so the boxes land where the officer looks.
        image = ImageOps.exif_transpose(Image.open(BytesIO(file_bytes))).convert("RGB")
        return [_ocr_page(image, 1)]
    raise ValueError(f"Unsupported document type: {content_type}")


def _cached(document: DocumentRecord) -> dict[str, Any] | None:
    try:
        cached = json.loads(storage_service.download_file(cache_path(document.id)))
    except Exception:
        return None
    if cached.get("version") == VERSION and cached.get("storage_path") == document.storage_path:
        return cached
    return None


def get_or_build(document: DocumentRecord) -> dict[str, Any]:
    """The document's text lines, from the cache or worked out now (and cached). Blocking: run it in a thread."""
    cached = _cached(document)
    if cached is not None:
        return {"pages": cached["pages"]}
    assert document.storage_path is not None
    file_bytes = storage_service.download_file(document.storage_path)
    pages = extract_text_lines(file_bytes, content_type_from_filename(document.filename))
    try:
        storage_service.save_json(
            cache_path(document.id), {"version": VERSION, "storage_path": document.storage_path, "pages": pages}
        )
    except Exception:
        logger.exception("Failed to cache the text lines of document %s", document.id)
    return {"pages": pages}
