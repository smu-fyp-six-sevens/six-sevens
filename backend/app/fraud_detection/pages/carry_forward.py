"""PAGE.CARRY_FORWARD: the balance a page carries forward is the balance the next page brings forward, and
each carry line agrees with the balances printed beside it.

Extraction keeps the transaction rows but drops the "Balance Brought/Carried Forward" lines, so these are a
second copy of the balances that nothing else reads. They catch what BS.RUNNING_BALANCE can't: a carry line
edited on its own, and a page deleted with the later balances retyped so the rows still reconcile.

Only the boundaries between pages are checked. Page 1's brought-forward is the opening balance and the last
page's carried-forward the closing one, which BS.STATED_OPENING and BS.STATED_CLOSING already compare. Where
the transaction rows break at the same boundary, BS.RUNNING_BALANCE reports it, so this stays quiet there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..numerical_validation.common import MONEY_TOLERANCE, format_money, table_rows
from ..numerical_validation.models import SkippedCheck
from ..numerical_validation.statements import _is_newest_first
from .models import CheckOutcome, Evidence, Place
from .page_view import MONEY_RE, PageView, PlacedLine, PlacedRow, money, place_of

CODE = "PAGE.CARRY_FORWARD"
_CARRY_RE = re.compile(
    r"\b(?:bal(?:ance)?\.?\s+)?(brought|carried)\s+(?:forward|fwd)\b|\b(?:bal(?:ance)?\.?\s+)?([bc])\s*/\s*f\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Carries:
    brought: float | None   # the first brought-forward on the page
    carried: float | None   # the last carried-forward on the page
    # Where each is printed, its label and amount; None on an OCR page (no positions).
    brought_at: Place | None = None
    carried_at: Place | None = None


def _amount_beside(page: PageView, label: PlacedLine, match: re.Match[str]) -> tuple[float, Place] | None:
    """The amount after the label on its own line, else the nearest amount to its right on the same row:
    statements print the carry amount in the balance column, a separate line of text on the same row. With
    where the label and amount are printed."""
    if (value := money(label.text[match.end():])) is not None:
        return value, place_of(page.number, [label])
    middle = (label.y0 + label.y1) / 2
    beside = [
        line for line in page.placed
        if line is not label and line.y0 <= middle <= line.y1 and line.x0 >= label.x1 - 1
        and MONEY_RE.fullmatch(line.text)
    ]
    if not beside:
        return None
    nearest = min(beside, key=lambda line: line.x0)
    value = money(nearest.text)
    return (value, place_of(page.number, [label, nearest])) if value is not None else None


def _amount_after(lines: tuple[str, ...], index: int, match: re.Match[str]) -> float | None:
    """Without positions (OCR text): the amount after the label, else on one of the next two lines."""
    if (value := money(lines[index][match.end():])) is not None:
        return value
    for line in lines[index + 1:index + 3]:
        if MONEY_RE.fullmatch(line):
            return money(line)
    return None


def read_carries(page: PageView) -> _Carries:
    found: list[tuple[str, float, Place | None]] = []
    if page.placed:
        ordered = sorted(page.placed, key=lambda line: (line.y0, line.x0))
        for line in ordered:
            if (match := _CARRY_RE.search(line.text)) and (beside := _amount_beside(page, line, match)) is not None:
                found.append(((match.group(1) or match.group(2) or "").lower(), *beside))
    else:
        for index, text in enumerate(page.lines):
            if (match := _CARRY_RE.search(text)) and (amount := _amount_after(page.lines, index, match)) is not None:
                found.append(((match.group(1) or match.group(2) or "").lower(), amount, None))
    brought = next((item for item in found if item[0].startswith("b")), None)
    carried = next((item for item in reversed(found) if item[0].startswith("c")), None)
    return _Carries(
        brought[1] if brought else None, carried[1] if carried else None,
        brought[2] if brought else None, carried[2] if carried else None,
    )


def _differs(a: float, b: float) -> bool:
    return abs(a - b) > MONEY_TOLERANCE


def check_carry_forward(pages: list[PageView], transactions: object, placed: list[PlacedRow]) -> CheckOutcome:
    outcome = CheckOutcome()
    carries = {page.number: read_carries(page) for page in pages}
    boundaries = [
        (page.number, nxt.number) for page, nxt in zip(pages, pages[1:])
        if carries[page.number].carried is not None or carries[nxt.number].brought is not None
    ]
    if not boundaries:
        outcome.skipped.append(SkippedCheck(check_code=CODE, reason="No balance brought or carried forward between pages"))
        return outcome
    rows = table_rows(transactions)
    if rows and _is_newest_first(rows):
        outcome.skipped.append(SkippedCheck(
            check_code=CODE, reason="Transactions are listed newest first; carry lines are only read oldest first",
        ))
        return outcome

    by_page: dict[int, list[PlacedRow]] = {}
    for row in placed:
        by_page.setdefault(row.page, []).append(row)

    for before, after in boundaries:
        carried, brought = carries[before].carried, carries[after].brought
        carried_at = tuple(place for place in (carries[before].carried_at,) if place)
        brought_at = tuple(place for place in (carries[after].brought_at,) if place)
        last_row = by_page.get(before, [None])[-1]
        first_row = (by_page.get(after) or [None])[0]
        key, pages_involved = ("after", before), (before, after)

        # A. Each carry line against the balances printed beside it on its own page.
        if carried is not None and last_row is not None and _differs(carried, last_row.balance):
            outcome.evidence.append(Evidence(
                CODE, "impossible", key, (before,),
                f"Page {before} carries forward {format_money(carried)}, but its last balance is {format_money(last_row.balance)}",
                stated=format_money(carried), expected=format_money(last_row.balance), places=carried_at,
            ))
        opening = (
            first_row.balance - first_row.amount
            if first_row is not None and first_row.amount is not None else None
        )
        if brought is not None and opening is not None and _differs(brought, opening):
            outcome.evidence.append(Evidence(
                CODE, "impossible", key, (after,),
                f"Page {after} brings forward {format_money(brought)}, but its first transaction starts from {format_money(opening)}",
                stated=format_money(brought), expected=format_money(opening), places=brought_at,
            ))

        # B. Carried against brought across the boundary, where the rows don't already show the break.
        if carried is not None and brought is not None and _differs(carried, brought):
            rows_break = (
                last_row is not None and first_row is not None and first_row.amount is not None
                and _differs(last_row.balance + first_row.amount, first_row.balance)
            )
            if not rows_break:
                outcome.evidence.append(Evidence(
                    CODE, "impossible", key, pages_involved,
                    f"Page {before} carries forward {format_money(carried)}, but page {after} brings forward {format_money(brought)}",
                    stated=format_money(brought), expected=format_money(carried), places=carried_at + brought_at,
                ))
    return outcome
