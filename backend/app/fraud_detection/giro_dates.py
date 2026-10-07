"""GIRO transactions dated on days no Singapore bank processes GIRO.

See sg_reference/holidays.py for the sources: GIRO never runs on Sundays or public
holidays; Saturday same-bank GIRO is genuine at some banks, so Saturdays aren't flagged.
FAST, card and own-account transfers run every day and are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from .numerical_validation.common import _parse_field_date, _rows
from .sg_reference.holidays import giro_closed_reason

# "GIRO", "Interbank GIRO", "IBG", "PayNow GIRO" — all settle through GIRO on business days.
_GIRO_RE = re.compile(r"\bgiro\b|\bibg\b", re.IGNORECASE)


@dataclass(frozen=True)
class ClosedDayGiro:
    index: int
    day: date
    reason: str
    description: str


def giro_on_closed_days(transactions: Any) -> list[ClosedDayGiro]:
    found = []
    for index, row in enumerate(_rows(transactions)):
        description = " ".join(str(row.get("description") or "").split())
        if not _GIRO_RE.search(description):
            continue
        day = _parse_field_date(row.get("value_date")) or _parse_field_date(row.get("date"))
        if day is None:
            continue
        reason = giro_closed_reason(day)
        if reason:
            found.append(ClosedDayGiro(index=index, day=day, reason=reason, description=description))
    return found
