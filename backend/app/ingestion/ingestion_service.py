import mimetypes
from io import BytesIO
from typing import Iterable, cast

from PIL import Image
from ..config import settings
from .pdf_extractor import extract_pdf, pdf_contains_text
from .ocr_extractor import extract_image_with_ocr
from .metadata_extractor import extract_metadata

_OCR_ZOOM = 2


class DocumentTooLarge(ValueError):
    """A scanned document beyond what OCR is allowed to read."""


def _check_ocr_limits(document) -> None:
    pages = document.page_count
    if pages > settings.OCR_MAX_PAGES:
        raise DocumentTooLarge(
            f"The scanned PDF has {pages} pages; at most {settings.OCR_MAX_PAGES} can be read by OCR."
        )
    for index, page in enumerate(cast(Iterable, document)):
        pixels = round(page.rect.width * _OCR_ZOOM) * round(page.rect.height * _OCR_ZOOM)
        if pixels > settings.OCR_MAX_PAGE_PIXELS:
            raise DocumentTooLarge(
                f"The scanned PDF's page {index + 1} is too large to read by OCR "
                f"({pixels:,} pixels; the limit is {settings.OCR_MAX_PAGE_PIXELS:,})."
            )


def content_type_from_filename(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _needs_review(extraction_method: str, raw_text: str, pages: list) -> bool:
    if not raw_text.strip():
        return True

    if extraction_method == "ocr":
        confidences = [
            e["confidence"]
            for page in pages
            for e in page.get("elements", [])
        ]
        if confidences and (sum(confidences) / len(confidences)) < 0.5:
            return True

    return False


def ingest_document(
    file_bytes: bytes,
    file_name: str,
    content_type: str,
    *,
    with_metadata: bool = True,
):
    """Text (and OCR) of a document. with_metadata=False leaves out the exiftool run, for a caller that
    reads the metadata separately or doesn't need it."""

    # --------------------------------------
    # 1. Extract metadata
    # --------------------------------------

    metadata = extract_metadata(file_bytes, file_name) if with_metadata else {}

    # --------------------------------------
    # 2. PDF processing
    # --------------------------------------

    if content_type == "application/pdf":

        if pdf_contains_text(file_bytes):

            pdf_result = extract_pdf(
                file_bytes
            )

            extraction_method = "native_pdf"
            raw_text = pdf_result["text"]
            pages = pdf_result["pages"]

        else:

            import pymupdf

            document = pymupdf.open(
                stream=file_bytes,
                filetype="pdf"
            )
            _check_ocr_limits(document)

            pages = []
            all_text = []

            for index, page in enumerate(cast(Iterable, document)):

                pix = page.get_pixmap(
                    matrix=pymupdf.Matrix(_OCR_ZOOM, _OCR_ZOOM)
                )

                image = Image.open(
                    BytesIO(
                        pix.tobytes("png")
                    )
                )

                result = extract_image_with_ocr(
                    image,
                    index + 1
                )

                pages.append({
                    "page_number": index + 1,
                    "text": result["text"],
                    "elements": result["elements"]
                })

                all_text.append(
                    result["text"]
                )

            extraction_method = "ocr"
            raw_text = "\n".join(all_text)

    # --------------------------------------
    # 3. Image processing
    # --------------------------------------

    elif content_type.startswith("image/"):

        image = Image.open(
            BytesIO(file_bytes)
        )

        result = extract_image_with_ocr(
            image,
            page_number=1
        )

        extraction_method = "ocr"
        raw_text = result["text"]
        pages = [
            {
                "page_number": 1,
                "text": result["text"],
                "elements": result["elements"]
            }
        ]

    else:
        raise ValueError(
            f"Unsupported document type: {content_type}"
        )

    # --------------------------------------
    # 4. Build final result (shared by all branches)
    # --------------------------------------

    return {
        "file_name": file_name,
        "file_type": content_type,
        "metadata": metadata,
        "extraction_method": extraction_method,
        "raw_text": raw_text,
        "pages": pages,
        "needs_review": _needs_review(extraction_method, raw_text, pages),
        "status": "completed"
    }