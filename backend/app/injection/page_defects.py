"""Page-level defects, for documents that test the cross-page checks (fraud_detection/pages).

The field injectors (injectors.py) change one value on a page. These change the pages themselves: delete
one, copy one, swap one in from another document, or rewrite a carry line's amount. Each takes and returns
PDF bytes; page indices are 0-based.
"""

from __future__ import annotations

import re

import pymupdf

from ..fraud_detection.pages.page_view import text_lines
from ..fraud_detection.shared.pdf_pages import page as _page


def _open(pdf: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=pdf, filetype="pdf")


def _save(doc: pymupdf.Document) -> bytes:
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data


def delete_page(pdf: bytes, index: int) -> bytes:
    """The document without one page, numbering and balances left as printed."""
    doc = _open(pdf)
    doc.delete_page(index)
    return _save(doc)


def duplicate_page(pdf: bytes, index: int) -> bytes:
    """A copy of one page inserted straight after it."""
    doc = _open(pdf)
    doc.fullcopy_page(index, index + 1)
    return _save(doc)


def swap_page(pdf: bytes, index: int, donor: bytes, donor_index: int) -> bytes:
    """One page replaced by a page from another document (another month's or customer's statement)."""
    doc, other = _open(pdf), _open(donor)
    doc.delete_page(index)
    doc.insert_pdf(other, from_page=donor_index, to_page=donor_index, start_at=index)
    other.close()
    return _save(doc)


_CARRY_LABELS = {"brought": r"brought\s+forward|b\s*/\s*f", "carried": r"carried\s+forward|c\s*/\s*f"}
_AMOUNT_RE = re.compile(r"\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\)?")


def edit_carry_line(pdf: bytes, index: int, kind: str, new_amount: str) -> bytes:
    """The amount on a page's "Balance Brought/Carried Forward" line printed as new_amount instead, the rest
    of the page untouched. kind is "brought" or "carried"."""
    doc = _open(pdf)
    page = _page(doc, index)
    label = re.compile(_CARRY_LABELS[kind], re.IGNORECASE)
    lines = [(line, "".join(span["text"] for span in line["spans"])) for line in text_lines(page)]
    label_line = next((line for line, text in lines if label.search(text)), None)
    if label_line is None:
        doc.close()
        raise ValueError(f"No {kind}-forward line on page {index + 1}")
    # The amount is on the label's line, or the nearest amount to its right on the same row.
    y = (label_line["bbox"][1] + label_line["bbox"][3]) / 2
    candidates = [
        (span, line) for line, text in lines for span in line["spans"]
        if _AMOUNT_RE.fullmatch(span["text"].strip()) and line["bbox"][1] <= y <= line["bbox"][3]
        and line["bbox"][0] >= label_line["bbox"][0]
    ]
    if not candidates:
        doc.close()
        raise ValueError(f"No amount beside the {kind}-forward line on page {index + 1}")
    span, _ = min(candidates, key=lambda c: c[1]["bbox"][0])
    rect = pymupdf.Rect(span["bbox"])
    page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()
    width = pymupdf.get_text_length(new_amount, fontname="helv", fontsize=span["size"])
    # Right-aligned where the old amount ended, as statement columns are.
    page.insert_text((rect.x1 - width, span["origin"][1]), new_amount, fontname="helv", fontsize=span["size"])
    return _save(doc)
