"""PAGE.SOURCE: a page that physically comes from somewhere else: a different page size or orientation, a
scanned page among digital ones, or a page set in fonts no other page uses.

A signal, not proof: a statement can carry a scanned annex. font_consistency judges text against its
neighbours on the same page, so a page that is consistent with itself but shares no font with the rest of the
document is only seen here. Single odd runs of text and image resolution stay with font_consistency and
image_manipulation.
"""

from __future__ import annotations

from collections import Counter

from .models import CheckOutcome, Evidence
from .page_view import PageView

CODE = "PAGE.SOURCE"
# A page this much covered by one image, with no text layer, is a scan.
SCAN_IMAGE_COVER = 0.5
# A font is one of a page's own when it sets at least this share of the page's characters (a bullet or
# symbol font doesn't count), and a page needs this many characters before its fonts say anything.
MAIN_FONT_SHARE = 0.1
MIN_FONT_CHARS = 100
# Fonts are only compared when at least this many pages have enough text: with two, neither is the odd one.
MIN_FONT_PAGES = 3


def _is_furniture(page: PageView, pages: list[PageView]) -> bool:
    """A cover or terms page at either end with no amounts on it: often a different stock, never evidence."""
    return page.number in (pages[0].number, pages[-1].number) and page.money_count == 0


def _main_fonts(page: PageView) -> set[str]:
    total = sum(chars for _, chars in page.font_chars)
    return {family for family, chars in page.font_chars if chars >= total * MAIN_FONT_SHARE}


def _font_evidence(pages: list[PageView]) -> list[Evidence]:
    """A page whose own fonts appear on no other page: swapped in from another document."""
    texty = [p for p in pages if sum(chars for _, chars in p.font_chars) >= MIN_FONT_CHARS]
    if len(texty) < MIN_FONT_PAGES:
        return []
    evidence = []
    for page in texty:
        if _is_furniture(page, pages):
            continue
        others = {family for other in texty if other is not page for family, _ in other.font_chars}
        own = _main_fonts(page)
        if own and not own & others:
            evidence.append(Evidence(
                CODE, "signal", ("page", page.number), (page.number,),
                f"Page {page.number} is set in {', '.join(sorted(own))}, a font no other page uses; "
                f"the other pages use {', '.join(sorted(others))}",
                stated=", ".join(sorted(own)), expected=", ".join(sorted(others)),
            ))
    return evidence


def check_source(pages: list[PageView]) -> CheckOutcome:
    outcome = CheckOutcome()
    shapes = Counter((p.size, p.rotation) for p in pages)
    (usual_size, usual_rotation), usual_count = shapes.most_common(1)[0]
    if usual_count > len(pages) / 2:
        for page in pages:
            if (page.size, page.rotation) == (usual_size, usual_rotation) or _is_furniture(page, pages):
                continue
            outcome.evidence.append(Evidence(
                CODE, "signal", ("page", page.number), (page.number,),
                f"Page {page.number} is {page.size[0]}x{page.size[1]} pt"
                + (f", rotated {page.rotation}°" if page.rotation else "")
                + f"; the other pages are {usual_size[0]}x{usual_size[1]} pt"
                + (f", rotated {usual_rotation}°" if usual_rotation else ""),
                stated=f"{page.size[0]}x{page.size[1]}", expected=f"{usual_size[0]}x{usual_size[1]}",
            ))

    digital = [p for p in pages if p.has_text_layer]
    if len(digital) >= 2:
        for page in pages:
            if not page.has_text_layer and page.image_cover >= SCAN_IMAGE_COVER:
                outcome.evidence.append(Evidence(
                    CODE, "signal", ("page", page.number), (page.number,),
                    f"Page {page.number} is a scanned image, but {len(digital)} other pages are digital text",
                    stated="scanned page", expected="digital text like the other pages",
                ))

    outcome.evidence.extend(_font_evidence(pages))
    return outcome
