"""PAGE.HEADER_IDENTITY: an identifier printed on several pages (account number, holder name, UEN...) is the
same on every page.

A page swapped in from another account or customer is internally consistent, so nothing else notices it:
only its header disagrees with the other pages. The label is learnt from the page where the extracted
value is printed ("Account No: 012-345678-9" teaches "Account No"), then that label is read on every other
page, so no per-bank pattern is needed. A page without the header (a continuation page) is not evidence;
only a different value under the same label is.

Statement periods and dates are not compared: a multi-month statement legitimately prints each page's own
month, and a page from another period is already caught by BS.PERIOD_BOUNDS through its transaction dates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from ..numerical_validation.models import SkippedCheck
from .models import CheckOutcome, Evidence, Place
from .page_view import PageView, place_of

CODE = "PAGE.HEADER_IDENTITY"
ANCHOR_FIELDS = (
    "account_number", "account_holder_name", "company_name", "individual_name", "uen",
    "tax_reference_number", "nric_number",
)
_MASK_CHARS = "X*•"
_MAX_LABEL_LENGTH = 40


def _norm(text: str) -> str:
    return re.sub(r"[^A-Z0-9" + re.escape(_MASK_CHARS) + "]", "", text.upper())


def _agrees(read: str, value: str) -> bool:
    """Whether the value read under the label is the anchor value, allowing extra text after it on the same
    line and a masked form ("XXXX-1234" against "0123-1234": the visible digits must match)."""
    read_n, value_n = _norm(read), _norm(value)
    if read_n.startswith(value_n) or (len(read_n) >= 4 and value_n.startswith(read_n)):
        return True
    if any(c in read_n + value_n for c in _MASK_CHARS):
        visible = lambda s: re.sub(rf"^[{re.escape(_MASK_CHARS)}]+", "", s)  # noqa: E731
        shorter, longer = sorted((visible(read_n[: len(value_n)]), visible(value_n)), key=len)
        return len(shorter) >= 4 and longer.endswith(shorter)
    return False


def _value_pattern(value: str) -> re.Pattern[str]:
    """The value as printed, allowing any spacing or punctuation between its letters and digits."""
    chunks = re.findall(r"[A-Za-z0-9]+", value)
    return re.compile(r"[\s\-./]*".join(re.escape(c) for c in chunks), re.IGNORECASE)


def _clean_label(text: str) -> str:
    return text.strip().rstrip(":.-").strip()


@dataclass(frozen=True)
class _Label:
    text: str
    on_own_line: bool  # "Account No:" on one line, the value on the next


def _learn_label(pages: list[PageView], value: str) -> tuple[int, _Label] | None:
    """The label printed before the value on the first page that prints it."""
    pattern = _value_pattern(value)
    for page in pages:
        for index, line in enumerate(page.lines):
            match = pattern.search(line)
            if not match:
                continue
            before = _clean_label(line[:match.start()])
            if re.search(r"[A-Za-z]{2}", before) and len(before) <= _MAX_LABEL_LENGTH:
                return page.number, _Label(before, on_own_line=False)
            if not before and index > 0 and page.lines[index - 1].endswith(":"):
                label = _clean_label(page.lines[index - 1])
                if re.search(r"[A-Za-z]{2}", label) and len(label) <= _MAX_LABEL_LENGTH:
                    return page.number, _Label(label, on_own_line=True)
    return None


def _after_label(line: str, wanted: str) -> str | None:
    """The text after the label on a line that starts with it (labels compared by letters and digits only)."""
    if not _norm(line).startswith(wanted):
        return None
    end = next(i for i in range(1, len(line) + 1) if len(_norm(line[:i])) == len(wanted))
    return line[end:].lstrip(" :.-\t") or None


def _read_under_label(page: PageView, label: _Label) -> str | None:
    wanted = _norm(label.text)
    for index, line in enumerate(page.lines):
        if label.on_own_line:
            if _norm(_clean_label(line)) == wanted and index + 1 < len(page.lines):
                return page.lines[index + 1]
        elif (rest := _after_label(line, wanted)) is not None:
            return rest
    return None


def _value_place(page: PageView, value: str) -> Place | None:
    """Where the page prints the value."""
    pattern = _value_pattern(value)
    line = next((line for line in page.placed if pattern.search(line.text)), None)
    return place_of(page.number, [line]) if line else None


def _label_place(page: PageView, label: _Label) -> Place | None:
    """Where the page prints the label, and the value with it: on the same line, or the line under it."""
    wanted = _norm(label.text)
    ordered = sorted(page.placed, key=lambda line: (line.y0, line.x0))
    for index, line in enumerate(ordered):
        if label.on_own_line and _norm(_clean_label(line.text)) == wanted:
            return place_of(page.number, ordered[index:index + 2])
        if not label.on_own_line and _after_label(line.text, wanted) is not None:
            return place_of(page.number, [line])
    return None


def check_header_identity(pages: list[PageView], fields: dict[str, Any]) -> CheckOutcome:
    outcome = CheckOutcome()
    anchors = {
        name: value.strip() for name in ANCHOR_FIELDS
        if isinstance(value := fields.get(name), str) and len(_norm(value)) >= 4
    }
    compared = 0
    for name, value in anchors.items():
        learnt = _learn_label(pages, value)
        if learnt is None:
            continue
        source_page, label = learnt
        for page in pages:
            if page.number == source_page:
                continue
            read = _read_under_label(page, label)
            if read is None:
                continue
            compared += 1
            if not _agrees(read, value):
                shown = read[: max(len(value) + 8, 24)]
                by_number = {p.number: p for p in pages}
                places = (_value_place(by_number[source_page], value), _label_place(page, label))
                outcome.evidence.append(Evidence(
                    CODE, "impossible", ("page", page.number), (source_page, page.number),
                    f'Page {page.number} prints {label.text} "{shown}", but page {source_page} prints "{value}"',
                    stated=shown, expected=value, places=tuple(place for place in places if place),
                ))
    if compared == 0:
        outcome.skipped.append(SkippedCheck(
            check_code=CODE, reason="No identifier is printed under the same label on more than one page",
        ))
    return outcome
