from __future__ import annotations

from typing import cast

import pymupdf


def page(doc: pymupdf.Document, index: int) -> pymupdf.Page:
    """pyright can't resolve Document.__getitem__'s overloads to Page, so cast once here."""
    return cast(pymupdf.Page, doc[index])


def pages(doc: pymupdf.Document) -> list[pymupdf.Page]:
    return [page(doc, i) for i in range(len(doc))]
