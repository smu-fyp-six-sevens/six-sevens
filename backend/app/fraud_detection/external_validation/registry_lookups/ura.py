"""URA Data Service: is an address approved for residential use?

https://eservice.ura.gov.sg/maps/api/ — "Approved Residential Use" (EAU_Appr_Resi_Use). Given a
block/house number and street, and for a strata building the storey and unit, it answers
"Y" or "NA": "either the record for the address is not available or the address is not
approved for Residential use". URA's notes on its coverage:
* private residential units only: HDB developments and State properties are not included
* residential units in shophouse developments are not included
* developments without TOP are not included; quarterly updates
So "Y" confirms a private home; "NA" for a given unit means not a home, or a unit URA has no
record of (one that doesn't exist, a shophouse home, a new development).

Behaviour checked against the live service (Oct 2026): the street must be spelt as OneMap
gives it (upper case, unabbreviated), so callers pass OneMap's block and road name; a strata
unit only answers "Y" with its storey and unit ("#40-01" -> storey "40", unit "01"); a landed
house answers without them.

Access: an access key (settings.URA_API_KEY) from which a token is generated "for the day's
access"; the token is fetched here once a day and again if URA rejects it.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ....config import settings
from .http import LookupUnavailable, get

SOURCE = "URA Approved Residential Use"
_TOKEN_URL = "https://eservice.ura.gov.sg/uraDataService/insertNewToken/v1"
_SERVICE_URL = "https://eservice.ura.gov.sg/uraDataService/invokeUraDS/v1"
_SGT = timezone(timedelta(hours=8))
# "#40-01", "# 4 - 1", "#B1-12A": storey and unit as printed.
_UNIT_RE = re.compile(r"#\s*([A-Z]?\d{1,3})\s*-\s*(\d{1,5}[A-Z]?)", re.IGNORECASE)

_token: dict[str, str] = {}
# Answers by address, kept until the next process start (URA updates quarterly). Only real
# answers are kept: a rejected-token reply must not be remembered as one. Bounded: the oldest
# answer goes when it's full, so a long-running server can't grow it without limit.
_MAX_ANSWERS = 5000
_answers: dict[tuple[str, str, tuple[str, str] | None], bool] = {}


def configured() -> bool:
    return bool(settings.URA_API_KEY)


def unit_of(address: str) -> tuple[str, str] | None:
    """The (storey, unit) an address names, zero-padded to two digits as Singapore prints them."""
    match = _UNIT_RE.search(address)
    if not match:
        return None
    storey, unit = match.group(1).upper(), match.group(2).upper()
    return (storey.zfill(2) if storey.isdigit() else storey), (unit.zfill(2) if unit[:1].isdigit() and len(unit) == 1 else unit)


async def _day_token(refresh: bool = False) -> str:
    today = datetime.now(_SGT).date().isoformat()
    if not refresh and _token.get("day") == today:
        return _token["value"]
    body = await get(_TOKEN_URL, headers={"AccessKey": settings.URA_API_KEY}, cache_ttl=0)
    token = body.get("Result") if isinstance(body, dict) else None
    if not token or body.get("Status") != "Success":
        raise LookupUnavailable(f"URA did not issue a token: {body.get('Message') if isinstance(body, dict) else body}")
    _token.update(day=today, value=str(token))
    return str(token)


async def approved_residential_use(block: str, street: str, unit: tuple[str, str] | None = None) -> bool:
    """True if URA has the address approved for residential use; False for "NA" (not approved, or no record)."""
    if not configured():
        raise LookupUnavailable("no URA access key configured (URA_API_KEY)")
    key = (block.upper(), street.upper(), unit)
    if key in _answers:
        return _answers[key]
    params = {"service": "EAU_Appr_Resi_Use", "blkHouseNo": block.upper(), "street": street.upper()}
    if unit:
        params.update(storeyNo=unit[0], unitNo=unit[1])
    body: object = None
    for attempt in range(2):
        token = await _day_token(refresh=attempt > 0)
        body = await get(_SERVICE_URL, params=params, cache_ttl=0,
                         headers={"AccessKey": settings.URA_API_KEY, "Token": token})
        answer = body.get("isResiUse") if isinstance(body, dict) else None
        if answer in ("Y", "NA"):
            if len(_answers) >= _MAX_ANSWERS:
                _answers.pop(next(iter(_answers)))
            _answers[key] = answer == "Y"
            return _answers[key]
        # Anything else (an expired or rejected token) gets one retry with a fresh token.
    raise LookupUnavailable(f"URA gave no answer for {block} {street}: {str(body)[:120]}")
