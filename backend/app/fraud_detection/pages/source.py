"""PAGE.SOURCE: a page that physically comes from somewhere else: a different page size or orientation, or a
scanned page among digital ones.

A signal, not proof: a statement can carry a scanned annex. Fonts and image resolution are left to
font_consistency and image_manipulation, which already compare them across the whole document.
"""

from __future__ import annotations

from collections import Counter

from .models import CheckOutcome, Evidence
from .page_view import PageView

CODE = "PAGE.SOURCE"
# A page this much covered by one image, with no text layer, is a scan.
SCAN_IMAGE_COVER = 0.5


def _is_furniture(page: PageView, pages: list[PageView]) -> bool:
    """A cover or terms page at either end with no amounts on it: often a different stock, never evidence."""
    return page.number in (pages[0].number, pages[-1].number) and page.money_count == 0


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
    return outcome
