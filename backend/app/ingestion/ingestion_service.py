import mimetypes
from io import BytesIO
from typing import Iterable, cast

from PIL import Image
from .pdf_extractor import extract_pdf, pdf_contains_text
from .ocr_extractor import extract_image_with_ocr
from .metadata_extractor import extract_metadata

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
    content_type: str
):

    # --------------------------------------
    # 1. Extract metadata
    # --------------------------------------

    metadata = extract_metadata(
        file_bytes,
        file_name
    )

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

            pages = []
            all_text = []

            for index, page in enumerate(cast(Iterable, document)):

                pix = page.get_pixmap(
                    matrix=pymupdf.Matrix(2, 2)
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