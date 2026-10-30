"""PAGE.DUPLICATE: two pages with the same content, copied to make a statement look busier.

Only pages with figures on them take part: terms and conditions or notices legitimately repeat. Pages match
when their text is the same once page numbers and print timestamps are set aside, or when the transaction
rows placed on them are the same rows.
"""

from __future__ import annotations

import re

from .models import CheckOutcome, Evidence
from .numbering import _BARE_RE, _PAGE_RE
from .page_view import PageView, PlacedRow

CODE = "PAGE.DUPLICATE"
MIN_AMOUNTS = 3
MIN_ROWS = 3
_TIME_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?", re.IGNORECASE)


def _content_key(page: PageView) -> str:
    text = _BARE_RE.sub(" ", _PAGE_RE.sub(" ", page.text))
    return re.sub(r"\s+", " ", _TIME_RE.sub(" ", text)).strip().upper()


def check_duplicates(pages: list[PageView], placed: list[PlacedRow]) -> CheckOutcome:
    outcome = CheckOutcome()
    # ("text", the page's content) or ("rows", its transactions) -> the pages that have it.
    groups: dict[tuple[str, object], list[int]] = {}
    for page in pages:
        if page.money_count >= MIN_AMOUNTS:
            groups.setdefault(("text", _content_key(page)), []).append(page.number)

    rows_by_page: dict[int, list[tuple[str, str, str]]] = {}
    for row in placed:
        rows_by_page.setdefault(row.page, []).append(row.key)
    for number, keys in rows_by_page.items():
        if len(keys) >= MIN_ROWS:
            groups.setdefault(("rows", tuple(sorted(keys))), []).append(number)

    reported: set[tuple[int, ...]] = set()
    for (kind, _), numbers in groups.items():
        found = tuple(sorted(set(numbers)))
        if len(found) < 2 or found in reported:
            continue
        reported.add(found)
        listed = ", ".join(str(n) for n in found[:-1]) + f" and {found[-1]}"
        what = "the same content" if kind == "text" else "the same transactions"
        # Two neighbouring pages: the boundary between them, where numbering and carry evidence also point.
        key = ("after", found[0]) if len(found) == 2 and found[1] == found[0] + 1 else ("pages", *found)
        outcome.evidence.append(Evidence(
            CODE, "impossible", key, found, f"Pages {listed} have {what}",
            stated=f"pages {listed} identical", expected="each page different",
        ))
    return outcome
