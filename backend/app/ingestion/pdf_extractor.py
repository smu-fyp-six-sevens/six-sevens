import pymupdf


def extract_pdf(file_bytes: bytes):
    """
    Extract text, bounding boxes and font information
    from a digitally generated PDF.
    """

    document = pymupdf.open(
        stream=file_bytes,
        filetype="pdf"
    )

    pages = []
    total_text = []

    for page_index, page in enumerate(document):

        page_number = page_index + 1

        page_text = page.get_text("text")

        # --------------------------------
        # Extract detailed text information
        # --------------------------------

        page_dict = page.get_text("dict")

        elements = []

        for block in page_dict.get("blocks", []):

            if "lines" not in block:
                continue

            for line in block["lines"]:

                for span in line["spans"]:

                    element = {
                        "text": span["text"],
                        "bbox": span["bbox"],
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

        text = page.get_text("text")

        if text.strip():
            return True

    return False