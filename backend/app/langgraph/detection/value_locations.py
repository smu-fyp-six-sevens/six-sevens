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

Text the content guard quoted (hidden text, and text worded as an instruction to the AI) is found the same way,
by its words, when the guard didn't keep where it was.

A fillable PDF form keeps what was typed into it in its form fields, drawn by the viewer over the page, not in the
page's text: each field's value is written into the page (invisibly, inside the field's box) before searching, so
it is found like any other text.

A photo or a scan has no text layer: its OCR lines (app/text_lines.py) are written into a stand-in PDF, each line
stretched over its box and scaled to an A4 width so the distances above still hold, the same search runs on that,
and the boxes are scaled back to the image's pixels.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, cast

import pymupdf

from ...fraud_detection.shared.pdf_pages import pages

TOOLS = ("numerical_checks_tool", "date_consistency_tool", "external_validation_tool")
# Findings quoting text on the page that the content guard may not have placed.
QUOTE_TOOLS = ("hidden_text_tool", "prompt_injection_tool")
_QUOTE_WORDS = (12, 5)  # the quote's first words tried, longest first
_OCR_PAGE_WIDTH = 595.0  # an OCR'd page is laid out at an A4 width in points

_MAX_MATCHES = 3
_MIN_LENGTH = 4
_SAME_LINE = 6.0  # points between the centres of a value and its row's description
_PATH_PART = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_POSTAL_CODE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_ADDRESS_LINES_ABOVE = 50.0  # points: an address's first line is at most a few lines above its postal code
_NUMBER = re.compile(r"^\(?-?(?:S?\$)?\s*-?[\d,]*\.?\d+\)?$")


def needs_locations(finding: dict) -> bool:
    if "locations" in finding:
        return False
    if finding.get("tool") in TOOLS:
        return True
    # Hidden text and visible instructions to the AI are in the text; metadata, a file name or an image's text is not.
    on_page = finding.get("tool") == "hidden_text_tool" or finding.get("where") in ("hidden", "visible")
    return finding.get("tool") in QUOTE_TOOLS and on_page and not finding.get("bbox") and bool(_quote(finding))


def _quote(finding: dict) -> str:
    return " ".join(str(finding.get("quote") or finding.get("text") or "").replace("…", " ").split())


def locate_quote(doc: pymupdf.Document, finding: dict) -> dict:
    """A finding quoting text on the page, with `locations`: its first words where they are printed (on its page,
    when it names one). Empty when they aren't found, or are found too often to point at."""
    words = _quote(finding).split()
    page_number = finding.get("page")
    for count in _QUOTE_WORDS:
        if len(words) < min(count, 3):
            continue
        text = " ".join(words[:count])
        hits = [
            (index, rect)
            for index, page in enumerate(pages(doc))
            if not isinstance(page_number, int) or index + 1 == page_number
            for rect in page.search_for(text)
        ]
        if hits:
            locations = [_location(page, rect) for page, rect in hits] if len(hits) <= _MAX_MATCHES else []
            return {**finding, "bbox_units": "pt", "locations": locations}
    return {**finding, "locations": []}


def open_with_form_values(file_bytes: bytes) -> pymupdf.Document:
    """The PDF with each filled form field's value written into its page, invisibly, inside the field's box, so the
    page's text search finds what was typed into the form."""
    doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    for page in pages(doc):
        for widget in page.widgets() or []:
            value = widget.field_value
            if not isinstance(value, str) or not value.strip() or widget.field_type_string not in ("Text", "ComboBox"):
                continue
            box = widget.rect
            text = " ".join(value.split())
            # A field whose value is already drawn into the page (it has an appearance) is found as it is.
            if text.lower() in " ".join(page.get_textbox(box).split()).lower():
                continue
            size = max(min(box.height * 0.7, 11.0), 1.0)
            length = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
            if length <= 0 or box.width <= 0:
                continue
            baseline = pymupdf.Point(box.x0 + 2, box.y1 - (box.height - size) / 2 - size * 0.2)
            # Squeezed to fit a field narrower than its text; otherwise as long as it is, from the field's left edge.
            squeeze = min(1.0, (box.width - 4) / length) if box.width > 4 else 1.0
            page.insert_text(
                baseline, text, fontsize=size, fontname="helv", render_mode=3,
                morph=(baseline, pymupdf.Matrix(squeeze, 1)),
            )
    return doc


def doc_from_text_lines(text_pages: list[dict]) -> tuple[pymupdf.Document, list[float]]:
    """A stand-in PDF of OCR'd pages: each line written (invisibly) over its box, the page scaled to an A4 width.
    Returns it with each page's scale, points per pixel."""
    doc = pymupdf.open()
    scales = []
    for text_page in text_pages:
        width, height = (float(v) for v in text_page["size"])
        scale = _OCR_PAGE_WIDTH / width if width > 0 else 1.0
        page = doc.new_page(width=width * scale, height=height * scale)
        for line in text_page["lines"]:
            x0, y0, x1, y1 = (float(v) * scale for v in line["bbox"])
            size = max((y1 - y0) * 0.85, 1.0)
            length = pymupdf.get_text_length(line["text"], fontname="helv", fontsize=size)
            if length <= 0 or x1 <= x0:
                continue
            baseline = pymupdf.Point(x0, y1 - (y1 - y0) * 0.22)
            page.insert_text(
                baseline, line["text"], fontsize=size, fontname="helv", render_mode=3,
                morph=(baseline, pymupdf.Matrix((x1 - x0) / length, 1)),
            )
        scales.append(scale)
    return doc, scales


def in_image_units(finding: dict, text_pages: list[dict], scales: list[float]) -> dict:
    """A finding placed on the stand-in PDF, with its boxes back in the OCR'd image's pixels."""
    locations = finding.get("locations") or []
    if not locations:
        return finding
    first = text_pages[locations[0]["page"] - 1]
    return {
        **finding,
        "bbox_units": first["units"],
        "page_size": list(first["size"]),
        "locations": [
            {**loc, "bbox": [round(v / scales[loc["page"] - 1], 1) for v in loc["bbox"]]} for loc in locations
        ],
    }


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
    """A number that is zero ("0", "$0.00", "(0.00)"): too common to point at unless beside its label. Text is not."""
    return _NUMBER.match(text.strip()) is not None and not re.sub(r"[^1-9]", "", text)


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
