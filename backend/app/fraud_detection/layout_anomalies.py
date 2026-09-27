from __future__ import annotations

import logging
import re
from collections import Counter
from statistics import median
from typing import Literal

import pymupdf
from pydantic import BaseModel, Field, model_serializer

logger = logging.getLogger("fraud_detection.layout_anomalies")

BBox = tuple[float, float, float, float]
Alignment = Literal["left-aligned", "centred", "right-aligned", "justified"]
AnomalyType = Literal["Indentation", "Alignment", "Margin", "Spacing"]
ParagraphType = Literal["body", "header", "footer", "table", "signature", "heading", "cover"]

# A left/right indent this far (or further) from comparable neighbours is a
# tamper signal, not typesetting noise. 12pt ≈ 4.2mm — below the 6–12mm
# clause-block shifts the constitution generator injects as "misalignment".
INDENT_THRESHOLD_PT = 12.0

# Page margins this far from the document's established pattern are
# treated as a different layout, not printer variance.
MARGIN_THRESHOLD_PT = 18.0

# Running header/footer chrome this far from the document's typical
# y-position is a swapped page template, not a clause title that merely
# sits in the header band.
CHROME_POS_THRESHOLD_PT = 12.0

# Side-by-side items on the same visual row (label left, amount right)
# whose top or baseline differs by this much are treated as vertically
# misaligned — the check the horizontal left/centre/right classifier
# does not cover.
ROW_ALIGN_THRESHOLD_PT = 6.0

# On a title/cover page (most lines are short, stacked, and share a
# centre axis) a line whose midpoint is this far off that axis is a
# shoved heading — e.g. a company name sitting ~47pt right of the
# other cover titles. Not applied to full-width clause pages.
CENTER_SHIFT_THRESHOLD_PT = 18.0
_TITLE_PAGE_SHORT_RATIO = 0.55
_TITLE_PAGE_SHORT_FRACTION = 0.70
_CENTER_MID_BUCKET_PT = 2.0

# Intra-paragraph line leading this far from the page's typical leading
# is a typesetting break (inserted line or a restyled pasted clause).
LINE_SPACING_THRESHOLD_PT = 4.0

# Gap between consecutive body paragraphs this far from the local
# pattern is a paragraph-spacing break. Gaps several times the typical
# rhythm are treated as section breaks, not insertions.
PARA_SPACING_THRESHOLD_PT = 8.0
_PARA_SECTION_BREAK_MULT = 3.0

# How close a line's edge must sit to the content column to count as flush.
ALIGN_EDGE_TOLERANCE_PT = 8.0
# Leftover left/right within this of each other → centred rather than a
# slightly-indented left-aligned line.
CENTER_SYMMETRY_TOLERANCE_PT = 12.0
# A line filling this much of the content column is full-width (justified
# or a long left-aligned line that happens to wrap flush).
JUSTIFY_FILL_RATIO = 0.90
# Blocks narrower than this fraction of the content column are "short"
# (titles, dates, labels) and are only compared to other short blocks.
SHORT_WIDTH_RATIO = 0.55

# Header/footer bands as a fraction of page height — running titles and
# page numbers live here and are not comparable to body copy.
HEADER_RATIO = 0.10
FOOTER_RATIO = 0.08

MIN_BLOCK_CHARS = 12
_TEXT_SNIPPET_CHARS = 200
_INDENT_BUCKET_PT = 1.0
_LOCAL_WINDOW = 2
# Lower page band (but above the footer strip) where a short "Signed by…"
# block is a signature, not body copy to compare against paragraphs.
_SIGNATURE_ZONE_RATIO = 0.65
_SIGNATURE_RE = re.compile(
    r"\b(signed|signature|signatory|witnesseth|witness|director|"
    r"authorised signatory|authorized signatory|for and on behalf)\b",
    re.IGNORECASE,
)
# "2. REGISTERED OFFICE" / "1. NAME" — not "5.1 The issued share capital…".
_CLAUSE_HEADING_RE = re.compile(
    r"^\d+(?:\.\d+)*\.?\s+[A-Z0-9][A-Z0-9 ,/&'().:—–-]{1,}$"
)
# Neighbouring flags only count as a local insertion stretch when both
# boxes are full-width clause columns, not short titles or cover lines.
_CLAUSE_STRETCH_MIN_WIDTH_PT = 330.0


class LayoutAnomaly(BaseModel):
    """
    One affected area (same page and bbox), not one check.

    `anomaly_type` is the strongest check: Indentation, Alignment, Margin,
    or Spacing. Extra checks on the same area are `evidence.signals`, not
    extra rows.     Neighbouring full-width flagged clauses set `local_pattern` and
    `consecutive_run` on this finding; they need not share anomaly_type.
    `detected` / `expected` / `deviation` hold the measurements on the
    object. The JSON body keeps `summary` and a `checks` list
    (check, detected, expected, difference) and leaves out the repeated
    evidence dict. `bbox` / `text` are for highlighting.
    """

    anomaly_type: AnomalyType
    page: int
    location: str
    bbox: BBox
    text: str
    detected: str
    expected: str
    baseline: str | None = None
    deviation: str
    confidence: float
    evidence: dict
    summary: str

    @model_serializer(mode="wrap")
    def _serialize(self, handler):
        """HTTP body is one finding with a short check list.

        The newline-joined detected/expected strings and the raw evidence
        dict repeat the same measurements, so they stay on the object for
        the checkers and drop out of the JSON.
        """
        del handler
        signals = self.evidence.get("signals")
        if not isinstance(signals, list) or not signals:
            signals = [_signal_record(self)]
        return {
            "type": self.anomaly_type,
            "page": self.page,
            "where": self.location,
            "text": self.text,
            "summary": self.summary,
            "confidence": self.confidence,
            "checks": [_readable_check(signal) for signal in signals if isinstance(signal, dict)],
        }


class CheckAssessment(BaseModel):
    """AC5: the relevant check is marked Unable to Assess when its inputs
    cannot be extracted or there isn't enough comparable content."""

    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


class PageMargins(BaseModel):
    page: int
    left: float
    right: float
    top: float
    bottom: float
    page_width: float
    page_height: float


class LayoutElement(BaseModel):
    """
    Per-paragraph baseline used to form comparable groups. Headers are
    not compared to body copy, so a centred company name is not an
    Alignment anomaly just because the clauses under it are left-aligned.
    """

    page: int
    bbox: BBox
    left: float
    top: float
    right: float
    bottom: float
    alignment: Alignment | None = None
    paragraph_type: ParagraphType
    font: str | None = None
    size: float | None = None
    text: str


class LayoutAnomalyResult(BaseModel):
    """
    Shared response for GET /documents/{id}/detection/layout-anomalies
    and POST /extraction/test-layout-anomalies.

    Each of indentation / alignment / margin / spacing can be Unable to
    Assess while the other families still return findings. Empty anomalies
    with unable_to_assess=false means the checks ran and nothing exceeded
    a threshold — not that the document is free of fraud.
    """

    document_id: str
    anomalies: list[LayoutAnomaly]
    # Full paragraph inventory and per-page margin tables stay available on
    # the result object. They are omitted from the JSON so a finding is
    # readable without scrolling past every line on the document.
    elements: list[LayoutElement] = Field(default_factory=list, exclude=True)
    page_margins: list[PageMargins] = Field(exclude=True)
    elements_analysed: int = Field(exclude=True)
    indentation: CheckAssessment
    alignment: CheckAssessment
    margin: CheckAssessment
    spacing: CheckAssessment
    unable_to_assess: bool
    unable_to_assess_reason: str | None = None


class _Line(BaseModel):
    text: str
    bbox: BBox
    page: int  # 0-indexed
    page_width: float
    page_height: float
    font: str | None = None
    size: float | None = None


class _Block(BaseModel):
    lines: list[_Line]
    bbox: BBox
    page: int
    page_width: float
    page_height: float
    text: str
    role: ParagraphType
    font: str | None = None
    size: float | None = None
    alignment: Alignment | None = None

    @property
    def x0(self) -> float:
        return self.bbox[0]

    @property
    def y0(self) -> float:
        return self.bbox[1]

    @property
    def x1(self) -> float:
        return self.bbox[2]

    @property
    def y1(self) -> float:
        return self.bbox[3]

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def char_count(self) -> int:
        return len(self.text.replace(" ", ""))


def _empty_result(document_id: str, reason: str) -> LayoutAnomalyResult:
    unable = CheckAssessment(unable_to_assess=True, unable_to_assess_reason=reason)
    return LayoutAnomalyResult(
        document_id=document_id,
        anomalies=[],
        elements=[],
        page_margins=[],
        elements_analysed=0,
        indentation=unable,
        alignment=unable,
        margin=unable,
        spacing=unable,
        unable_to_assess=True,
        unable_to_assess_reason=reason,
    )


def _assessed() -> CheckAssessment:
    return CheckAssessment(unable_to_assess=False, unable_to_assess_reason=None)


def check_layout_anomalies(document_id: str, file_bytes: bytes) -> LayoutAnomalyResult:
    """
    Inspects a PDF's own text positioning for internal inconsistency —
    indented/aligned fragments that clash with surrounding copy, and pages
    whose margins don't match the rest of the document. Text/geometry
    based, no LLM tokens. Synchronous/CPU-bound — callers should run this
    via asyncio.to_thread.
    """
    try:
        doc = _open_document(file_bytes)
    except Exception as exc:
        reason = f"Failed to open document: {type(exc).__name__}: {exc}"
        logger.warning("Document %s: %s", document_id, reason, exc_info=True)
        return _empty_result(document_id, reason)

    try:
        blocks = _extract_blocks(doc)
        if not blocks and _needs_ocr(doc, file_bytes):
            blocks = _ocr_blocks(doc)
        if not blocks:
            has_images = any(page.get_images() for page in doc)
            reason = (
                f"No extractable text layout found ({doc.page_count} page(s), "
                f"has embedded images: {has_images}) -- likely a scanned/"
                "image-only PDF with no text layer, or an encrypted/"
                "corrupted one."
            )
            logger.warning("Layout anomaly check: document %s: %s", document_id, reason)
            return _empty_result(document_id, reason)

        page_margins = _page_margins(blocks)
        _assign_alignments(blocks)
        indent_anomalies, indent_check = _check_indentation(blocks)
        right_edge_anomalies, right_edge_check = _check_right_edge(blocks)
        align_anomalies, align_check = _check_alignment(blocks)
        row_anomalies = _check_row_alignment(blocks)
        centre_anomalies = _check_centre_shift(blocks)
        line_anomalies, line_check = _check_line_spacing(blocks)
        para_anomalies, para_check = _check_paragraph_spacing(blocks)
        margin_anomalies, margin_check = _check_margins(page_margins, blocks)
        protrude_anomalies = _check_protruding_right(blocks)
        chrome_anomalies, chrome_check = _check_chrome_position(blocks)
        spacing_check = _merge_spacing_assessment(line_check, para_check)
        if not indent_check.unable_to_assess or not right_edge_check.unable_to_assess:
            indent_check = _assessed()
        if not margin_check.unable_to_assess or not chrome_check.unable_to_assess:
            margin_check = _assessed()

        anomalies = (
            indent_anomalies
            + right_edge_anomalies
            + align_anomalies
            + row_anomalies
            + centre_anomalies
            + line_anomalies
            + para_anomalies
            + margin_anomalies
            + chrome_anomalies
            + protrude_anomalies
        )
        anomalies = _coalesce_signals(anomalies)
        _mark_local_consecutive(anomalies)
        elements = _compile_elements(blocks)

        checks = (indent_check, align_check, margin_check, spacing_check)
        unable = all(check.unable_to_assess for check in checks)
        reason = None
        if unable:
            reason = (
                "Not enough comparable text to assess layout. "
                "A one-page document can still be checked from its own lines; "
                "margins need a second page."
            )
        return LayoutAnomalyResult(
            document_id=document_id,
            anomalies=anomalies,
            elements=elements,
            page_margins=page_margins,
            elements_analysed=len(elements),
            indentation=indent_check,
            alignment=align_check,
            margin=margin_check,
            spacing=spacing_check,
            unable_to_assess=unable,
            unable_to_assess_reason=reason,
        )
    except Exception as exc:
        logger.exception("Layout anomaly check failed for document %s", document_id)
        reason = f"Unexpected error during layout anomaly check: {type(exc).__name__}: {exc}"
        return _empty_result(document_id, reason)
    finally:
        doc.close()


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
    for page in doc:
        if (page.get_text("text") or "").strip():
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
    for page_index, page in enumerate(doc):
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
        scale_x = float(page.rect.width) / float(pix.width) if pix.width else 1.0
        scale_y = float(page.rect.height) / float(pix.height) if pix.height else 1.0
        page_width = float(page.rect.width)
        page_height = float(page.rect.height)
        for box, raw_text in zip(boxes, texts):
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


def _extract_blocks(doc: pymupdf.Document) -> list[_Block]:
    blocks: list[_Block] = []
    table_rects_by_page: dict[int, list[pymupdf.Rect]] = {}
    for page_index, page in enumerate(doc):
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
                text, font, size = _line_text_and_style(raw_line)
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
                    font=_dominant(fonts) if fonts else None,  # type: ignore[arg-type]
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


def _line_text_and_style(raw_line: dict) -> tuple[str, str | None, float | None]:
    parts: list[str] = []
    fonts: list[str] = []
    sizes: list[float] = []
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
    joined = "".join(parts).strip()
    font = _dominant(fonts) if fonts else None
    size = float(median(sizes)) if sizes else None
    return joined, font, size  # type: ignore[return-value]


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
        font=_dominant(fonts) if fonts else None,  # type: ignore[arg-type]
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
    text = block.text.strip()
    if _CLAUSE_HEADING_RE.match(text):
        return True
    if not _is_clause_heading_text(text):
        return False
    return block.width < block.page_width * _TITLE_PAGE_SHORT_RATIO


def _apply_heading_and_cover_roles(blocks: list[_Block]) -> None:
    by_page: dict[int, list[_Block]] = {}
    for block in blocks:
        by_page.setdefault(block.page, []).append(block)
    for page_blocks in by_page.values():
        body = [b for b in page_blocks if b.role == "body"]
        if _is_title_page(body):
            for block in body:
                if block.width < block.page_width * _TITLE_PAGE_SHORT_RATIO:
                    block.role = "cover"
        for block in page_blocks:
            if _is_heading_block(block):
                block.role = "heading"


def _assign_alignments(blocks: list[_Block]) -> None:
    by_page: dict[int, list[_Block]] = {}
    for block in blocks:
        by_page.setdefault(block.page, []).append(block)
    for page_blocks in by_page.values():
        body = [b for b in page_blocks if b.role == "body"]
        column = _content_column(body) or _content_column(page_blocks)
        if column is None:
            continue
        content_left, content_right = column
        for block in page_blocks:
            block.alignment = _classify_block_alignment(block, content_left, content_right)


def _compile_elements(blocks: list[_Block]) -> list[LayoutElement]:
    elements: list[LayoutElement] = []
    for block in blocks:
        snippet = (
            block.text
            if len(block.text) <= _TEXT_SNIPPET_CHARS
            else block.text[: _TEXT_SNIPPET_CHARS - 1] + "…"
        )
        elements.append(
            LayoutElement(
                page=block.page + 1,
                bbox=tuple(round(v, 2) for v in block.bbox),  # type: ignore[arg-type]
                left=round(block.x0, 2),
                top=round(block.y0, 2),
                right=round(block.x1, 2),
                bottom=round(block.y1, 2),
                alignment=block.alignment,
                paragraph_type=block.role,
                font=block.font,
                size=round(block.size, 2) if block.size is not None else None,
                text=snippet,
            )
        )
    return elements


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


def _as_bbox(raw) -> BBox | None:
    if not raw or len(raw) != 4:
        return None
    return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))


def _union_bbox(bboxes) -> BBox:
    xs0, ys0, xs1, ys1 = zip(*bboxes)
    return (min(xs0), min(ys0), max(xs1), max(ys1))


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
        key = " ".join(block.text.split()).lower()
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
    by_page: dict[int, list[_Block]] = {}
    for block in blocks:
        by_page.setdefault(block.page, []).append(block)

    records: list[PageMargins] = []
    for page, page_blocks in sorted(by_page.items()):
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


def _body_blocks(blocks: list[_Block]) -> list[_Block]:
    """Lines that can be compared with each other.

    Body paragraphs are the usual set. A one-page form often has only one
    of those and the rest tagged as headings, so those headings join the
    comparison when body copy alone is not enough. Running headers and
    footers stay out.
    """
    body = [b for b in blocks if b.role == "body" and b.char_count >= MIN_BLOCK_CHARS]
    if len(body) >= 2:
        return body
    others = [
        b
        for b in blocks
        if b.role in {"heading", "cover", "table"} and b.char_count >= MIN_BLOCK_CHARS
    ]
    if len(body) + len(others) >= 2:
        return body + others
    return body


# A left edge shared by this many blocks is a column (dates, descriptions,
# amounts), not one shoved paragraph. Two full-width clauses still count
# as an indent run.
_COLUMN_MIN_MEMBERS = 3
_SHORT_COLUMN_MIN_MEMBERS = 2


def _cluster_by_left(blocks: list[_Block]) -> list[list[_Block]]:
    clusters: list[list[_Block]] = []
    for block in sorted(blocks, key=lambda b: b.x0):
        if clusters and block.x0 - clusters[-1][0].x0 < INDENT_THRESHOLD_PT:
            clusters[-1].append(block)
        else:
            clusters.append([block])
    return clusters


def _page_columns(blocks: list[_Block]) -> list[float]:
    """Repeated left edges on a multi-column page.

    Empty when the page has a single body column, so a one-off indent
    among uniform paragraphs still uses the neighbour comparison.
    A pair of short amount lines joins the columns. A pair of full-width
    clauses does not — that is the consecutive-indent signal.
    """
    clusters = _cluster_by_left(blocks)
    wide = [cluster for cluster in clusters if len(cluster) >= _COLUMN_MIN_MEMBERS]
    if not wide:
        return []
    columns = [float(median(b.x0 for b in cluster)) for cluster in wide]
    for cluster in clusters:
        if len(cluster) < _SHORT_COLUMN_MIN_MEMBERS or len(cluster) >= _COLUMN_MIN_MEMBERS:
            continue
        if median(b.width for b in cluster) >= _CLAUSE_STRETCH_MIN_WIDTH_PT:
            continue
        columns.append(float(median(b.x0 for b in cluster)))
    return columns


def _column_of(block: _Block, columns: list[float]) -> float | None:
    if not columns:
        return None
    nearest = min(columns, key=lambda column: abs(block.x0 - column))
    if abs(block.x0 - nearest) < INDENT_THRESHOLD_PT:
        return nearest
    return None


def _column_groups(blocks: list[_Block], columns: list[float]) -> list[list[_Block]]:
    """Blocks on each column. A line on no column is judged against the nearest one."""
    grouped: dict[float, list[_Block]] = {column: [] for column in columns}
    for block in blocks:
        home = _column_of(block, columns)
        if home is None:
            home = min(columns, key=lambda column: abs(block.x0 - column))
        grouped[home].append(block)
    return [group for group in grouped.values() if group]


def _content_column(blocks: list[_Block]) -> tuple[float, float] | None:
    """Dominant left/right edges of full-ish body copy on this set of blocks."""
    if not blocks:
        return None
    lefts = [_bucket_pt(b.x0) for b in blocks]
    content_left = _dominant(lefts)
    if content_left is None:
        content_left = median(b.x0 for b in blocks)

    widths = sorted(b.width for b in blocks)
    wide_cut = widths[len(widths) // 2]
    wide = [b for b in blocks if b.width >= wide_cut] or list(blocks)
    content_right = median(b.x1 for b in wide)
    if content_right <= content_left:
        return None
    return (float(content_left), float(content_right))


def _width_band(block: _Block, content_left: float, content_right: float) -> Literal["full", "short"]:
    column_width = content_right - content_left
    if column_width <= 0:
        return "short"
    return "full" if block.width >= SHORT_WIDTH_RATIO * column_width else "short"


def _reaches_column_right(block: _Block, content_right: float) -> bool:
    """True when the block still meets the column edge — a reflowed wrap,
    not a short title/label that merely starts further in."""
    return content_right - block.x1 < INDENT_THRESHOLD_PT


def _has_strong_majority(matching: int, comparable: int) -> bool:
    """Enough peers share a pattern that an outlier is not a coin flip."""
    if comparable < 2:
        return False
    return matching >= max(3, (comparable + 1) // 2 + 1)


def _check_indentation(blocks: list[_Block]) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    body = _body_blocks(blocks)
    if len(body) < 2:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Not enough comparable lines to assess indentation "
                f"({len(body)} found, need at least 2)"
            ),
        )

    anomalies: list[LayoutAnomaly] = []
    by_page: dict[int, list[_Block]] = {}
    for block in body:
        by_page.setdefault(block.page, []).append(block)

    flagged = False
    for page_blocks in by_page.values():
        page_blocks = sorted(page_blocks, key=lambda b: (b.y0, b.x0))
        columns = _page_columns(page_blocks)
        if len(columns) >= 2:
            # Dates, descriptions, and amounts each keep their own left
            # edge. Only a line that sits on none of those columns is an indent.
            flagged = True
            for block in page_blocks:
                if _column_of(block, columns) is not None:
                    continue
                nearest = min(columns, key=lambda column: abs(block.x0 - column))
                signed_left = block.x0 - nearest
                if abs(signed_left) < INDENT_THRESHOLD_PT:
                    continue
                # A short label or amount sitting between columns is not a
                # shoved paragraph. Only a line that fills the content span is.
                span = max(b.x1 for b in page_blocks) - min(b.x0 for b in page_blocks)
                if span <= 0 or block.width < SHORT_WIDTH_RATIO * span:
                    continue
                matching = sum(
                    1 for peer in page_blocks if _column_of(peer, columns) == nearest
                )
                comparable = len(page_blocks)
                anomalies.append(
                    _anomaly(
                        "Indentation",
                        block,
                        detected=_pt(block.x0),
                        expected=_pt(nearest),
                        baseline=(
                            f"{_pct(matching, comparable)} of comparable body paragraphs "
                            f"start at {_pt(nearest)}"
                        ),
                        deviation=_signed_pt(signed_left),
                        confidence=_delta_confidence(
                            abs(signed_left),
                            INDENT_THRESHOLD_PT,
                            matching / comparable if comparable else 0.0,
                        ),
                        evidence={
                            "kind": "indentation",
                            "side": "left",
                            "left_indent_pt": round(block.x0, 2),
                            "expected_left_indent_pt": round(nearest, 2),
                            "left_delta_pt": round(signed_left, 2),
                            "comparable_count": comparable,
                            "matching_count": matching,
                        },
                    )
                )
            continue
        column = _content_column(page_blocks)
        if column is None:
            continue
        content_left, content_right = column
        full = [b for b in page_blocks if _width_band(b, content_left, content_right) == "full"]
        if len(full) < 2:
            # A one-page form is short labels, not full-width clauses.
            # Compare those labels when they share a left edge.
            short_findings = _short_line_indents(page_blocks)
            if short_findings is not None:
                flagged = True
                anomalies.extend(short_findings)
            continue
        flagged = True
        page_left = _dominant(_bucket_pt(b.x0) for b in full)

        for index, block in enumerate(full):
            window = full[max(0, index - _LOCAL_WINDOW) : index] + full[index + 1 : index + 1 + _LOCAL_WINDOW]
            expected_left = _dominant(_bucket_pt(b.x0) for b in window)
            if expected_left is None:
                expected_left = page_left

            left_delta = (
                abs(block.x0 - expected_left)
                if expected_left is not None
                else 0.0
            )
            if left_delta < INDENT_THRESHOLD_PT:
                continue
            if not _reaches_column_right(block, content_right):
                continue

            matching = sum(
                1
                for peer in full
                if expected_left is not None
                and abs(peer.x0 - expected_left) < INDENT_THRESHOLD_PT
            )
            signed_left = (
                block.x0 - expected_left if expected_left is not None else left_delta
            )
            majority_frac = matching / len(full) if full else 0.0
            evidence = {
                "kind": "indentation",
                "side": "left",
                "left_indent_pt": round(block.x0, 2),
                "expected_left_indent_pt": (
                    round(expected_left, 2) if expected_left is not None else None
                ),
                "left_delta_pt": round(signed_left, 2),
                "comparable_count": len(full),
                "matching_count": matching,
            }

            anomalies.append(
                _anomaly(
                    "Indentation",
                    block,
                    detected=_pt(block.x0),
                    expected=_pt(expected_left) if expected_left is not None else "unknown",
                    baseline=(
                        f"{_pct(matching, len(full))} of comparable body paragraphs "
                        f"start at {_pt(expected_left)}"
                        if expected_left is not None
                        else None
                    ),
                    deviation=_signed_pt(signed_left),
                    confidence=_delta_confidence(left_delta, INDENT_THRESHOLD_PT, majority_frac),
                    evidence=evidence,
                )
            )

    if not flagged:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Not enough full-width body paragraphs on any page to assess indentation"
            ),
        )
    return anomalies, _assessed()


def _short_line_indents(page_blocks: list[_Block]) -> list[LayoutAnomaly] | None:
    """Left-edge outliers among short lines that share a width.

    None means there is no majority edge, so indentation stays unassessed.
    An empty list means the lines were compared and none is shifted.
    A line much wider or narrower than the majority is a different column
    (a value beside a label), not an indent.
    """
    if len(page_blocks) < 4:
        return None
    expected_left = _dominant(_bucket_pt(block.x0) for block in page_blocks)
    if expected_left is None:
        return None
    matching = [
        block
        for block in page_blocks
        if abs(block.x0 - expected_left) < INDENT_THRESHOLD_PT
    ]
    if not _has_strong_majority(len(matching), len(page_blocks)):
        return None
    typical_width = float(median(block.width for block in matching))
    anomalies: list[LayoutAnomaly] = []
    for block in page_blocks:
        signed = block.x0 - expected_left
        if abs(signed) < INDENT_THRESHOLD_PT:
            continue
        width_gap = abs(block.width - typical_width)
        if width_gap > max(40.0, 0.45 * typical_width):
            continue
        anomalies.append(
            _anomaly(
                "Indentation",
                block,
                detected=_pt(block.x0),
                expected=_pt(expected_left),
                baseline=(
                    f"{_pct(len(matching), len(page_blocks))} of comparable lines "
                    f"start at {_pt(expected_left)}"
                ),
                deviation=_signed_pt(signed),
                confidence=_delta_confidence(
                    abs(signed),
                    INDENT_THRESHOLD_PT,
                    len(matching) / len(page_blocks),
                ),
                evidence={
                    "kind": "indentation",
                    "side": "left",
                    "left_indent_pt": round(block.x0, 2),
                    "expected_left_indent_pt": round(expected_left, 2),
                    "left_delta_pt": round(signed, 2),
                    "comparable_count": len(page_blocks),
                    "matching_count": len(matching),
                },
            )
        )
    return anomalies


def _consistent_right_edge(block: _Block) -> float | None:
    """
    Right edge of a wrapped column, ignoring a short last line. A single
    line that simply does not fill the column is not a reflowed paste.
    Ragged left-aligned copy (intermediate lines end at different x) has
    no stable right indent to compare, so returns None.
    """
    if len(block.lines) < 2:
        return None
    lines = block.lines[:-1]
    rights = [line.bbox[2] for line in lines]
    if max(rights) - min(rights) > ALIGN_EDGE_TOLERANCE_PT:
        return None
    return float(median(rights))


def _check_right_edge(blocks: list[_Block]) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    """
    Left indent can match while the paragraph still ends short or long of
    the column — a pasted clause that was reflowed to a different width.
    Only full-width blocks with a stable right edge (justified / wrapped
    column, not ragged left-aligned copy) are compared.
    """
    body = _body_blocks(blocks)
    if len(body) < 2:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Not enough comparable body paragraphs to assess right-edge consistency "
                f"({len(body)} found, need at least 2)"
            ),
        )

    anomalies: list[LayoutAnomaly] = []
    by_page: dict[int, list[_Block]] = {}
    for block in body:
        by_page.setdefault(block.page, []).append(block)

    flagged = False
    for page_blocks in by_page.values():
        page_blocks = sorted(page_blocks, key=lambda b: (b.y0, b.x0))
        column = _content_column(page_blocks)
        if column is None:
            continue
        content_left, content_right = column
        full = [b for b in page_blocks if _width_band(b, content_left, content_right) == "full"]
        edged = [(b, edge) for b in full if (edge := _consistent_right_edge(b)) is not None]
        if len(edged) < 2:
            continue
        flagged = True
        page_right = _dominant(_bucket_pt(edge) for _, edge in edged)

        for index, (block, right_edge) in enumerate(edged):
            window = edged[max(0, index - _LOCAL_WINDOW) : index] + edged[index + 1 : index + 1 + _LOCAL_WINDOW]
            expected_right = _dominant(_bucket_pt(edge) for _, edge in window)
            if expected_right is None:
                expected_right = page_right
            if expected_right is None:
                continue
            signed = right_edge - float(expected_right)
            if abs(signed) < INDENT_THRESHOLD_PT:
                continue
            matching = sum(
                1 for _, edge in edged if abs(edge - float(expected_right)) < INDENT_THRESHOLD_PT
            )
            if not _has_strong_majority(matching, len(edged)):
                continue
            majority_frac = matching / len(edged)
            anomalies.append(
                _anomaly(
                    "Indentation",
                    block,
                    detected=_pt(right_edge),
                    expected=_pt(float(expected_right)),
                    baseline=(
                        f"{_pct(matching, len(edged))} of comparable body paragraphs "
                        f"end at {_pt(float(expected_right))}"
                    ),
                    deviation=_signed_pt(signed),
                    confidence=_delta_confidence(
                        abs(signed), INDENT_THRESHOLD_PT, majority_frac
                    ),
                    evidence={
                        "kind": "right-edge",
                        "side": "right",
                        "right_edge_pt": round(right_edge, 2),
                        "expected_right_edge_pt": round(float(expected_right), 2),
                        "right_delta_pt": round(signed, 2),
                        "comparable_count": len(edged),
                        "matching_count": matching,
                    },
                    location=f"Right edge of body paragraph near y={round(block.y0)}",
                )
            )

    if not flagged:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Not enough full-width body paragraphs with a stable right edge "
                "to assess right-edge consistency"
            ),
        )
    return anomalies, _assessed()


def _check_protruding_right(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """A paragraph whose right edge sticks past the document's usual edge.

    A horizontal shift often leaves the left edge on the label and only
    lengthens the line. That shows up here, named on the paragraph, rather
    than as a blank page-margin finding. A whole page that is wider is left
    to the margin check.
    """
    body = _body_blocks(blocks)
    by_page: dict[int, list[_Block]] = {}
    for block in body:
        by_page.setdefault(block.page, []).append(block)
    if len(by_page) < 2:
        return []

    page_max = {
        page: max(block.x1 for block in page_blocks)
        for page, page_blocks in by_page.items()
    }
    established = float(median(page_max.values()))
    anomalies: list[LayoutAnomaly] = []
    for page_blocks in by_page.values():
        protruding = [
            block
            for block in page_blocks
            if block.x1 - established >= INDENT_THRESHOLD_PT
        ]
        if len(protruding) == 0 or len(protruding) > 2:
            continue
        for block in protruding:
            signed = block.x1 - established
            anomalies.append(
                _anomaly(
                    "Indentation",
                    block,
                    detected=_pt(block.x1),
                    expected=_pt(established),
                    baseline=(
                        f"Other pages end at {_pt(established)}"
                    ),
                    deviation=_signed_pt(signed),
                    confidence=_delta_confidence(signed, INDENT_THRESHOLD_PT, 1.0),
                    evidence={
                        "kind": "right-edge",
                        "side": "right",
                        "right_edge_pt": round(block.x1, 2),
                        "expected_right_edge_pt": round(established, 2),
                        "right_delta_pt": round(signed, 2),
                        "comparable_count": len(page_max),
                        "matching_count": sum(
                            1
                            for edge in page_max.values()
                            if abs(edge - established) < INDENT_THRESHOLD_PT
                        ),
                    },
                    location=f"Right edge of body paragraph near y={round(block.y0)}",
                )
            )
    return anomalies


def _line_leadings(block: _Block) -> list[float]:
    """Baseline-to-baseline distance between consecutive lines in a block."""
    ordered = sorted(block.lines, key=lambda line: line.bbox[1])
    leadings: list[float] = []
    for prev, curr in zip(ordered, ordered[1:]):
        leading = curr.bbox[1] - prev.bbox[1]
        if leading > 0:
            leadings.append(leading)
    return leadings


def _check_line_spacing(blocks: list[_Block]) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    body = [b for b in _body_blocks(blocks) if len(b.lines) >= 2]
    if len(body) < 2:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Not enough multi-line body paragraphs to assess line spacing "
                f"({len(body)} found, need at least 2)"
            ),
        )

    anomalies: list[LayoutAnomaly] = []
    by_page: dict[int, list[_Block]] = {}
    for block in body:
        by_page.setdefault(block.page, []).append(block)

    assessed_any = False
    for raw_blocks in by_page.values():
        raw_blocks = sorted(raw_blocks, key=lambda b: (b.y0, b.x0))
        columns = _page_columns(raw_blocks)
        # A statement's date and description columns do not share a line
        # rhythm. Spacing on those pages is the table, not a tampered paragraph.
        if len(columns) >= 2:
            continue
        groups = [raw_blocks]
        for page_blocks in groups:
            leadings_by_block = [(b, _line_leadings(b)) for b in page_blocks]
            all_leadings = [lead for _, leads in leadings_by_block for lead in leads]
            if len(all_leadings) < 3:
                continue
            assessed_any = True
            page_expected = float(median(all_leadings))
            for index, (block, leads) in enumerate(leadings_by_block):
                if not leads:
                    continue
                window_leads = [
                    lead
                    for j, (_, other) in enumerate(leadings_by_block)
                    if j != index and abs(j - index) <= _LOCAL_WINDOW
                    for lead in other
                ]
                expected = float(median(window_leads)) if window_leads else page_expected
                worst = max(leads, key=lambda lead: abs(lead - expected))
                signed = worst - expected
                if abs(signed) < LINE_SPACING_THRESHOLD_PT:
                    continue
                matching = sum(
                    1
                    for lead in all_leadings
                    if abs(lead - expected) < LINE_SPACING_THRESHOLD_PT
                )
                anomalies.append(
                    _anomaly(
                        "Spacing",
                        block,
                        detected=_pt(worst),
                        expected=_pt(expected),
                        baseline=(
                            f"{_pct(matching, len(all_leadings))} of line gaps on this page "
                            f"are ≈{_pt(expected)}"
                        ),
                        deviation=_signed_pt(signed),
                        confidence=_delta_confidence(
                            abs(signed),
                            LINE_SPACING_THRESHOLD_PT,
                            matching / len(all_leadings),
                        ),
                        evidence={
                            "kind": "line-spacing",
                            "detected_leading_pt": round(worst, 2),
                            "expected_leading_pt": round(expected, 2),
                            "leading_delta_pt": round(signed, 2),
                            "block_median_leading_pt": round(float(median(leads)), 2),
                            "comparable_count": len(all_leadings),
                            "matching_count": matching,
                        },
                        location=f"Line spacing in body paragraph near y={round(block.y0)}",
                    )
            )

    if not assessed_any:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Not enough intra-paragraph line gaps to assess line spacing"
            ),
        )
    return anomalies, _assessed()


def _check_paragraph_spacing(
    blocks: list[_Block],
) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    body = _body_blocks(blocks)
    if len(body) < 3:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Not enough consecutive body paragraphs to assess paragraph spacing "
                f"({len(body)} found, need at least 3)"
            ),
        )

    anomalies: list[LayoutAnomaly] = []
    by_page: dict[int, list[_Block]] = {}
    for block in body:
        by_page.setdefault(block.page, []).append(block)

    assessed_any = False
    for raw_blocks in by_page.values():
        raw_blocks = sorted(raw_blocks, key=lambda b: (b.y0, b.x0))
        columns = _page_columns(raw_blocks)
        if len(columns) >= 2:
            continue
        groups = [raw_blocks]
        for page_blocks in groups:
            page_blocks = sorted(page_blocks, key=lambda b: (b.y0, b.x0))
            if len(page_blocks) < 3:
                continue
            gaps: list[tuple[_Block, _Block, float]] = []
            for prev, curr in zip(page_blocks, page_blocks[1:]):
                gap = curr.y0 - prev.y1
                if gap < 0:
                    continue
                gaps.append((prev, curr, gap))
            if len(gaps) < 2:
                continue
            assessed_any = True
            typical = float(median(gap for _, _, gap in gaps))
            section_cut = max(typical * _PARA_SECTION_BREAK_MULT, PARA_SPACING_THRESHOLD_PT * 2)
            comparable = [(prev, curr, gap) for prev, curr, gap in gaps if gap <= section_cut]
            if len(comparable) < 2:
                continue
            expected_page = float(median(gap for _, _, gap in comparable))
            for index, (prev, curr, gap) in enumerate(comparable):
                window = [
                    g
                    for j, (_, _, g) in enumerate(comparable)
                    if j != index and abs(j - index) <= _LOCAL_WINDOW
                ]
                expected = float(median(window)) if window else expected_page
                signed = gap - expected
                if abs(signed) < PARA_SPACING_THRESHOLD_PT:
                    continue
                matching = sum(
                    1 for _, _, g in comparable if abs(g - expected) < PARA_SPACING_THRESHOLD_PT
                )
                space = "before" if signed > 0 else "after"
                anomalies.append(
                    _anomaly(
                        "Spacing",
                        curr,
                        detected=_pt(gap),
                        expected=_pt(expected),
                        baseline=(
                            f"{_pct(matching, len(comparable))} of paragraph gaps on this page "
                            f"are ≈{_pt(expected)}"
                        ),
                        deviation=_signed_pt(signed),
                        confidence=_delta_confidence(
                            abs(signed),
                            PARA_SPACING_THRESHOLD_PT,
                            matching / len(comparable),
                        ),
                        evidence={
                            "kind": "paragraph-spacing",
                            "space": "before" if signed > 0 else "after",
                            "gap_before_pt": round(gap, 2),
                            "gap_after_prev_pt": round(gap, 2),
                            "expected_gap_pt": round(expected, 2),
                            "gap_delta_pt": round(signed, 2),
                            "comparable_count": len(comparable),
                            "matching_count": matching,
                            "previous_text": _snippet(prev.text, 40),
                        },
                        location=(
                            f"Paragraph spacing near y={round(curr.y0)} "
                            f"(space {space} '{_snippet(curr.text, 40)}')"
                        ),
                    )
                )

    if not assessed_any:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Not enough consecutive body paragraphs on any page to assess paragraph spacing"
            ),
        )
    return anomalies, _assessed()


def _merge_spacing_assessment(
    line_check: CheckAssessment, para_check: CheckAssessment
) -> CheckAssessment:
    if not line_check.unable_to_assess or not para_check.unable_to_assess:
        return _assessed()
    reason = line_check.unable_to_assess_reason or para_check.unable_to_assess_reason
    return CheckAssessment(unable_to_assess=True, unable_to_assess_reason=reason)


_PRIMARY_RANK = {
    "centre-shift": 0,
    "row-alignment": 1,
    "alignment": 2,
    "right-edge": 3,
    "indentation": 4,
    "line-spacing": 5,
    "paragraph-spacing": 6,
}


def _primary_rank(anomaly: LayoutAnomaly) -> int:
    kind = _signal_kind(anomaly)
    if kind.endswith("-position"):
        return 7
    if kind.endswith("-margin"):
        return 8
    return _PRIMARY_RANK.get(kind, 9)


def _signal_kind(anomaly: LayoutAnomaly) -> str:
    ev = anomaly.evidence
    if ev.get("kind"):
        return str(ev["kind"])
    if ev.get("axis") == "vertical":
        return "row-alignment"
    if anomaly.anomaly_type == "Indentation":
        return "indentation"
    if anomaly.anomaly_type == "Alignment" and ev.get("alignment"):
        return "alignment"
    if anomaly.anomaly_type == "Margin" and ev.get("side"):
        return f"{ev['side']}-margin"
    return anomaly.anomaly_type.lower()


def _signal_record(anomaly: LayoutAnomaly) -> dict:
    ev = {
        k: v
        for k, v in anomaly.evidence.items()
        if k not in {"signals", "signal_count", "local_pattern", "consecutive_run"}
    }
    kind = _signal_kind(anomaly)
    return {
        "kind": kind,
        "anomaly_type": anomaly.anomaly_type,
        "detected": anomaly.detected,
        "expected": anomaly.expected,
        "deviation": anomaly.deviation,
        "confidence": anomaly.confidence,
        **ev,
    }


def _readable_check(signal: dict) -> dict:
    """One check, without the numeric fields that repeat detected/expected."""
    check = {
        "check": str(signal.get("kind") or signal.get("anomaly_type") or "check").replace("-", " "),
        "detected": signal.get("detected"),
        "expected": signal.get("expected"),
        "difference": signal.get("deviation"),
    }
    comparable = signal.get("comparable_count")
    matching = signal.get("matching_count")
    if isinstance(comparable, int) and isinstance(matching, int):
        check["matches"] = f"{matching} of {comparable}"
    pages = signal.get("pages_analysed")
    agreeing = signal.get("pages_matching_baseline")
    if isinstance(pages, int) and isinstance(agreeing, int):
        check["matches"] = f"{agreeing} of {pages} pages"
    return {key: value for key, value in check.items() if value is not None}


def _coalesce_signals(anomalies: list[LayoutAnomaly]) -> list[LayoutAnomaly]:
    """
    Several checks on the same paragraph (indent + right-edge + spacing)
    become one finding with evidence.signals, not three anomaly rows.
    Consecutive runs stay as separate findings; they are enrichment only.
    """
    groups: dict[tuple[int, BBox], list[LayoutAnomaly]] = {}
    order: list[tuple[int, BBox]] = []
    for anomaly in anomalies:
        key = (anomaly.page, anomaly.bbox)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(anomaly)

    merged: list[LayoutAnomaly] = []
    for key in order:
        items = groups[key]
        signals = [_signal_record(item) for item in items]
        primary = min(
            items,
            key=lambda a: (_primary_rank(a), -a.confidence),
        )
        primary.evidence["signals"] = signals
        primary.evidence["signal_count"] = len(signals)
        if "kind" not in primary.evidence:
            primary.evidence["kind"] = _signal_kind(primary)
        if len(items) == 1:
            merged.append(primary)
            continue
        kinds = [s["kind"] for s in signals]
        primary.detected = "\n".join(
            f"{s['kind']}: {s['deviation']}" for s in signals
        )
        primary.expected = "\n".join(
            f"{s['kind']}: {s['expected']}" for s in signals
        )
        primary.deviation = "\n".join(
            f"{s['kind']}: {s['deviation']}" for s in signals
        )
        primary.confidence = round(
            min(1.0, max(item.confidence for item in items) + 0.06 * (len(items) - 1)),
            2,
        )
        if _signal_kind(primary) in {
            "indentation",
            "right-edge",
            "alignment",
            "line-spacing",
            "paragraph-spacing",
        }:
            primary.location = f"Paragraph near y={round(primary.bbox[1])}"
        elif all(kind.endswith("-margin") for kind in kinds):
            primary.location = "Page margins"
        primary.baseline = (
            f"{len(signals)} signals on this block (" + ", ".join(kinds) + ")"
        )
        primary.summary = _format_summary(
            primary.anomaly_type,
            primary.evidence,
            deviation=primary.deviation,
        )
        merged.append(primary)
    return merged


def _mark_local_consecutive(anomalies: list[LayoutAnomaly]) -> None:
    """
    Adjacent flagged full-width clauses on a page are a stronger insertion
    signal than one stray line. Short titles and cover lines do not count.
    Enrich those findings; do not emit a new type.
    """
    by_page: dict[int, list[LayoutAnomaly]] = {}
    for anomaly in anomalies:
        if not _is_clause_stretch_finding(anomaly):
            continue
        by_page.setdefault(anomaly.page, []).append(anomaly)

    for group in by_page.values():
        ordered = sorted(group, key=lambda a: a.bbox[1])
        run: list[LayoutAnomaly] = []

        def _flush(items: list[LayoutAnomaly]) -> None:
            if len(items) < 2:
                return
            n = len(items)
            note = f"{n} consecutive anomalous paragraphs"
            for item in items:
                item.evidence["local_pattern"] = True
                item.evidence["consecutive_run"] = n
                item.confidence = round(min(1.0, item.confidence + 0.08), 2)
                if note not in item.location:
                    item.location = f"{item.location} ({note})"
                item.summary = _format_summary(
                    item.anomaly_type,
                    item.evidence,
                    deviation=item.deviation,
                )

        for anomaly in ordered:
            if not run:
                run = [anomaly]
                continue
            prev_bottom = run[-1].bbox[3]
            gap = anomaly.bbox[1] - prev_bottom
            if gap <= 80:
                run.append(anomaly)
            else:
                _flush(run)
                run = [anomaly]
        _flush(run)


def _is_paragraph_finding(anomaly: LayoutAnomaly) -> bool:
    kind = _signal_kind(anomaly)
    if kind.endswith("-margin") or kind.endswith("-position"):
        return False
    if kind == "centre-shift":
        return False
    return bool(anomaly.text)


def _is_clause_stretch_finding(anomaly: LayoutAnomaly) -> bool:
    if not _is_paragraph_finding(anomaly):
        return False
    width = anomaly.bbox[2] - anomaly.bbox[0]
    return width >= _CLAUSE_STRETCH_MIN_WIDTH_PT


def _check_alignment(blocks: list[_Block]) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    body = _body_blocks(blocks)
    if len(body) < 2:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Not enough comparable lines to assess alignment "
                f"({len(body)} found, need at least 2)"
            ),
        )

    anomalies: list[LayoutAnomaly] = []
    by_page: dict[int, list[_Block]] = {}
    for block in body:
        by_page.setdefault(block.page, []).append(block)

    classified_any = False
    for raw_blocks in by_page.values():
        raw_blocks = sorted(raw_blocks, key=lambda b: (b.y0, b.x0))
        columns = _page_columns(raw_blocks)
        groups = (
            _column_groups(raw_blocks, columns) if len(columns) >= 2 else [raw_blocks]
        )
        for page_blocks in groups:
            column = _content_column(page_blocks)
            if column is None:
                continue
            content_left, content_right = column

            labelled: list[tuple[_Block, Alignment, Literal["full", "short"]]] = []
            for block in page_blocks:
                alignment = _classify_block_alignment(block, content_left, content_right)
                if alignment is None:
                    continue
                labelled.append((block, alignment, _width_band(block, content_left, content_right)))

            if not labelled:
                continue

            full_peers = [(block, alignment) for block, alignment, band in labelled if band == "full"]
            short_peers = [(block, alignment) for block, alignment, band in labelled if band == "short"]
            full_expected = _dominant(alignment for _, alignment in full_peers) if len(full_peers) >= 2 else None

            for band, peers in (("full", full_peers), ("short", short_peers)):
                if len(peers) < 2:
                    continue
                classified_any = True
                expected = _dominant(alignment for _, alignment in peers)
                if expected is None:
                    continue
                matching = sum(1 for _, a in peers if a == expected)
                if not _has_strong_majority(matching, len(peers)):
                    continue
                for block, alignment in peers:
                    if alignment == expected:
                        continue
                    if _alignment_mismatch_unreliable(
                        block, alignment, expected, content_left
                    ):
                        continue
                    anomalies.append(
                        _alignment_anomaly(block, alignment, expected, peers)
                    )

            # A centred/right-aligned insertion among full-width body copy is
            # often itself short (its lines don't fill the column), so it
            # never enters the full-band comparison above. Flag it against
            # the full-band majority when it looks like a paragraph, not a
            # one-line heading or date.
            if full_expected is not None and _has_strong_majority(
                sum(1 for _, a in full_peers if a == full_expected),
                len(full_peers),
            ):
                classified_any = True
                already = {(a.page, a.bbox) for a in anomalies}
                for block, alignment in short_peers:
                    if alignment == full_expected:
                        continue
                    if _alignment_mismatch_unreliable(
                        block, alignment, full_expected, content_left
                    ):
                        continue
                    if not (len(block.lines) >= 2 or block.char_count >= 80):
                        continue
                    key = (block.page + 1, tuple(round(v, 2) for v in block.bbox))
                    if key in already:
                        continue
                    anomalies.append(
                        _alignment_anomaly(block, alignment, full_expected, full_peers)
                    )

    if not classified_any:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Could not reliably classify horizontal alignment of comparable paragraphs"
            ),
        )
    return anomalies, _assessed()


def _check_row_alignment(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """
    Horizontal alignment (left/centre/right/justified) does not catch a
    right-hand amount sitting higher or lower than its left-hand label.
    Pair each body/table block with the nearest item to its right on the
    same visual row and compare tops and baselines directly (not against
    a cluster median, which halves a two-item gap).
    """
    candidates = [
        b
        for b in blocks
        if b.role in ("body", "table") and b.height >= 2.0 and b.text.strip()
    ]
    by_group: dict[tuple[int, str], list[_Block]] = {}
    for block in candidates:
        by_group.setdefault((block.page, block.role), []).append(block)

    anomalies: list[LayoutAnomaly] = []
    seen: set[tuple[int, BBox, BBox]] = set()
    for group_blocks in by_group.values():
        measured: list[tuple[_Block, _Block, tuple[float, float]]] = []
        for left, right in _nearest_row_pairs(group_blocks):
            deltas = _vertical_misalignment(left, right)
            if deltas is None:
                continue
            measured.append((left, right, deltas))
        rhythms = _row_offset_rhythms([deltas for _, _, deltas in measured])
        for left, right, (top_delta, baseline_delta) in measured:
            key = (right.page, left.bbox, right.bbox)
            if key in seen:
                continue
            seen.add(key)
            primary = (
                top_delta if abs(top_delta) >= abs(baseline_delta) else baseline_delta
            )
            # The same stagger on many rows is how a statement's columns sit,
            # not one amount shoved off its label.
            if _matches_row_rhythm(primary, rhythms):
                continue
            location = (
                f"Same-row pair near y={round(left.y0)} "
                f"({_snippet(right.text, 40)} vs {_snippet(left.text, 40)})"
            )
            confidence = _delta_confidence(abs(primary), ROW_ALIGN_THRESHOLD_PT, 1.0)
            evidence = {
                "axis": "vertical",
                "detected_top_pt": round(right.y0, 2),
                "expected_top_pt": round(left.y0, 2),
                "detected_baseline_pt": round(right.y1, 2),
                "expected_baseline_pt": round(left.y1, 2),
                "top_delta_pt": round(top_delta, 2),
                "baseline_delta_pt": round(baseline_delta, 2),
                "row_spread_top_pt": round(abs(top_delta), 2),
                "row_count": 2,
            }
            anomalies.append(
                LayoutAnomaly(
                    anomaly_type="Alignment",
                    page=right.page + 1,
                    location=location,
                    bbox=tuple(round(v, 2) for v in right.bbox),  # type: ignore[arg-type]
                    text=_snippet(right.text, _TEXT_SNIPPET_CHARS),
                    detected=f"top {_pt(right.y0)}, baseline {_pt(right.y1)}",
                    expected=f"top {_pt(left.y0)}, baseline {_pt(left.y1)}",
                    baseline=(
                        f"Left-hand '{_snippet(left.text, 40)}' "
                        f"at top {_pt(left.y0)}"
                    ),
                    deviation=_signed_pt(primary),
                    confidence=confidence,
                    evidence=evidence,
                    summary=_format_summary(
                        "Alignment", evidence, deviation=_signed_pt(primary)
                    ),
                )
            )
    return anomalies


def _row_offset_rhythms(deltas: list[tuple[float, float]]) -> list[float]:
    """Offsets shared by three or more rows are the column's usual stagger."""
    if len(deltas) < 3:
        return []
    primaries = [
        top if abs(top) >= abs(base) else base
        for top, base in deltas
    ]
    clusters: list[list[float]] = []
    for value in sorted(primaries):
        if clusters and abs(value - clusters[-1][0]) < ROW_ALIGN_THRESHOLD_PT:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [float(median(cluster)) for cluster in clusters if len(cluster) >= 3]


def _matches_row_rhythm(value: float, rhythms: list[float]) -> bool:
    return any(abs(value - rhythm) < ROW_ALIGN_THRESHOLD_PT for rhythm in rhythms)


def _nearest_row_pairs(blocks: list[_Block]) -> list[tuple[_Block, _Block]]:
    """Each block pairs with the closest item to its right on the same row."""
    pairs: list[tuple[_Block, _Block]] = []
    for left in blocks:
        partners = [
            b
            for b in blocks
            if b is not left
            and b.x0 >= left.x0
            and _side_by_side(left, b)
            and _same_visual_row(left, b)
        ]
        if not partners:
            continue
        right = min(partners, key=lambda b: (_mid_y_gap(left, b), b.x0))
        if right.x0 == left.x0:
            continue
        pairs.append((left, right))
    return pairs


def _vertical_misalignment(
    left: _Block, right: _Block
) -> tuple[float, float] | None:
    top_delta = right.y0 - left.y0
    baseline_delta = right.y1 - left.y1
    similar_height = abs(right.height - left.height) <= max(
        4.0, 0.25 * max(left.height, right.height)
    )
    if similar_height:
        # Same-size label and amount should share both edges.
        if (
            abs(top_delta) >= ROW_ALIGN_THRESHOLD_PT
            or abs(baseline_delta) >= ROW_ALIGN_THRESHOLD_PT
        ):
            return top_delta, baseline_delta
        return None
    # Wrapping label next to a one-line amount: sharing a top or a
    # baseline is enough; tamper is neither edge lining up.
    if (
        abs(top_delta) >= ROW_ALIGN_THRESHOLD_PT
        and abs(baseline_delta) >= ROW_ALIGN_THRESHOLD_PT
    ):
        return top_delta, baseline_delta
    return None


def _mid_y(block: _Block) -> float:
    return (block.y0 + block.y1) / 2


def _mid_y_gap(a: _Block, b: _Block) -> float:
    return abs(_mid_y(a) - _mid_y(b))


def _same_visual_row(a: _Block, b: _Block) -> bool:
    """Side-by-side items occupying the same horizontal band."""
    overlap = min(a.y1, b.y1) - max(a.y0, b.y0)
    if overlap > 0:
        return True
    # Midpoints within about a line-height: a shoved amount with no
    # box overlap. A column header like "Total ($)" sits a full row
    # above the figure and stays out.
    line = max(min(a.height, b.height), ROW_ALIGN_THRESHOLD_PT)
    return _mid_y_gap(a, b) <= line * 1.1


def _side_by_side(a: _Block, b: _Block) -> bool:
    x_overlap = min(a.x1, b.x1) - max(a.x0, b.x0)
    if x_overlap <= 0:
        return True
    min_w = min(a.width, b.width)
    if min_w > 0 and x_overlap / min_w < 0.4:
        return True
    # Full-width line vs a short amount sitting on its right: the amount
    # sits inside the line's x-range, but they are still a label/value pair.
    left, right = (a, b) if a.x0 <= b.x0 else (b, a)
    return right.x0 >= (left.x0 + left.x1) / 2


def _check_centre_shift(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """
    Cover/title pages stack short centred headings on one vertical axis.
    A heading whose midpoint is off that axis (company name shoved right)
    is not caught by left/centre/right/justified, because the shifted
    line often becomes the content-column edge and is labelled justified.
    """
    by_page: dict[int, list[_Block]] = {}
    for block in blocks:
        if block.role not in ("body", "cover") or not block.text.strip():
            continue
        by_page.setdefault(block.page, []).append(block)

    anomalies: list[LayoutAnomaly] = []
    for page_blocks in by_page.values():
        covers = [b for b in page_blocks if b.role == "cover"]
        group = covers if len(covers) >= 4 else page_blocks
        if len(covers) < 4 and not _is_title_page(group):
            continue
        page_blocks = group
        mids = [_mid_x(b) for b in page_blocks]
        bucketed = [_bucket_pt(mid, _CENTER_MID_BUCKET_PT) for mid in mids]
        expected_mid = _dominant(bucketed)
        if expected_mid is None:
            continue
        matching = sum(
            1 for mid in mids if abs(mid - float(expected_mid)) < CENTER_SHIFT_THRESHOLD_PT
        )
        if matching < 3:
            continue
        expected_mid_f = float(expected_mid)
        for block, mid in zip(page_blocks, mids):
            delta = mid - expected_mid_f
            if abs(delta) < CENTER_SHIFT_THRESHOLD_PT:
                continue
            location = f"Cover title near y={round(block.y0)} ({_snippet(block.text, 40)})"
            confidence = _delta_confidence(
                abs(delta), CENTER_SHIFT_THRESHOLD_PT, matching / len(page_blocks)
            )
            evidence = {
                "axis": "horizontal",
                "kind": "centre-shift",
                "detected_mid_pt": round(mid, 2),
                "expected_mid_pt": round(expected_mid_f, 2),
                "mid_delta_pt": round(delta, 2),
                "comparable_count": len(page_blocks),
                "matching_count": matching,
            }
            anomalies.append(
                LayoutAnomaly(
                    anomaly_type="Alignment",
                    page=block.page + 1,
                    location=location,
                    bbox=tuple(round(v, 2) for v in block.bbox),  # type: ignore[arg-type]
                    text=_snippet(block.text, _TEXT_SNIPPET_CHARS),
                    detected=f"centre {_pt(mid)}",
                    expected=f"centre {_pt(expected_mid_f)}",
                    baseline=(
                        f"{matching} of {len(page_blocks)} cover titles "
                        f"share a centre at {_pt(expected_mid_f)}"
                    ),
                    deviation=_signed_pt(delta),
                    confidence=confidence,
                    evidence=evidence,
                    summary=_format_summary(
                        "Alignment", evidence, deviation=_signed_pt(delta)
                    ),
                )
            )
    return anomalies


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


def _mid_x(block: _Block) -> float:
    return (block.x0 + block.x1) / 2


def _snippet(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _classify_block_alignment(
    block: _Block, content_left: float, content_right: float
) -> Alignment | None:
    column_width = content_right - content_left
    if column_width <= 0:
        return None

    lines = _alignment_lines(block, content_left, content_right)
    if not lines:
        return None

    if len(lines) == 1:
        return _classify_line_alignment(lines[0].bbox, content_left, content_right)

    lefts = [line.bbox[0] for line in lines]
    rights = [line.bbox[2] for line in lines]
    left_spread = max(lefts) - min(lefts)
    right_spread = max(rights) - min(rights)
    body_lines = lines[:-1]
    union_width = max(rights) - min(lefts)

    left_flush = abs(min(lefts) - content_left) <= ALIGN_EDGE_TOLERANCE_PT
    body_right_flush = all(
        abs(line.bbox[2] - content_right) <= ALIGN_EDGE_TOLERANCE_PT for line in body_lines
    )
    fill_ratio = union_width / column_width

    # Justified is left-AND-right flush to the page's content column —
    # internally-aligned left edges that sit inset from the column are an
    # indented left-aligned paragraph, not justification.
    if (
        left_flush
        and left_spread <= ALIGN_EDGE_TOLERANCE_PT
        and body_right_flush
        and fill_ratio >= JUSTIFY_FILL_RATIO
    ):
        return "justified"
    if left_spread <= ALIGN_EDGE_TOLERANCE_PT:
        return "left-aligned"
    if right_spread <= ALIGN_EDGE_TOLERANCE_PT and all(
        abs(line.bbox[2] - content_right) <= ALIGN_EDGE_TOLERANCE_PT for line in lines
    ):
        return "right-aligned"

    leftovers = [
        (line.bbox[0] - content_left, content_right - line.bbox[2]) for line in lines
    ]
    if leftovers and all(
        abs(left - right) <= CENTER_SYMMETRY_TOLERANCE_PT
        and left > ALIGN_EDGE_TOLERANCE_PT
        and right > ALIGN_EDGE_TOLERANCE_PT
        for left, right in leftovers
    ):
        return "centred"

    # Fall back to the block as a whole (union bbox) when per-line signals
    # disagree — a wrapped left-aligned paragraph with a ragged right edge.
    return _classify_line_alignment(block.bbox, content_left, content_right)


def _classify_line_alignment(
    bbox: BBox, content_left: float, content_right: float
) -> Alignment | None:
    x0, _, x1, _ = bbox
    column_width = content_right - content_left
    if column_width <= 0:
        return None
    leftover_left = x0 - content_left
    leftover_right = content_right - x1
    line_width = x1 - x0
    fill_ratio = line_width / column_width

    if (
        fill_ratio >= JUSTIFY_FILL_RATIO
        and leftover_left <= ALIGN_EDGE_TOLERANCE_PT
        and leftover_right <= ALIGN_EDGE_TOLERANCE_PT
    ):
        return "justified"
    if leftover_left <= ALIGN_EDGE_TOLERANCE_PT and leftover_right > leftover_left + ALIGN_EDGE_TOLERANCE_PT:
        return "left-aligned"
    if leftover_right <= ALIGN_EDGE_TOLERANCE_PT and leftover_left > leftover_right + ALIGN_EDGE_TOLERANCE_PT:
        return "right-aligned"
    if (
        abs(leftover_left - leftover_right) <= CENTER_SYMMETRY_TOLERANCE_PT
        and leftover_left > ALIGN_EDGE_TOLERANCE_PT
        and leftover_right > ALIGN_EDGE_TOLERANCE_PT
    ):
        return "centred"
    if leftover_left <= leftover_right:
        return "left-aligned"
    return "right-aligned"


def _is_short_or_heading_line(line: _Line, column_width: float) -> bool:
    if column_width > 0 and (line.bbox[2] - line.bbox[0]) / column_width < SHORT_WIDTH_RATIO:
        return True
    return bool(_CLAUSE_HEADING_RE.match(line.text.strip()))


def _alignment_lines(
    block: _Block, content_left: float, content_right: float
) -> list[_Line]:
    """Drop a short clause heading sitting on top of the paragraph it labels."""
    lines = list(block.lines)
    column_width = content_right - content_left
    while len(lines) >= 2 and _is_short_or_heading_line(lines[0], column_width):
        lines = lines[1:]
    return lines


def _alignment_mismatch_unreliable(
    block: _Block,
    alignment: Alignment,
    expected: Alignment,
    content_left: float,
) -> bool:
    """
    Alignment here means where the box sits, not how far the text fills.

    Left vs justified is a fill-ratio difference (ragged vs flush right)
    when both start on the same column edge. Left vs right-aligned is only
    a placement change when the box itself has moved off that edge.
    """
    pair = {alignment, expected}
    if pair == {"left-aligned", "justified"}:
        return True
    left_flush = abs(block.x0 - content_left) <= ALIGN_EDGE_TOLERANCE_PT
    return pair == {"left-aligned", "right-aligned"} and left_flush


def _check_margins(
    page_margins: list[PageMargins],
    blocks: list[_Block] | None = None,
) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    if len(page_margins) < 2:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Need at least 2 pages with extractable text to establish a margin pattern "
                f"({len(page_margins)} found)"
            ),
        )

    title_pages = _title_page_numbers(blocks) if blocks else set()
    inner = [p for p in page_margins if p.page not in title_pages]
    titles = [p for p in page_margins if p.page in title_pages]
    anomalies: list[LayoutAnomaly] = []
    if len(inner) >= 2:
        anomalies.extend(_margin_anomalies_for(inner))
    if len(titles) >= 2:
        anomalies.extend(_margin_anomalies_for(titles))
    return anomalies, _assessed()


def _title_page_numbers(blocks: list[_Block]) -> set[int]:
    by_page: dict[int, list[_Block]] = {}
    for block in blocks:
        if block.role in {"body", "cover", "heading"}:
            by_page.setdefault(block.page, []).append(block)
    return {page + 1 for page, items in by_page.items() if _is_title_page(items)}


def _margin_anomalies_for(page_margins: list[PageMargins]) -> list[LayoutAnomaly]:
    expected = _expected_margins(page_margins)
    anomalies: list[LayoutAnomaly] = []
    for record in page_margins:
        page_expected = expected[record.page]
        detected_margins = {
            "left": record.left,
            "right": record.right,
            "top": record.top,
            "bottom": record.bottom,
        }
        for side in ("left", "right", "top", "bottom"):
            delta = detected_margins[side] - page_expected[side]
            if abs(delta) < MARGIN_THRESHOLD_PT:
                continue
            agreeing = sum(
                1
                for page in page_margins
                if abs(getattr(page, side) - page_expected[side]) < MARGIN_THRESHOLD_PT
            )
            majority_frac = agreeing / len(page_margins) if page_margins else 0.0
            anomalies.append(
                LayoutAnomaly(
                    anomaly_type="Margin",
                    page=record.page,
                    location=f"{side.capitalize()} page margin",
                    bbox=(
                        record.left,
                        record.top,
                        record.page_width - record.right,
                        record.page_height - record.bottom,
                    ),
                    text="",
                    detected=_pt(detected_margins[side]),
                    expected=_pt(page_expected[side]),
                    baseline=(
                        f"{agreeing} of {len(page_margins)} pages have a {side} "
                        f"margin of ≈{_pt(page_expected[side])}"
                    ),
                    deviation=_signed_pt(delta),
                    confidence=_delta_confidence(
                        abs(delta), MARGIN_THRESHOLD_PT, majority_frac
                    ),
                    evidence={
                        "side": side,
                        "detected_pt": detected_margins[side],
                        "expected_pt": page_expected[side],
                        "delta_pt": round(delta, 2),
                        "pages_analysed": len(page_margins),
                        "pages_matching_baseline": agreeing,
                    },
                    summary=_format_summary(
                        "Margin",
                        {
                            "side": side,
                            "delta_pt": round(delta, 2),
                        },
                        deviation=_signed_pt(delta),
                    ),
                )
            )
    return anomalies


def _check_chrome_position(
    blocks: list[_Block],
) -> tuple[list[LayoutAnomaly], CheckAssessment]:
    """
    Compare running header/footer y-position across pages. Uses the
    topmost header-band block and the bottommost footer-band block so a
    clause title that happens to sit in the header strip is only the
    chrome anchor when it is the highest line — and pages are only
    compared if they actually have that chrome.
    """
    header_anchors = _chrome_anchors(blocks, "header")
    footer_anchors = _chrome_anchors(blocks, "footer")
    anomalies: list[LayoutAnomaly] = []
    header_ok = _flag_chrome_shift(header_anchors, "header", anomalies)
    footer_ok = _flag_chrome_shift(footer_anchors, "footer", anomalies)
    if not header_ok and not footer_ok:
        return [], CheckAssessment(
            unable_to_assess=True,
            unable_to_assess_reason=(
                "Need at least 2 pages with a running header or footer "
                "to compare chrome position"
            ),
        )
    return anomalies, _assessed()


def _chrome_anchors(
    blocks: list[_Block], role: Literal["header", "footer"]
) -> dict[int, _Block]:
    by_page: dict[int, list[_Block]] = {}
    for block in blocks:
        if block.role != role:
            continue
        by_page.setdefault(block.page, []).append(block)
    anchors: dict[int, _Block] = {}
    for page, items in by_page.items():
        if role == "header":
            anchors[page] = min(items, key=lambda b: b.y0)
        else:
            anchors[page] = max(items, key=lambda b: b.y1)
    return anchors


def _flag_chrome_shift(
    anchors: dict[int, _Block],
    band: Literal["header", "footer"],
    anomalies: list[LayoutAnomaly],
) -> bool:
    if len(anchors) < 2:
        return False
    ys = [block.y0 for block in anchors.values()]
    bucketed = [_bucket_pt(y, 2.0) for y in ys]
    expected = _dominant(bucketed)
    if expected is None:
        expected = float(median(ys))
    expected_f = float(expected)
    matching = sum(1 for y in ys if abs(y - expected_f) < CHROME_POS_THRESHOLD_PT)
    if matching < 2:
        return True
    label = "Header" if band == "header" else "Footer"
    for block in anchors.values():
        signed = block.y0 - expected_f
        if abs(signed) < CHROME_POS_THRESHOLD_PT:
            continue
        location = f"{label} vertical position"
        confidence = _delta_confidence(
            abs(signed), CHROME_POS_THRESHOLD_PT, matching / len(anchors)
        )
        evidence = {
            "kind": f"{band}-position",
            "detected_y0_pt": round(block.y0, 2),
            "expected_y0_pt": round(expected_f, 2),
            "delta_pt": round(signed, 2),
            "pages_analysed": len(anchors),
            "pages_matching_baseline": matching,
        }
        anomalies.append(
            LayoutAnomaly(
                anomaly_type="Margin",
                page=block.page + 1,
                location=location,
                bbox=tuple(round(v, 2) for v in block.bbox),  # type: ignore[arg-type]
                text=_snippet(block.text, _TEXT_SNIPPET_CHARS),
                detected=_pt(block.y0),
                expected=_pt(expected_f),
                baseline=(
                    f"{matching} of {len(anchors)} pages have a {band} "
                    f"at y≈{_pt(expected_f)}"
                ),
                deviation=_signed_pt(signed),
                confidence=confidence,
                evidence=evidence,
                summary=_format_summary(
                    "Margin", evidence, deviation=_signed_pt(signed)
                ),
            )
        )
    return True


def _expected_margins(page_margins: list[PageMargins]) -> dict[int, dict[str, float]]:
    """
    Established pattern: median of all pages, unless there are enough
    even/odd pages to support a facing-page (inner/outer) convention, in
    which case odd pages are compared to the odd median and even to even.
    """
    use_facing = len(page_margins) >= 4
    odds = [p for p in page_margins if p.page % 2 == 1]
    evens = [p for p in page_margins if p.page % 2 == 0]
    if use_facing and len(odds) >= 2 and len(evens) >= 2:
        odd_med = _median_margins(odds)
        even_med = _median_margins(evens)
        facing_differs = any(
            abs(odd_med[side] - even_med[side]) >= MARGIN_THRESHOLD_PT
            for side in ("left", "right")
        )
        if facing_differs:
            return {
                p.page: odd_med if p.page % 2 == 1 else even_med for p in page_margins
            }

    overall = _median_margins(page_margins)
    return {p.page: overall for p in page_margins}


def _median_margins(pages: list[PageMargins]) -> dict[str, float]:
    return {
        "left": round(float(median(p.left for p in pages)), 2),
        "right": round(float(median(p.right for p in pages)), 2),
        "top": round(float(median(p.top for p in pages)), 2),
        "bottom": round(float(median(p.bottom for p in pages)), 2),
    }


def _alignment_anomaly(
    block: _Block,
    detected: Alignment,
    expected: Alignment,
    peers: list[tuple[_Block, Alignment]],
) -> LayoutAnomaly:
    matching = sum(1 for _, alignment in peers if alignment == expected)
    comparable = len(peers) or 1
    expected_label = _ALIGNMENT_LABEL[expected]
    return _anomaly(
        "Alignment",
        block,
        detected=_ALIGNMENT_LABEL[detected],
        expected=expected_label,
        baseline=(
            f"{_pct(matching, comparable)} of comparable body paragraphs "
            f"are {expected_label.lower()}"
        ),
        deviation="1 paragraph",
        confidence=_majority_confidence(matching, comparable),
        evidence={
            "alignment": detected,
            "expected_alignment": expected,
            "comparable_count": comparable,
            "matching_count": matching,
            "occurrence_count": 1,
        },
    )


def _anomaly(
    anomaly_type: AnomalyType,
    block: _Block,
    *,
    detected: str,
    expected: str,
    baseline: str | None,
    deviation: str,
    confidence: float,
    evidence: dict,
    location: str | None = None,
) -> LayoutAnomaly:
    snippet = (
        block.text
        if len(block.text) <= _TEXT_SNIPPET_CHARS
        else block.text[: _TEXT_SNIPPET_CHARS - 1] + "…"
    )
    location = location or _location_label(block)
    return LayoutAnomaly(
        anomaly_type=anomaly_type,
        page=block.page + 1,
        location=location,
        bbox=tuple(round(v, 2) for v in block.bbox),  # type: ignore[arg-type]
        text=snippet,
        detected=detected,
        expected=expected,
        baseline=baseline,
        deviation=deviation,
        confidence=confidence,
        evidence=evidence,
        summary=_format_summary(anomaly_type, evidence, deviation=deviation),
    )


_ALIGNMENT_LABEL: dict[Alignment, str] = {
    "left-aligned": "Left-aligned",
    "centred": "Centre-aligned",
    "right-aligned": "Right-aligned",
    "justified": "Justified",
}

_ROLE_LABEL = {
    "body": "Body paragraph",
    "header": "Header",
    "footer": "Footer",
    "table": "Table cell",
    "signature": "Signature block",
    "heading": "Clause heading",
    "cover": "Cover title",
}


def _location_label(block: _Block) -> str:
    role = _ROLE_LABEL.get(block.role, "Paragraph")
    return f"{role} near y={round(block.y0)}"


def _pt(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return f"{rounded} pt"
    return f"{round(value, 1)} pt"


def _signed_pt(delta: float) -> str:
    rounded = round(delta)
    if abs(delta - rounded) < 0.05:
        return f"{rounded:+d} pt"
    return f"{delta:+.1f} pt"


def _pct(matching: int, comparable: int) -> str:
    if comparable <= 0:
        return "0%"
    return f"{round(100 * matching / comparable)}%"


def _majority_confidence(matching: int, comparable: int) -> float:
    if comparable <= 0:
        return 0.5
    majority_frac = matching / comparable
    rarity = 1.0 - 1.0 / comparable
    return round(min(1.0, max(0.05, 0.45 * majority_frac + 0.35 * rarity + 0.2)), 2)


def _delta_confidence(delta_pt: float, threshold_pt: float, majority_frac: float) -> float:
    strength = min(1.0, max(0.0, (abs(delta_pt) - threshold_pt) / max(threshold_pt, 1.0)))
    return round(min(1.0, max(0.05, 0.4 * majority_frac + 0.4 * strength + 0.2)), 2)


def _format_summary(
    anomaly_type: AnomalyType,
    evidence: dict,
    *,
    deviation: str = "",
) -> str:
    """One sentence for a UI. Measurements stay in structured fields."""
    kind = _evidence_kind(anomaly_type, evidence)
    delta = _abs_pt_phrase(evidence, deviation)
    sentence = _summary_sentence(kind, evidence, delta)

    signals = evidence.get("signals") or []
    extra = [item for item in signals if item.get("kind") and item.get("kind") != kind]
    if extra:
        sentence += " Other layout measurements on this same area support the finding."

    run = evidence.get("consecutive_run")
    if evidence.get("local_pattern") and isinstance(run, int) and run >= 2:
        sentence += f" Similar issues appear across {run} consecutive paragraphs."
    return sentence


def _evidence_kind(anomaly_type: AnomalyType, evidence: dict) -> str:
    if evidence.get("kind"):
        return str(evidence["kind"])
    if evidence.get("axis") == "vertical":
        return "row-alignment"
    if anomaly_type == "Indentation":
        return "indentation"
    if anomaly_type == "Alignment" and evidence.get("alignment"):
        return "alignment"
    if anomaly_type == "Margin" and evidence.get("side"):
        return f"{evidence['side']}-margin"
    return anomaly_type.lower()


def _abs_pt_phrase(evidence: dict, deviation: str) -> str:
    for key in (
        "left_delta_pt",
        "right_delta_pt",
        "leading_delta_pt",
        "gap_delta_pt",
        "mid_delta_pt",
        "delta_pt",
        "top_delta_pt",
        "baseline_delta_pt",
    ):
        value = evidence.get(key)
        if isinstance(value, (int, float)):
            return _pt(abs(float(value)))
    first = (deviation or "").split("\n")[0].strip()
    if ": " in first:
        first = first.split(": ", 1)[1]
    cleaned = first.replace("+", "").replace("-", "").strip()
    return cleaned or "an unusual amount"


def _label_alignment(value: object) -> str:
    if value in _ALIGNMENT_LABEL:
        return _ALIGNMENT_LABEL[value]  # type: ignore[index]
    return str(value).replace("-", " ")


def _summary_sentence(kind: str, evidence: dict, delta: str) -> str:
    if kind == "indentation":
        signed = evidence.get("left_delta_pt")
        if isinstance(signed, (int, float)) and signed < 0:
            return (
                f"This paragraph starts {delta} further left than comparable "
                "body paragraphs."
            )
        return (
            f"This paragraph is indented {delta} further than comparable "
            "body paragraphs."
        )
    if kind == "right-edge":
        signed = evidence.get("right_delta_pt")
        if isinstance(signed, (int, float)) and signed < 0:
            return f"This paragraph ends {delta} short of the comparable column edge."
        return f"This paragraph extends {delta} past the comparable column edge."
    if kind == "alignment":
        detected = _label_alignment(evidence.get("alignment") or "differently aligned")
        expected = evidence.get("expected_alignment")
        if expected:
            return (
                f"This paragraph is {detected.lower()} while comparable body "
                f"paragraphs are {_label_alignment(expected).lower()}."
            )
        return f"This paragraph is {detected.lower()}, unlike comparable body paragraphs."
    if kind == "row-alignment":
        return (
            f"This label and amount on the same row are vertically offset by {delta}."
        )
    if kind == "centre-shift":
        return (
            f"This title is shifted {delta} off the shared centre axis of the "
            "cover titles."
        )
    if kind == "line-spacing":
        return (
            f"Line spacing in this paragraph differs from comparable body "
            f"paragraphs by {delta}."
        )
    if kind == "paragraph-spacing":
        where = evidence.get("space")
        if where in {"before", "after"}:
            return (
                f"The gap {where} this paragraph differs from the local rhythm "
                f"by {delta}."
            )
        return f"The gap around this paragraph differs from the local rhythm by {delta}."
    if kind.endswith("-margin"):
        side = evidence.get("side") or kind.removesuffix("-margin")
        return f"The {side} page margin differs from the document pattern by {delta}."
    if kind in {"header-position", "footer-position"}:
        band = "header" if kind.startswith("header") else "footer"
        return (
            f"The running {band} sits {delta} away from its position on other pages."
        )
    return f"This {kind.replace('-', ' ')} differs from comparable content by {delta}."


def _bucket_pt(value: float, bucket: float = _INDENT_BUCKET_PT) -> float:
    return round(value / bucket) * bucket


def _dominant(values) -> object | None:
    """
    Most common value, or None if there's a tie for first place — a tie
    means there's no real majority pattern to call "expected", so nothing
    should be flagged as deviating from one. Same rule as font_consistency.
    """
    counts = Counter(values).most_common()
    if not counts:
        return None
    if len(counts) > 1 and counts[0][1] == counts[1][1]:
        return None
    return counts[0][0]
