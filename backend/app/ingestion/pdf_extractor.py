import re
from dataclasses import dataclass
from typing import Iterable, cast

import pymupdf

# sort=True pads the gap between columns with spaces; a long run carries no
# meaning beyond "next column", and would otherwise inflate every LLM prompt.
_COLUMN_GAP_RE = re.compile(r"[ \t]{3,}")

# A shape or image painted over text hides it when it is at least this opaque.
_COVER_MIN_OPACITY = 0.99
# A page where most of the text sits under a later image is a scan with its
# OCR layer stored first, not a cover-up, so nothing on it is treated as hidden.
_MAX_HIDDEN_SHARE = 0.5
# Where letters sit, as a fraction of the font size (capitals above the
# baseline, descenders below). Font boxes add padding that would make stacked
# lines look like they overlap.
_CAP_HEIGHT = 0.72
_DESCENT = 0.18
# Two runs of text whose letters overlap by this share of the smaller one are
# drawn on top of each other.
_OVERLAP_MIN_RATIO = 0.20


@dataclass
class _Segment:
    """A run of visible characters from one span, in drawing order."""

    text: str
    x0: float
    x1: float
    baseline: float
    size: float
    font: str
    flags: int

    def band(self) -> tuple[float, float, float, float]:
        return (
            self.x0,
            self.baseline - _CAP_HEIGHT * self.size,
            self.x1,
            self.baseline + _DESCENT * self.size,
        )


def _covers(page: pymupdf.Page) -> list[tuple[int, pymupdf.Rect]]:
    """Opaque fills and images, with their place in the page's drawing order.

    Drawings, text spans and the bbox log share one sequence: a shape whose
    number is higher than a span's was painted after it.
    """
    covers = []
    for drawing in page.get_drawings():
        opacity = drawing.get("fill_opacity")
        if drawing.get("fill") is not None and (opacity is None or opacity >= _COVER_MIN_OPACITY):
            covers.append((drawing["seqno"], pymupdf.Rect(drawing["rect"])))

    # An image with a soft mask (a stamp, a logo with transparency) lets the
    # text under it show through.
    opaque_images = [
        rect
        for xref, smask, *_ in page.get_images(full=True)
        if not smask
        for rect in page.get_image_rects(xref)
    ]
    for seqno, (kind, box) in enumerate(page.get_bboxlog()):
        if kind != "fill-image":
            continue
        rect = pymupdf.Rect(box)
        if any(abs(rect.x0 - o.x0) < 1 and abs(rect.y0 - o.y0) < 1
               and abs(rect.x1 - o.x1) < 1 and abs(rect.y1 - o.y1) < 1 for o in opaque_images):
            covers.append((seqno, rect))
    return covers


def _visible_segments(page: pymupdf.Page) -> tuple[list[_Segment], int]:
    """Runs of text a reader can see, and how many characters are hidden.

    A character is hidden when an opaque shape or image painted after it
    covers its centre: the white box of a cover-up, with the new value
    typed on top.
    """
    covers = _covers(page)
    spans = page.get_texttrace()

    def build(use_covers: bool) -> tuple[list[_Segment], int, int]:
        segments: list[_Segment] = []
        hidden = total = 0
        for span in spans:
            later = [rect for seqno, rect in covers if seqno > span["seqno"]] if use_covers else []
            run: list = []

            def flush() -> None:
                text = "".join(chr(ch[0]) for ch in run)
                if text.strip():
                    segments.append(_Segment(
                        text=text,
                        x0=run[0][3][0],
                        x1=run[-1][3][2],
                        baseline=run[0][2][1],
                        size=float(span["size"]),
                        font=span.get("font") or "",
                        flags=int(span.get("flags") or 0),
                    ))
                run.clear()

            for ch in span["chars"]:
                total += 1
                bbox = ch[3]
                cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
                # A space under a form's checkbox is not hidden content.
                if not chr(ch[0]).isspace() and any(
                    r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in later
                ):
                    hidden += 1
                    flush()
                    continue
                run.append(ch)
            flush()
        return segments, hidden, total

    segments, hidden, total = build(use_covers=True)
    if hidden and hidden > total * _MAX_HIDDEN_SHARE:
        segments, hidden, _ = build(use_covers=False)
    return segments, hidden


def _overlap_ratio(a: _Segment, b: _Segment) -> float:
    ax0, ay0, ax1, ay1 = a.band()
    bx0, by0, bx1, by1 = b.band()
    width = min(ax1, bx1) - max(ax0, bx0)
    height = min(ay1, by1) - max(ay0, by0)
    if width <= 0 or height <= 0:
        return 0.0
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0))
    return width * height / smaller if smaller > 0 else 0.0


def _alnum_count(text: str) -> int:
    return sum(ch.isalnum() for ch in text)


def _has_overlapping_text(segments: list[_Segment]) -> bool:
    """Two runs with letters or digits drawn on top of each other. A lone
    punctuation mark kerned under a quote is typesetting, not a second value."""
    ordered = sorted((s for s in segments if _alnum_count(s.text) >= 2), key=lambda s: s.baseline)
    for i, a in enumerate(ordered):
        for b in ordered[i + 1:]:
            if b.baseline - a.baseline > (_CAP_HEIGHT + _DESCENT) * max(a.size, b.size):
                break
            if _overlap_ratio(a, b) >= _OVERLAP_MIN_RATIO:
                return True
    return False


def _reading_order_text(segments: list[_Segment]) -> str:
    """Rows top to bottom, each left to right. Text drawn over other text on
    the same row goes on its own row, so two values never interleave."""
    rows: list[list[_Segment]] = []
    for seg in sorted(segments, key=lambda s: (s.baseline, s.x0)):
        placed = False
        for row in reversed(rows):
            if seg.baseline - row[0].baseline > 0.3 * min(seg.size, row[0].size):
                break
            if not any(_overlap_ratio(seg, other) >= _OVERLAP_MIN_RATIO for other in row):
                row.append(seg)
                placed = True
                break
        if not placed:
            rows.append([seg])

    lines = []
    for row in rows:
        row.sort(key=lambda s: s.x0)
        text = row[0].text
        for prev, seg in zip(row, row[1:]):
            gap = seg.x0 - prev.x1
            if gap > 1.5 * seg.size:
                text += "   "
            elif gap > 0.15 * seg.size and not text.endswith(" ") and not seg.text.startswith(" "):
                text += " "
            text += seg.text
        lines.append(_COLUMN_GAP_RE.sub("   ", text).rstrip())
    return "\n".join(lines)


# A line whose writing direction is further than this from left-to-right
# (cos of the angle, about 8 degrees) is printed sideways.
_HORIZONTAL_MIN_COS = 0.99


def _sideways_lines(page: pymupdf.Page) -> list[str]:
    """Lines printed at an angle, e.g. a bank's "Co. Reg. No. ... GST Reg No: ..."
    running up the margin, each read in its own direction. Sorted text breaks
    such a line into single words stacked bottom to top, which strands a value
    away from its label."""
    lines = []
    for block in cast(dict, page.get_text("dict")).get("blocks", []):
        for line in block.get("lines", []):
            if abs(line["dir"][0]) >= _HORIZONTAL_MIN_COS:
                continue
            text = _COLUMN_GAP_RE.sub("   ", "".join(span["text"] for span in line["spans"])).strip()
            if text:
                lines.append(text)
    return lines


def _page_text(page: pymupdf.Page) -> tuple[str, list[_Segment] | None]:
    """
    Text in on-page reading order (top to bottom, left to right), not the
    order it's stored in the file. An edited PDF stores the new text at the
    end of the page, so file order strands a value away from its label and
    the text-only extraction step can't pair them.

    The text is what a reader sees. pymupdf's sorted text includes text under
    a cover-up box and interleaves two values drawn on top of each other
    ("SGD 9,000.008,193.27"), so a page with hidden or overlapping text is
    rebuilt from its visible runs, which are returned too. Every other page
    keeps pymupdf's sorted text, and None. Either way, a line printed
    sideways is added whole at the end of the page.
    """
    segments, hidden = _visible_segments(page)
    sideways = _sideways_lines(page)
    if hidden or _has_overlapping_text(segments):
        return "\n".join([_reading_order_text(segments), *sideways]), segments
    text = cast(str, page.get_text("text", sort=True))
    lines = [_COLUMN_GAP_RE.sub("   ", line).rstrip() for line in text.splitlines()]
    return "\n".join(lines + sideways), None


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

        page_text, visible_segments = _page_text(page)

        elements = []

        # A page with hidden or overlapping text: elements come from the runs
        # a reader can see, the same ones the page text was built from.
        if visible_segments is not None:
            for segment in visible_segments:
                elements.append({
                    "text": segment.text,
                    "font": segment.font,
                    "font_size": segment.size,
                    "flags": segment.flags,
                    "page_number": page_number,
                    "extraction_method": "native_pdf"
                })

        page_dict = page.get_text("dict") if visible_segments is None else {}

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