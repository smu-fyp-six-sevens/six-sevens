"""Text extraction (native text layer or OCR) into blocks, with header, table, signature, heading, and cover roles."""

from __future__ import annotations

import re
from statistics import median
from typing import Iterable, Literal, cast

import pymupdf

from .common import (
    ALIGN_EDGE_TOLERANCE_PT, _as_bbox, BBox, _Block, _by_page, _CLAUSE_HEADING_RE,
    _dominant, FOOTER_RATIO, HEADER_RATIO, _Line, logger, MIN_BLOCK_CHARS, PageMargins,
    ParagraphType, _SIGNATURE_RE, _SIGNATURE_ZONE_RATIO, _TITLE_PAGE_SHORT_FRACTION,
    _TITLE_PAGE_SHORT_RATIO, _union_bbox,
)
from ..shared.pdf_pages import page as _page, pages as _pages


def _open_document(file_bytes: bytes) -> pymupdf.Document:
    """Open a PDF or a photo. The layout rules do not depend on the container."""
    if file_bytes.startswith(b"\x89PNG"):
        filetype = "png"
    elif file_bytes.startswith(b"\xff\xd8\xff"):
        filetype = "jpeg"
    elif file_bytes.startswith((b"II*\x00", b"MM\x00*")):
        filetype = "tiff"
    elif file_bytes.startswith(b"GIF8"):
        filetype = "gif"
    else:
        filetype = "pdf"
    return pymupdf.open(stream=file_bytes, filetype=filetype)



def _needs_ocr(doc: pymupdf.Document, file_bytes: bytes) -> bool:
    """A photo, or a page whose picture covers most of the sheet, has no text layer."""
    if not file_bytes.startswith(b"%PDF"):
        return True
    for page in _pages(doc):
        # pymupdf's get_text() stub is overloaded on the (non-literal-typed)
        # `option` string, so pyright can't narrow the "text" mode's return
        # to str — it's always a str at runtime for this mode.
        text = cast(str, page.get_text("text"))
        if text.strip():
            continue
        for info in page.get_image_info() or []:
            bbox = info.get("bbox")
            if not bbox:
                continue
            width = float(bbox[2]) - float(bbox[0])
            height = float(bbox[3]) - float(bbox[1])
            if width >= page.rect.width * 0.5 and height >= page.rect.height * 0.5:
                return True
    return False


# Measured on the NRIC and passport photos: every printed line reads at 0.96
# or higher; the crest's tiny motto, misread, scores 0.67.
_OCR_MIN_CONFIDENCE = 0.80


def _ocr_blocks(doc: pymupdf.Document) -> list[_Block]:
    """Word boxes from OCR, in page coordinates, for the same layout checks."""
    try:
        import numpy as np
        from rapidocr import RapidOCR
    except ImportError:
        logger.warning("OCR is unavailable, so an image-only document cannot be assessed")
        return []

    engine = RapidOCR()
    blocks: list[_Block] = []
    for page_index in range(len(doc)):
        page = _page(doc, page_index)
        pix = page.get_pixmap(alpha=False)
        if pix.n != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        try:
            result = engine(image)
        except Exception:
            logger.exception("OCR failed on page %s", page_index + 1)
            continue
        boxes = getattr(result, "boxes", None)
        texts = getattr(result, "txts", None)
        if boxes is None or texts is None:
            continue
        scores = getattr(result, "scores", None) or [1.0] * len(texts)
        scale_x = float(page.rect.width) / float(pix.width) if pix.width else 1.0
        scale_y = float(page.rect.height) / float(pix.height) if pix.height else 1.0
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        for box, raw_text, score in zip(boxes, texts, scores):
            # Text OCR is unsure of is usually artwork (a crest motto, a
            # guilloche pattern), not a line printed on the document.
            if score is not None and float(score) < _OCR_MIN_CONFIDENCE:
                continue
            text = (raw_text or "").strip()
            if not text or box is None:
                continue
            xs = [float(point[0]) * scale_x for point in box]
            ys = [float(point[1]) * scale_y for point in box]
            bbox = (min(xs), min(ys), max(xs), max(ys))
            if bbox[2] - bbox[0] < 1 or bbox[3] - bbox[1] < 1:
                continue
            line = _Line(
                text=text,
                bbox=bbox,
                page=page_index,
                page_width=page_width,
                page_height=page_height,
                size=max(8.0, (bbox[3] - bbox[1]) * 0.8),
                ocr=True,
            )
            blocks.append(
                _Block(
                    lines=[line],
                    bbox=bbox,
                    page=page_index,
                    page_width=page_width,
                    page_height=page_height,
                    text=text,
                    role=_position_band(bbox, page_height),
                    size=line.size,
                )
            )
    _apply_heading_and_cover_roles(blocks)
    _mark_repeating_chrome(blocks)
    return blocks


def _runs_vertically(direction: object) -> bool | None:
    """Whether a text-layer line runs up or down the page, from PyMuPDF's (cos, sin) line direction; None if absent."""
    if not isinstance(direction, (tuple, list)) or len(direction) != 2:
        return None
    try:
        return abs(float(direction[1])) > abs(float(direction[0]))
    except (TypeError, ValueError):
        return None


def _extract_blocks(doc: pymupdf.Document) -> list[_Block]:
    blocks: list[_Block] = []
    table_rects_by_page: dict[int, list[pymupdf.Rect]] = {}
    # pymupdf.Document is iterable at runtime (and duck-typed test doubles
    # only implement __iter__) even though pyright's enumerate() overload
    # resolution doesn't recognise it as Iterable — cast documents that.
    for page_index, page in enumerate(cast(Iterable, doc)):
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        table_rects_by_page[page_index] = _table_rects(page)
        for raw_block in page.get_text("dict").get("blocks", []):
            if raw_block.get("type") != 0:
                continue
            lines: list[_Line] = []
            for raw_line in raw_block.get("lines", []):
                bbox = _as_bbox(raw_line.get("bbox"))
                if bbox is None:
                    continue
                text, font, size, baseline = _line_text_and_style(raw_line)
                if not text:
                    continue
                lines.append(
                    _Line(
                        text=text,
                        bbox=bbox,
                        page=page_index,
                        page_width=page_width,
                        page_height=page_height,
                        font=font,
                        size=size,
                        baseline=baseline,
                        vertical=_runs_vertically(raw_line.get("dir")),
                    )
                )
            if not lines:
                continue
            block_bbox = _as_bbox(raw_block.get("bbox")) or _union_bbox(line.bbox for line in lines)
            text = " ".join(line.text for line in lines)
            fonts = [line.font for line in lines if line.font]
            sizes = [line.size for line in lines if line.size is not None]
            blocks.append(
                _Block(
                    lines=lines,
                    bbox=block_bbox,
                    page=page_index,
                    page_width=page_width,
                    page_height=page_height,
                    text=text,
                    role=_position_band(block_bbox, page_height),
                    font=_dominant(fonts) if fonts else None,
                    size=float(median(sizes)) if sizes else None,
                )
            )
    _apply_table_roles(blocks, table_rects_by_page)
    _apply_signature_roles(blocks)
    blocks = _merge_adjacent_blocks(blocks)
    blocks = _split_leading_headings(blocks)
    _apply_heading_and_cover_roles(blocks)
    _mark_repeating_chrome(blocks)
    return blocks


def _line_text_and_style(
    raw_line: dict,
) -> tuple[str, str | None, float | None, float | None]:
    parts: list[str] = []
    fonts: list[str] = []
    sizes: list[float] = []
    origins: list[float] = []
    for span in raw_line.get("spans", []):
        text = (span.get("text") or "").strip()
        if not text:
            continue
        parts.append(span.get("text") or "")
        font = span.get("font") or ""
        if font:
            fonts.append(font)
        size = float(span.get("size") or 0.0)
        if size:
            sizes.append(size)
        origin = span.get("origin")
        if origin and len(origin) >= 2:
            origins.append(float(origin[1]))
    joined = "".join(parts).strip()
    font = _dominant(fonts) if fonts else None
    size = float(median(sizes)) if sizes else None
    baseline = float(median(origins)) if origins else None
    return joined, font, size, baseline


def _table_rects(page: pymupdf.Page) -> list[pymupdf.Rect]:
    try:
        finder = page.find_tables()
    except Exception:
        return []
    rects: list[pymupdf.Rect] = []
    for table in getattr(finder, "tables", []) or []:
        bbox = getattr(table, "bbox", None)
        if bbox is None:
            continue
        rects.append(pymupdf.Rect(bbox))
    return rects


def _apply_table_roles(
    blocks: list[_Block], table_rects_by_page: dict[int, list[pymupdf.Rect]]
) -> None:
    for block in blocks:
        rects = table_rects_by_page.get(block.page) or []
        if not rects:
            continue
        block_rect = pymupdf.Rect(block.bbox)
        block_area = block_rect.get_area()
        if block_area <= 0:
            continue
        for table_rect in rects:
            overlap = block_rect & table_rect
            if overlap.get_area() / block_area >= 0.5:
                block.role = "table"
                break


def _apply_signature_roles(blocks: list[_Block]) -> None:
    for block in blocks:
        if block.role != "body":
            continue
        if _looks_like_signature(block):
            block.role = "signature"


def _looks_like_signature(block: _Block) -> bool:
    mid_y = (block.y0 + block.y1) / 2
    in_zone = mid_y >= block.page_height * _SIGNATURE_ZONE_RATIO
    if not in_zone:
        return False
    return bool(_SIGNATURE_RE.search(block.text))


def _block_from_lines(
    source: _Block, lines: list[_Line], *, role: ParagraphType | None = None
) -> _Block:
    fonts = [line.font for line in lines if line.font]
    sizes = [line.size for line in lines if line.size is not None]
    return _Block(
        lines=lines,
        bbox=_union_bbox(line.bbox for line in lines),
        page=source.page,
        page_width=source.page_width,
        page_height=source.page_height,
        text=" ".join(line.text for line in lines),
        role=role or source.role,
        font=_dominant(fonts) if fonts else None,
        size=float(median(sizes)) if sizes else None,
        alignment=source.alignment,
    )


def _split_leading_headings(blocks: list[_Block]) -> list[_Block]:
    """
    PyMuPDF often glues '2. REGISTERED OFFICE' onto the first body line
    of the clause. Split that heading off so it is not compared to
    justified body copy. Mixed-case form labels and amounts stay body.
    """
    split: list[_Block] = []
    for block in blocks:
        if block.role != "body" or len(block.lines) < 2:
            split.append(block)
            continue
        first = block.lines[0]
        rest = block.lines[1:]
        if not _is_clause_heading_text(first.text):
            split.append(block)
            continue
        rest_text = " ".join(line.text for line in rest)
        if len(rest_text.replace(" ", "")) < MIN_BLOCK_CHARS:
            split.append(block)
            continue
        split.append(_block_from_lines(block, [first], role="heading"))
        split.append(_block_from_lines(block, rest, role="body"))
    return split


def _is_clause_heading_text(text: str) -> bool:
    """Numbered section titles ('2. NAME') or short ALL-CAPS titles.
    A line that merely starts with a digit (an address, a quantity) is not
    a section heading unless it matches the numbered-title pattern."""
    text = text.strip()
    if not text:
        return False
    if _CLAUSE_HEADING_RE.match(text):
        return True
    if text[0].isdigit():
        return False
    letters = sum(1 for ch in text if ch.isalpha())
    return text.isupper() and letters >= 8 and len(text) <= 80


def _is_heading_block(block: _Block) -> bool:
    if block.role != "body":
        return False
    # A numbered heading wraps onto two lines at most. A long uppercase run
    # ("26 JUN ATM WITHDRAWAL 90.00 …") is a table that matches the pattern.
    if len(block.lines) > 2:
        return False
    text = block.text.strip()
    if _CLAUSE_HEADING_RE.match(text):
        return True
    if not _is_clause_heading_text(text):
        return False
    return block.width < block.page_width * _TITLE_PAGE_SHORT_RATIO


def _apply_heading_and_cover_roles(blocks: list[_Block]) -> None:
    for page_blocks in _by_page(blocks).values():
        body = [b for b in page_blocks if b.role == "body"]
        if _is_title_page(body):
            for block in body:
                if block.width < block.page_width * _TITLE_PAGE_SHORT_RATIO:
                    block.role = "cover"
        for block in page_blocks:
            if _is_heading_block(block):
                block.role = "heading"


# Consecutive lines that PyMuPDF splits into separate blocks (typical for
# centred/right-aligned copy, where each line has a different x0) are
# reconstituted into one paragraph when the vertical gap is just leading,
# not a paragraph break.
_MAX_INTRA_PARAGRAPH_GAP_PT = 5.0


def _horizontally_related(a: _Block, b: _Block) -> bool:
    overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    min_width = min(a.width, b.width)
    if min_width > 0 and overlap / min_width >= 0.3:
        return True
    mid = (b.x0 + b.x1) / 2
    return a.x0 - ALIGN_EDGE_TOLERANCE_PT <= mid <= a.x1 + ALIGN_EDGE_TOLERANCE_PT


def _combine_blocks(a: _Block, b: _Block) -> _Block:
    lines = a.lines + b.lines
    bbox = _union_bbox([a.bbox, b.bbox])
    sizes = [s for s in (a.size, b.size) if s is not None]
    return _Block(
        lines=lines,
        bbox=bbox,
        page=a.page,
        page_width=a.page_width,
        page_height=a.page_height,
        text=" ".join(line.text for line in lines),
        role=a.role,
        font=a.font if a.font == b.font else (a.font or b.font),
        size=float(median(sizes)) if sizes else None,
        alignment=a.alignment if a.alignment == b.alignment else None,
    )


def _merge_adjacent_blocks(blocks: list[_Block]) -> list[_Block]:
    if not blocks:
        return []
    ordered = sorted(blocks, key=lambda b: (b.page, b.y0, b.x0))
    merged = [ordered[0]]
    for block in ordered[1:]:
        prev = merged[-1]
        gap = block.y0 - prev.y1
        if (
            block.page == prev.page
            and block.role == prev.role
            and -1.0 <= gap <= _MAX_INTRA_PARAGRAPH_GAP_PT
            and _horizontally_related(prev, block)
        ):
            merged[-1] = _combine_blocks(prev, block)
        else:
            merged.append(block)
    return merged


# "Page 4 of 6" is the only part of a running footer that changes from page to page.
_PAGE_NUMBER_RE = re.compile(r"\bpage \d+ (?:of|/) \d+\b")


def _mark_repeating_chrome(blocks: list[_Block]) -> None:
    """Text that repeats at the same height on several pages is running furniture.

    Column headings and registration lines sit in the body band, but they are
    the same on every page, so they are not paragraphs to indent against.
    """
    pages = {block.page for block in blocks}
    if len(pages) < 2:
        return
    grouped: dict[str, list[_Block]] = {}
    for block in blocks:
        key = _PAGE_NUMBER_RE.sub("page # of #", " ".join(block.text.split()).lower())
        if len(key) < 8:
            continue
        grouped.setdefault(key, []).append(block)
    for items in grouped.values():
        if len({block.page for block in items}) < 2:
            continue
        ys = [block.y0 for block in items]
        if max(ys) - min(ys) > 24:
            continue
        for block in items:
            block.role = "header" if block.y0 < block.page_height * 0.5 else "footer"


def _position_band(bbox: BBox, page_height: float) -> Literal["header", "footer", "body"]:
    mid_y = (bbox[1] + bbox[3]) / 2
    if mid_y <= page_height * HEADER_RATIO:
        return "header"
    if mid_y >= page_height * (1.0 - FOOTER_RATIO):
        return "footer"
    return "body"


def _page_margins(blocks: list[_Block]) -> list[PageMargins]:
    """Where ink sits on each page.

    Top, bottom, and the right edge of a short page follow how full the
    page is. The margin check does not treat those as margins.
    """
    records: list[PageMargins] = []
    for page, page_blocks in sorted(_by_page(blocks).items()):
        xs0 = [b.x0 for b in page_blocks]
        ys0 = [b.y0 for b in page_blocks]
        xs1 = [b.x1 for b in page_blocks]
        ys1 = [b.y1 for b in page_blocks]
        width = page_blocks[0].page_width
        height = page_blocks[0].page_height
        records.append(
            PageMargins(
                page=page + 1,
                left=round(min(xs0), 2),
                right=round(width - max(xs1), 2),
                top=round(min(ys0), 2),
                bottom=round(height - max(ys1), 2),
                page_width=round(width, 2),
                page_height=round(height, 2),
            )
        )
    return records


# A vertical rule on the page: x, top, bottom. Table cell borders are drawn,
# so a cell's text lines up with them rather than with other text.
_Rule = tuple[float, float, float]


def _vertical_rules(doc: pymupdf.Document) -> dict[int, list[_Rule]]:
    """Vertical lines and rectangle sides drawn on each page."""
    rules: dict[int, list[_Rule]] = {}
    for page_index, page in enumerate(cast(Iterable, doc)):
        found: list[_Rule] = []
        try:
            drawings = page.get_drawings()
        except Exception:
            drawings = []
        for drawing in drawings:
            for item in drawing.get("items", []):
                if item[0] == "re":
                    rect = item[1]
                    found.append((float(rect.x0), float(rect.y0), float(rect.y1)))
                    found.append((float(rect.x1), float(rect.y0), float(rect.y1)))
                elif item[0] == "l":
                    start, end = item[1], item[2]
                    if abs(start.x - end.x) < 0.5:
                        found.append((float(start.x), min(start.y, end.y), max(start.y, end.y)))
        rules[page_index] = found
    return rules


def _is_title_page(blocks: list[_Block]) -> bool:
    if len(blocks) < 4:
        return False
    page_width = blocks[0].page_width
    if page_width <= 0:
        return False
    cut = page_width * _TITLE_PAGE_SHORT_RATIO
    # A cover is stacked short lines. One single-line company name can sit
    # among them. A tall paragraph, or several wide lines, is a body page.
    wide = [block for block in blocks if block.width >= cut]
    if any(len(block.lines) >= 2 or block.height > 36 for block in wide):
        return False
    if len(wide) > 1:
        return False
    short = len(blocks) - len(wide)
    return short / len(blocks) >= _TITLE_PAGE_SHORT_FRACTION
