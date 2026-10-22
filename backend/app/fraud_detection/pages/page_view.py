"""Each page of a document as the cross-page checks read it, and which transaction rows sit on which page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

import pymupdf

from ...ingestion.ingestion_service import ingest_document
from ..numerical_validation.common import _as_number, _field_number, _rows, row_amount
from ..shared.pdf_pages import pages as _pdf_pages

# Page numbers live in the top and bottom bands of a page.
PAGE_BAND_FRACTION = 0.12
# A money amount as statements print it: grouped thousands or two decimals, optional brackets or CR/DR.
MONEY_RE = re.compile(
    r"\(?-?(?:S?\$\s*)?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}\)?(?:\s*(?:CR|DR)\b)?", re.IGNORECASE
)
_SPACES_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PlacedLine:
    """A line of text and where it sits on the page (points, origin top-left)."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True)
class PageView:
    number: int                   # 1-based physical position
    lines: tuple[str, ...]        # non-empty lines in reading order, whitespace collapsed
    placed: tuple[PlacedLine, ...]  # the same lines with positions; empty for OCR text
    band_text: str                # text in the top and bottom bands, whitespace collapsed
    has_text_layer: bool          # False: an image-only page (a scan)
    size: tuple[int, int]         # width, height in points, rounded (orientation included)
    rotation: int
    image_cover: float            # share of the page covered by its largest image

    @property
    def text(self) -> str:
        return " ".join(self.lines)

    @property
    def money_count(self) -> int:
        return len(MONEY_RE.findall(self.text))


def collapse(text: str) -> str:
    return _SPACES_RE.sub(" ", text).strip()


def money(text: str) -> float | None:
    """A printed amount as a number: brackets or DR make it negative."""
    match = MONEY_RE.search(text)
    if not match:
        return None
    printed = match.group(0)
    negative = printed.strip().upper().endswith("DR")
    value = _as_number(re.sub(r"(?i)\s*(CR|DR)$", "", printed).replace("S$", "").replace("$", ""))
    if value is None:
        return None
    return -abs(value) if negative else value


def page_text(page: pymupdf.Page) -> str:
    # get_text's return type is a union over its modes; "text" is always a string.
    return cast(str, page.get_text("text"))


def text_lines(page: pymupdf.Page) -> list[dict[str, Any]]:
    """The page's text lines as PyMuPDF lays them out ("bbox", "spans" with "text", "size", "origin")."""
    layout = cast(dict, page.get_text("dict"))
    return [line for block in layout["blocks"] if block.get("type") == 0 for line in block["lines"]]


def _page_view(page: pymupdf.Page, number: int, ocr_text: str | None = None) -> PageView:
    rect = page.rect
    area = max(rect.width * rect.height, 1.0)
    native = page_text(page)
    source = ocr_text if ocr_text is not None else native
    lines = tuple(line for line in (collapse(raw) for raw in source.splitlines()) if line)
    placed: tuple[PlacedLine, ...] = ()
    if ocr_text is None:
        placed = tuple(
            PlacedLine(joined, *line["bbox"])
            for line in text_lines(page)
            if (joined := collapse("".join(span["text"] for span in line["spans"])))
        )
        band_height = rect.height * PAGE_BAND_FRACTION
        band = [
            str(block[4]) for block in cast(list, page.get_text("blocks"))
            if block[3] <= rect.y0 + band_height or block[1] >= rect.y1 - band_height
        ]
        band_text = collapse(" ".join(band))
    else:
        band_text = collapse(" ".join(lines[:3] + lines[-3:]))
    covers = [
        (info["bbox"][2] - info["bbox"][0]) * (info["bbox"][3] - info["bbox"][1]) / area
        for info in page.get_image_info()
    ]
    return PageView(
        number=number, lines=lines, placed=placed, band_text=band_text, has_text_layer=bool(native.strip()),
        size=(round(rect.width), round(rect.height)), rotation=page.rotation,
        image_cover=max(covers, default=0.0),
    )


def read_pages(file_bytes: bytes, filename: str) -> list[PageView]:
    """Every page of a PDF. A PDF with no text layer at all is read by OCR (the same ingestion the other
    checks use); a single scanned page inside a digital PDF keeps its empty text, which is itself a
    signal (see source.py)."""
    with pymupdf.open(stream=file_bytes, filetype="pdf") as doc:
        pages = list(_pdf_pages(doc))
        if any(page_text(page).strip() for page in pages):
            return [_page_view(page, index + 1) for index, page in enumerate(pages)]
        ocr = ingest_document(file_bytes, filename, "application/pdf", with_metadata=False)["pages"]
        texts = {int(p.get("page_number", 0)): str(p.get("text") or "") for p in ocr}
        return [_page_view(page, index + 1, texts.get(index + 1, "")) for index, page in enumerate(pages)]


def _money_strings(value: float) -> tuple[str, ...]:
    plain = f"{abs(value):,.2f}"
    return plain, plain.replace(",", "")


def _printed(figure: str, text: str) -> bool:
    """The figure appears in the text as a whole number, not inside a longer one."""
    return re.search(rf"(?<![\d,.]){re.escape(figure)}(?!\d)", text) is not None


@dataclass(frozen=True)
class PlacedRow:
    index: int            # position in the extracted transactions
    page: int             # 1-based page it was found on
    amount: float | None
    balance: float
    key: tuple[str, str, str]   # date, description, amount as printed: what makes two rows the same


def place_rows(pages: list[PageView], transactions: Any) -> list[PlacedRow]:
    """The extracted transaction rows, each on the page that prints both its amount and its balance (the
    balance alone isn't enough: the next page's "Balance Brought Forward" prints the last balance again).
    Rows are placed in order and never move back a page, so a figure that recurs later can't pull a row onto
    the wrong page. A row whose figures aren't printed as expected (a scanned page) stays unplaced, and its
    page boundary is skipped, not guessed."""
    placed: list[PlacedRow] = []
    cursor = 0
    for index, row in enumerate(_rows(transactions)):
        balance, amount = _field_number(row, "balance"), row_amount(row)
        if balance is None or amount is None:
            continue
        figures = (_money_strings(balance), _money_strings(amount))
        for position in range(cursor, len(pages)):
            text = pages[position].text
            if all(any(_printed(c, text) for c in strings) for strings in figures):
                placed.append(PlacedRow(
                    index=index, page=pages[position].number, amount=amount, balance=balance,
                    key=(str(row.get("date") or ""), collapse(str(row.get("description") or "")).upper(),
                         str(row.get("amount") or "")),
                ))
                cursor = position
                break
    return placed
