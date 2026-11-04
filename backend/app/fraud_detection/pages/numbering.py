"""PAGE.NUMBERING: the printed "Page x of y" runs 1..y with no gaps, repeats or reordering.

A page deleted from the middle leaves "2" followed by "4"; one inserted or copied repeats a number; the last
pages dropped leave the highest number short of the printed total. Unnumbered pages (a cover, terms and
conditions, a scan whose number can't be read) are left out rather than flagged, and may stand in for numbers
missing at their position: "Page 1 of 3", an unnumbered page, then "Page 3 of 3" is complete. A consolidated statement that restarts at "Page 1" with a new
total for each account is read as separate runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from ..numerical_validation.models import SkippedCheck
from .models import CheckOutcome, Evidence, Place
from .page_view import PAGE_BAND_FRACTION, PageView, place_of

CODE = "PAGE.NUMBERING"
# "Page 2 of 4", "Page 2/4", anywhere on the page.
_PAGE_RE = re.compile(r"\bpage\s*(\d{1,3})\s*(?:of|/)\s*(\d{1,3})\b", re.IGNORECASE)
# A bare "2 of 4" is only read in the top or bottom band, where page numbers live.
_BARE_RE = re.compile(r"(?<![\d.,/-])(\d{1,3})\s+of\s+(\d{1,3})(?![\d.,/-])", re.IGNORECASE)


@dataclass(frozen=True)
class _Numbered:
    page: int
    printed: int
    total: int


def printed_number(page: PageView) -> tuple[int, int] | None:
    match = _PAGE_RE.search(page.text) or _BARE_RE.search(page.band_text)
    if not match:
        return None
    printed, total = int(match.group(1)), int(match.group(2))
    return (printed, total) if printed >= 1 and total >= 1 else None


def _number_place(page: PageView) -> Place | None:
    """Where the page prints its number, read the way printed_number reads it; None on an OCR page (no
    positions) or when the number is split across lines."""
    band = page.size[1] * PAGE_BAND_FRACTION
    in_band = [line for line in page.placed if line.y1 <= band or line.y0 >= page.size[1] - band]
    line = next((line for line in page.placed if _PAGE_RE.search(line.text)), None) or next(
        (line for line in in_band if _BARE_RE.search(line.text)), None,
    )
    return place_of(page.number, [line]) if line is not None else None


def _runs(numbered: list[_Numbered]) -> list[list[_Numbered]]:
    """Split where the numbering starts again at 1 with a different total (a new account's statement)."""
    runs: list[list[_Numbered]] = []
    for item in numbered:
        if runs and not (item.printed == 1 and item.total != runs[-1][-1].total):
            runs[-1].append(item)
        else:
            runs.append([item])
    return runs


def _missing(first: int, last: int) -> str:
    return f"page {first}" if first == last else f"pages {first}-{last}"


def check_numbering(pages: list[PageView]) -> CheckOutcome:
    outcome = CheckOutcome()
    numbered = [_Numbered(p.number, *n) for p in pages if (n := printed_number(p)) is not None]
    if len(numbered) < 2:
        outcome.skipped.append(SkippedCheck(check_code=CODE, reason="Fewer than 2 pages print a page number"))
        return outcome

    runs = _runs(numbered)
    for position, run in enumerate(runs):
        # Physical pages before and after this run that print no number (a cover, a terms page, a scan whose
        # number can't be read): they can stand in for the numbers a run doesn't show.
        unnumbered_before = run[0].page - (runs[position - 1][-1].page if position else 0) - 1
        unnumbered_after = (runs[position + 1][0].page if position + 1 < len(runs) else len(pages) + 1) - run[-1].page - 1
        totals = sorted({item.total for item in run})
        if len(totals) > 1:
            outcome.evidence.append(Evidence(
                CODE, "impossible", ("pages", *(i.page for i in run)), tuple(i.page for i in run),
                f"The pages disagree on how many pages there are: {', '.join(f'of {t}' for t in totals)}",
                stated=", ".join(f"page {i.page}: {i.printed} of {i.total}" for i in run),
            ))
        total = max(totals)
        previous: _Numbered | None = None
        for item in run:
            if item.printed > item.total:
                outcome.evidence.append(Evidence(
                    CODE, "impossible", ("page", item.page), (item.page,),
                    f"Page {item.page} is numbered {item.printed} of {item.total}, past the last page",
                    stated=f"{item.printed} of {item.total}", expected=f"at most {item.total}",
                ))
            if previous is None:
                if item.printed - 1 > unnumbered_before:
                    outcome.evidence.append(Evidence(
                        CODE, "impossible", ("after", item.page - 1), (item.page,),
                        f"The numbering starts at {item.printed} of {item.total}: "
                        f"{_missing(1, item.printed - 1)} missing before page {item.page}",
                        stated=f"first page numbered {item.printed}", expected="1",
                    ))
            elif item.printed == previous.printed:
                outcome.evidence.append(Evidence(
                    CODE, "impossible", ("after", previous.page), (previous.page, item.page),
                    f"Pages {previous.page} and {item.page} are both numbered {item.printed} of {item.total}",
                    stated=f"{item.printed} twice", expected=f"{previous.printed + 1}",
                ))
            elif item.printed - previous.printed - 1 > item.page - previous.page - 1:
                outcome.evidence.append(Evidence(
                    CODE, "impossible", ("after", previous.page), (previous.page, item.page),
                    f"Page numbered {previous.printed} is followed by {item.printed} of {item.total}: "
                    f"{_missing(previous.printed + 1, item.printed - 1)} missing",
                    stated=f"{previous.printed} then {item.printed}", expected=f"{previous.printed + 1}",
                ))
            elif item.printed < previous.printed:
                outcome.evidence.append(Evidence(
                    CODE, "inconsistent", ("after", previous.page), (previous.page, item.page),
                    f"Page {item.page} is numbered {item.printed} but comes after page numbered {previous.printed}",
                    stated=f"{previous.printed} then {item.printed}", expected=f"{previous.printed + 1}",
                ))
            previous = item
        last = run[-1]
        highest = max(item.printed for item in run)
        if total - highest > unnumbered_after and last.printed == highest:
            outcome.evidence.append(Evidence(
                CODE, "impossible", ("after", last.page), (last.page,),
                f"The document says {total} pages, but the last numbered page is {highest}: "
                f"{_missing(highest + 1, total)} missing at the end",
                stated=f"last page {highest}", expected=f"{total}",
            ))
    # Each finding boxes the page numbers it's about, so the officer sees them on the page.
    places = {page.number: place for page in pages if (place := _number_place(page)) is not None}
    outcome.evidence = [
        replace(item, places=tuple(places[page] for page in item.pages if page in places)) for item in outcome.evidence
    ]
    return outcome
