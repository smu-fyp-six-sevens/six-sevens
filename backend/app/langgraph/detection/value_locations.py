"""
Where a numerical or date finding's value is printed, so the preview can box it.

These checks work on the extracted fields, not the page, so their findings name a field
(`net_pay`, `transactions[3].balance`) and the value it holds, but not where it is. This finds that
value in the PDF's text, trying the forms it is likely printed in: the extracted value as read from
the page, the finding's `stated` value, and the usual ways of printing a number or a date. A value
in a list row (a transaction) must sit on the same line as that row's description; any other value
is preferred on the same line as its field's label, and a zero only counts there. A finding about a
whole list ("transaction 24 repeats transaction 13") boxes the rows its summary names. An address,
printed over several lines, is found by its postal code and boxed up to its first words. A value that
still matches in more than a few places is too common to point at, so it gets no box.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, cast

import pymupdf

from ...fraud_detection.shared.pdf_pages import pages

TOOLS = ("numerical_checks_tool", "date_consistency_tool", "external_validation_tool")

_MAX_MATCHES = 3
_MIN_LENGTH = 4
_SAME_LINE = 6.0  # points between the centres of a value and its row's description
_PATH_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_POSTAL_CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_ADDRESS_LINES_ABOVE = 50.0  # points: an address's first line is at most a few lines above its postal code
_NUMBER = re.compile(r"^\(?-?(?:S?\$)?\s*-?[\d,]*\.?\d+\)?$")


def needs_locations(finding: dict) -> bool:
    return finding.get("tool") in TOOLS and "locations" not in finding


def locate(doc: pymupdf.Document, finding: dict, fields: dict) -> dict:
    """The finding with `locations` (empty when its value isn't found, or is found too often)."""
    path = _parse_path(str(finding.get("field") or ""))
    extracted, row = _resolve(fields, path)
    if isinstance(extracted, list) and path and isinstance(path[-1], str):
        rows = _named_rows(str(finding.get("summary") or ""), path[-1], extracted)
        if rows:
            locations = [area for named in rows if (area := _row_area(doc, named))]
            return {**finding, "bbox_units": "pt", "locations": locations}
    for value in (extracted, finding.get("stated")):
        if isinstance(value, str) and (areas := _address_areas(doc, value)):
            locations = areas if len(areas) <= _MAX_MATCHES else []
            return {**finding, "bbox_units": "pt", "locations": locations}
    row_anchor = _row_anchor(row)
    label = None if row else _label(path)
    for candidate in _candidates(extracted, finding.get("stated")):
        hits = _hits(doc, candidate)
        if row_anchor:
            hits = _on_line_of(doc, hits, row_anchor)
        elif hits:
            beside = _on_line_of(doc, hits, label) if label else []
            hits = beside or ([] if _is_zero(candidate) else hits)
        if hits:
            locations = [_location(page, rect) for page, rect in hits] if len(hits) <= _MAX_MATCHES else []
            return {**finding, "bbox_units": "pt", "locations": locations}
    return {**finding, "locations": []}


def _address_areas(doc: pymupdf.Document, address: str) -> list[dict]:
    """Each place a Singapore address is printed: its postal code, together with its first words when
    they are a line or two above or on the same line. Empty when the text isn't an address."""
    codes = _POSTAL_CODE.findall(address)
    words = address.replace(",", " ").split()
    if not codes or len(words) < 3:
        return []
    start = " ".join(address.split()[:2]).rstrip(",")
    areas = []
    for page_index, code in _hits(doc, codes[-1]):
        page = doc[page_index]
        area = pymupdf.Rect(code)
        for rect in page.search_for(start):
            if code.y1 - _ADDRESS_LINES_ABOVE <= rect.y0 <= code.y1:
                area |= rect
                break
        # The whole of each line in between, but not text further along them (another column).
        for line in _lines(page):
            if area.y0 - 1 <= line.y0 and line.y1 <= area.y1 + 1 and abs(line.x0 - area.x0) <= 3:
                area |= line
        areas.append(_location(page_index, area))
    return areas


def _lines(page: pymupdf.Page) -> list[pymupdf.Rect]:
    return [
        pymupdf.Rect(line["bbox"])
        for block in cast(dict, page.get_text("dict")).get("blocks", [])
        for line in block.get("lines", [])
    ]


def _label(path: list[str | int]) -> str | None:
    names = [part for part in path if isinstance(part, str)]
    return names[-1].replace("_", " ") if names else None


def _named_rows(summary: str, list_name: str, rows: list) -> list[dict]:
    """The rows of a list its summary names by number ("transaction 24", "account 1"), 1-based."""
    noun = list_name.rstrip("s")
    numbers = re.findall(rf"\b{re.escape(noun)}s?\s+(\d+)", summary, flags=re.IGNORECASE)
    named = [rows[int(n) - 1] for n in dict.fromkeys(numbers) if 0 < int(n) <= len(rows)]
    return [row for row in named if isinstance(row, dict)]


def _row_area(doc: pymupdf.Document, row: dict) -> dict | None:
    """A list row's line: its description together with its values printed beside it. A description
    that repeats (a monthly rental) is told apart by which copy has the row's values on its line."""
    anchor = _row_anchor(row)
    if not anchor:
        return None
    values = [candidate for key, value in row.items() if key != "description" for candidate in _candidates(value, None)]
    best: tuple[int, int, pymupdf.Rect] | None = None
    tied = False
    for page_index, page in enumerate(pages(doc)):
        for start in page.search_for(anchor):
            area = pymupdf.Rect(start)
            beside = 0
            for candidate in values:
                for rect in page.search_for(candidate):
                    if abs(_mid(rect) - _mid(start)) <= _SAME_LINE and _whole(page, rect, candidate):
                        area |= rect
                        beside += 1
                        break
            if best is None or beside > best[0]:
                best, tied = (beside, page_index, area), False
            elif beside == best[0]:
                tied = True
    if best is None or (tied and best[0] == 0):
        return None
    return _location(best[1], best[2])


def _parse_path(field: str) -> list[str | int]:
    return [name if name else int(index) for name, index in _PATH_PART.findall(field)]


def _resolve(fields: dict, path: list[str | int]) -> tuple[Any, dict | None]:
    """The value at the field path, and the list row it sits in (if any)."""
    value: Any = fields
    row = None
    for part in path:
        if isinstance(part, int):
            if not isinstance(value, list) or part >= len(value):
                return None, None
            value = value[part]
            row = value if isinstance(value, dict) else None
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None, None
    return value, row


def _row_anchor(row: dict | None) -> str | None:
    if not row:
        return None
    for key in ("description", "name", "account_name", "product", "client"):
        text = row.get(key)
        if isinstance(text, str) and len(text.split()) >= 1 and len(text) >= _MIN_LENGTH:
            # Its first words: a long description wraps or is cut short.
            return " ".join(text.split()[:3])
    return None


def _candidates(extracted: Any, stated: Any) -> list[str]:
    raw = [v for v in (extracted, stated) if isinstance(v, (str, int, float)) and not isinstance(v, bool)]
    found: list[str] = []
    for value in raw:
        text = " ".join(str(value).split())
        found.append(text)
        found.extend(_number_forms(value))
        found.extend(_date_forms(text))
    unique = list(dict.fromkeys(found))
    return [c for c in unique if len(c) >= _MIN_LENGTH]


def _number_forms(value: Any) -> list[str]:
    if isinstance(value, str):
        if not _NUMBER.match(value.strip()):
            return []
        try:
            value = float(re.sub(r"[^\d.\-]", "", value))
        except ValueError:
            return []
    if not isinstance(value, (int, float)):
        return []
    # A debit can be printed without its sign, or in brackets.
    magnitude = abs(float(value))
    forms = [f"{magnitude:,.2f}", f"{magnitude:.2f}"]
    if magnitude.is_integer():
        forms.append(f"{int(magnitude):,}")
    return forms


def _date_forms(text: str) -> list[str]:
    match = _ISO_DATE.match(text)
    if not match:
        return []
    try:
        day = date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return []
    d, m, y = day.day, day.month, day.year
    month, mon = day.strftime("%B"), day.strftime("%b")
    return [
        f"{d:02d}/{m:02d}/{y}", f"{d:02d}-{m:02d}-{y}", f"{d:02d}.{m:02d}.{y}",
        f"{d} {month} {y}", f"{d:02d} {month} {y}", f"{d} {mon} {y}", f"{d:02d} {mon} {y}",
        f"{d:02d}-{mon}-{y % 100:02d}", f"{d:02d}-{mon}-{y}", f"{month} {d}, {y}",
        f"{d}/{m}/{y}",
    ]


def _is_zero(text: str) -> bool:
    return not re.sub(r"[^1-9]", "", text)


def _hits(doc: pymupdf.Document, candidate: str) -> list[tuple[int, pymupdf.Rect]]:
    return [
        (page_index, rect)
        for page_index, page in enumerate(pages(doc))
        for rect in page.search_for(candidate)
        if _whole(page, rect, candidate)
    ]


def _on_line_of(
    doc: pymupdf.Document, hits: list[tuple[int, pymupdf.Rect]], text: str
) -> list[tuple[int, pymupdf.Rect]]:
    """The hits on the same line as the text (a row's description, a field's label)."""
    anchors: dict[int, list[pymupdf.Rect]] = {}
    kept = []
    for page_index, rect in hits:
        if page_index not in anchors:
            anchors[page_index] = doc[page_index].search_for(text)
        if any(abs(_mid(rect) - _mid(a)) <= _SAME_LINE for a in anchors[page_index]):
            kept.append((page_index, rect))
    return kept


def _location(page_index: int, rect: pymupdf.Rect) -> dict:
    return {"page": page_index + 1, "bbox": [round(v, 2) for v in (rect.x0, rect.y0, rect.x1, rect.y1)]}


def _whole(page: pymupdf.Page, rect: pymupdf.Rect, candidate: str) -> bool:
    """Not part of a longer number (4,095.77 inside 14,095.77)."""
    around = page.get_textbox(pymupdf.Rect(rect.x0 - 8, rect.y0, rect.x1 + 8, rect.y1))
    pattern = r"(?<![\w.,/-])" + re.escape(candidate) + r"(?![\w/-]|[.,]\d)"
    return re.search(pattern, " ".join(around.split())) is not None


def _mid(rect: pymupdf.Rect) -> float:
    return (rect.y0 + rect.y1) / 2
