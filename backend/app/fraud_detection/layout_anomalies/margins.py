"""Page margin and running header/footer position checks."""

from __future__ import annotations

from statistics import median
from typing import Literal

from .common import (
    _anomaly, _assessed, _Block, _bucket_pt, _by_page, CheckAssessment,
    CHROME_POS_THRESHOLD_PT, _delta_confidence, _dominant, _format_summary,
    _FULL_LINE_WIDTH_RATIO, LayoutAnomaly, MARGIN_THRESHOLD_PT, PageMargins, _pt,
    _signed_pt,
)
from .extraction import (
    _is_title_page,
)


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
        anomalies.extend(_margin_anomalies_for(inner, blocks))
    if len(titles) >= 2:
        anomalies.extend(_margin_anomalies_for(titles, blocks))
    return anomalies, _assessed()


def _title_page_numbers(blocks: list[_Block]) -> set[int]:
    by_page = _by_page(b for b in blocks if b.role in {"body", "cover", "heading"})
    return {page + 1 for page, items in by_page.items() if _is_title_page(items)}


def _pages_with_full_line(blocks: list[_Block] | None, pages: set[int]) -> set[int]:
    """Pages whose body actually reaches across the sheet.

    Without that line, the right inset is the longest line on a short
    page, which is content extent rather than a margin.
    """
    if not blocks:
        return set(pages)
    found: set[int] = set()
    for block in blocks:
        page = block.page + 1
        if page not in pages or block.role in {"header", "footer", "signature"}:
            continue
        if block.page_width > 0 and block.width >= _FULL_LINE_WIDTH_RATIO * block.page_width:
            found.add(page)
    return found


def _margin_anomalies_for(
    page_margins: list[PageMargins],
    blocks: list[_Block] | None = None,
) -> list[LayoutAnomaly]:
    """Left edge, and the right edge of a page that has a full line.

    Top and bottom repeat the chrome-position check. Bottom is also how
    full the page is: the last page of a clause has a large empty band
    under the text. Neither is a margin.
    """
    expected = _expected_margins(page_margins)
    full_pages = _pages_with_full_line(blocks, {record.page for record in page_margins})
    anomalies: list[LayoutAnomaly] = []
    for record in page_margins:
        page_expected = expected[record.page]
        detected_margins = {
            "left": record.left,
            "right": record.right,
            "top": record.top,
            "bottom": record.bottom,
        }
        for side in ("left", "right"):
            if side == "right" and record.page not in full_pages:
                continue
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
    anchors: dict[int, _Block] = {}
    for page, items in _by_page(b for b in blocks if b.role == role).items():
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
        anomalies.append(
            _anomaly(
                "Margin",
                block,
                detected=_pt(block.y0),
                expected=_pt(expected_f),
                baseline=(
                    f"{matching} of {len(anchors)} pages have a {band} "
                    f"at y≈{_pt(expected_f)}"
                ),
                deviation=_signed_pt(signed),
                confidence=_delta_confidence(
                    abs(signed), CHROME_POS_THRESHOLD_PT, matching / len(anchors)
                ),
                evidence={
                    "kind": f"{band}-position",
                    "detected_y0_pt": round(block.y0, 2),
                    "expected_y0_pt": round(expected_f, 2),
                    "delta_pt": round(signed, 2),
                    "pages_analysed": len(anchors),
                    "pages_matching_baseline": matching,
                },
                location=f"{label} vertical position",
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
