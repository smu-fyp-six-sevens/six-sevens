import re
from typing import Iterable, cast

import pymupdf

# sort=True pads the gap between columns with spaces; a long run carries no
# meaning beyond "next column", and would otherwise inflate every LLM prompt.
_COLUMN_GAP_RE = re.compile(r"[ \t]{3,}")


def _page_text(page: pymupdf.Page) -> str:
    """
    Text in on-page reading order (top to bottom, left to right), not the
    order it's stored in the file. An edited PDF stores the new text at the
    end of the page, so file order strands a value away from its label and
    the text-only extraction step can't pair them.
    """
    text = cast(str, page.get_text("text", sort=True))
    return "\n".join(_COLUMN_GAP_RE.sub("   ", line).rstrip() for line in text.splitlines())


def extract_pdf(file_bytes: bytes):
    """
    Extract text and font information from a digitally generated PDF.
    """

    document = pymupdf.open(
        stream=file_bytes,
        filetype="pdf"
    )

    pages = []
    total_text = []

    for page_index, page in enumerate(cast(Iterable, document)):

        page_number = page_index + 1

        page_text = _page_text(page)

        page_dict = page.get_text("dict")

        elements = []

        for block in page_dict.get("blocks", []):

            if "lines" not in block:
                continue

            for line in block["lines"]:

                for span in line["spans"]:

                    element = {
                        "text": span["text"],
                        "font": span["font"],
                        "font_size": span["size"],
                        "flags": span["flags"],
                        "page_number": page_number,
                        "extraction_method": "native_pdf"
                    }

                    elements.append(element)

        pages.append({
            "page_number": page_number,
            "text": page_text,
            "elements": elements
        })

        total_text.append(page_text)

    return {
        "text": "\n".join(total_text),
        "pages": pages
    }


def pdf_contains_text(file_bytes: bytes) -> bool:

    document = pymupdf.open(
        stream=file_bytes,
        filetype="pdf"
    )

    for page in document:

        # get_text's return type is a union over every "mode" it supports;
        # mode="text" specifically always returns a plain string.
        text = cast(str, page.get_text("text"))

        if text.strip():
            return True

    return False