"""Text overlay: text boxes that physically overlap.

Runs as part of the layout anomaly check (`check_layout_anomalies`) and
reports `Text Overlay` findings in that result. Uses the layout package's
blocks, lines, and finding model.
"""

from __future__ import annotations

from typing import cast

from .layout_anomalies.common import (
    BBox, _bbox_area, _Block, _intersection_area, LayoutAnomaly, _Line, _snippet,
)


# Two text boxes overlapping by this fraction of the smaller one collide.
_TEXT_OVERLAP_MIN_RATIO = 0.20
# A watermark repeats on this many pages, so it is not an overlay finding.
_EXPECTED_OVERLAY_MIN_PAGES = 2


def _is_expected_overlay(line: _Line, lines: list[_Line]) -> bool:
    """Repeated, centrally placed text (a watermark on several pages) is expected."""
    normalized = " ".join(line.text.split()).lower()
    if not normalized:
        return False
    pages = {other.page for other in lines if " ".join(other.text.split()).lower() == normalized}
    if len(pages) < _EXPECTED_OVERLAY_MIN_PAGES:
        return False
    return (
        abs((line.bbox[0] + line.bbox[2]) / 2 - line.page_width / 2) <= line.page_width * 0.25
        and abs((line.bbox[1] + line.bbox[3]) / 2 - line.page_height / 2) <= line.page_height * 0.25
    )


def _check_text_overlaps(blocks: list[_Block]) -> list[LayoutAnomaly]:
    """Text lines whose boxes physically overlap, from the text layer or OCR."""
    lines = [
        line
        for block in blocks
        for line in block.lines
        if line.text.strip() and line.bbox[2] > line.bbox[0] and line.bbox[3] > line.bbox[1]
    ]
    expected = {id(line) for line in lines if _is_expected_overlay(line, lines)}
    anomalies: list[LayoutAnomaly] = []
    for index, left in enumerate(lines):
        for right in lines[index + 1 :]:
            if left.page != right.page or id(left) in expected or id(right) in expected:
                continue
            overlap_area = _intersection_area(left.bbox, right.bbox)
            smaller_area = min(_bbox_area(left.bbox), _bbox_area(right.bbox))
            if overlap_area <= 0 or smaller_area <= 0:
                continue
            ratio = overlap_area / smaller_area
            if ratio < _TEXT_OVERLAP_MIN_RATIO:
                continue
            overlap: BBox = (
                max(left.bbox[0], right.bbox[0]),
                max(left.bbox[1], right.bbox[1]),
                min(left.bbox[2], right.bbox[2]),
                min(left.bbox[3], right.bbox[3]),
            )
            anomalies.append(
                LayoutAnomaly(
                    anomaly_type="Text Overlay",
                    page=left.page + 1,
                    location=f"Text overlap near x={round(overlap[0])}, y={round(overlap[1])}",
                    bbox=cast(BBox, tuple(round(v, 2) for v in overlap)),
                    text=f"{_snippet(left.text, 80)} ↔ {_snippet(right.text, 80)}",
                    detected=f"{ratio:.0%} of the smaller text box overlaps",
                    expected="Text boxes should not overlap",
                    baseline=None,
                    deviation=f"{ratio:.0%} overlap",
                    confidence=1.0,
                    evidence={
                        "kind": "text-overlay",
                        "left_text": _snippet(left.text, 200),
                        "right_text": _snippet(right.text, 200),
                        "left_bbox": [round(v, 2) for v in left.bbox],
                        "right_bbox": [round(v, 2) for v in right.bbox],
                        "overlap_bbox": [round(v, 2) for v in overlap],
                        "overlap_ratio": round(ratio, 4),
                    },
                    summary=(
                        f"Overlapping text detected on page {left.page + 1}: "
                        f"{_snippet(left.text, 60)} overlaps {_snippet(right.text, 60)}."
                    ),
                )
            )
    return anomalies
