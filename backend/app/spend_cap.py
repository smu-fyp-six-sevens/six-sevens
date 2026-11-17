"""
A Claude spend cap per case (CASE_SPEND_CAP_USD): once a case's runs have cost that much at list price, no detection
run or case analysis starts on it. The spend is the audit trail's (audit.cost_usd_many: one database call). The cap
fails open: a spend that can't be read never stops a run.
"""

from __future__ import annotations

import logging

from . import audit
from .config import settings

logger = logging.getLogger(__name__)


def reached(case_id: str | None) -> float | None:
    """The case's spend when it has reached the cap; None when it hasn't, the cap is off, or the spend can't be read. Sync."""
    cap = settings.CASE_SPEND_CAP_USD
    if not case_id or cap <= 0:
        return None
    try:
        spent = audit.cost_usd_many([case_id]).get(case_id, 0.0)
    except Exception:
        logger.warning("Could not read the Claude spend of case %s; not applying the cap", case_id, exc_info=True)
        return None
    return spent if spent >= cap else None


def message(spent: float) -> str:
    return (
        f"This case has spent ${spent:.2f} on Claude, past its ${settings.CASE_SPEND_CAP_USD:.2f} cap, so no more runs "
        "start on it. An administrator can raise CASE_SPEND_CAP_USD."
    )
