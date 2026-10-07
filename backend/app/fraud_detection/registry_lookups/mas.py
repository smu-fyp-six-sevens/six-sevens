"""MAS Financial Institutions Directory: is the statement's bank licensed in Singapore?

https://eservices.mas.gov.sg/fid — MAS's own register. It has no JSON API, so the
bank categories are read from its listing pages (?category=...&count=100&page=N).
"""

from __future__ import annotations

import re
from typing import Any

from ..numerical_validation.common import REFERENCE_DATA, _bank_reference
from ..numerical_validation.models import SkippedCheck
from .http import LookupUnavailable, get
from .models import RegistryFinding

SOURCE = "MAS Financial Institutions Directory"
_LISTING_URL = "https://eservices.mas.gov.sg/fid/institution"
BANK_CATEGORIES = ("Local Bank", "Full Bank", "Qualifying Full Bank", "Wholesale Bank", "Merchant Bank")
_MAX_PAGES = 20
_NAME_RE = re.compile(r'<h3 class="header-inner-2">([^<]+)</h3>')
# Legal-form words that vary between how a statement and the register name a bank
# (e.g. "Bank Negara Indonesia" vs "P.T. BANK NEGARA INDONESIA (PERSERO) TBK").
_LEGAL_WORDS = frozenset({
    "LIMITED", "LTD", "PTE", "PRIVATE", "BERHAD", "BHD", "PLC", "INC", "SA", "AG", "NA", "N", "A",
    "THE", "PT", "P", "T", "PERSERO", "TBK", "SINGAPORE", "BRANCH", "CO",
})
_GENERIC_WORDS = frozenset({"BANK", "BANKING", "OF", "AND"})


def _key(name: str) -> tuple[str, ...]:
    tokens = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("&", " AND ")).split()
    return tuple(t for t in tokens if t not in _LEGAL_WORDS)


def _same_bank(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """One name is the other plus extra words, starting with the same word ("HSBC" and
    "HSBC BANK"), and says more than just "bank"."""
    if not a or not b:
        return False
    shorter, longer = sorted((a, b), key=len)
    return shorter[0] == longer[0] and set(shorter) <= set(longer) and bool(set(shorter) - _GENERIC_WORDS)


async def licensed_banks() -> list[str]:
    names: list[str] = []
    for category in BANK_CATEGORIES:
        for page in range(1, _MAX_PAGES + 1):
            html = await get(_LISTING_URL, params={"category": category, "count": 100, "page": page}, as_json=False)
            new = [n.strip() for n in _NAME_RE.findall(html) if n.strip() not in names]
            if not new:
                break
            names += new
    if not names:
        raise LookupUnavailable("the MAS directory listing returned no institutions (page layout may have changed)")
    return names


def _licensed_name(bank_name: str, licensed: list[str]) -> str | None:
    wanted = {_key(bank_name)}
    reference = _bank_reference(bank_name)
    if reference is not None:
        wanted |= {_key(n) for n, r in REFERENCE_DATA["bank_identifiers"].items() if r is reference}
    for name in licensed:
        if any(_same_bank(_key(name), key) for key in wanted):
            return name
    return None


async def check_bank_licensed(fields: dict[str, Any]) -> tuple[list[RegistryFinding], list[SkippedCheck]]:
    bank_name = fields.get("bank_name")
    if not isinstance(bank_name, str) or not bank_name.strip():
        return [], [SkippedCheck(check_code="REG.BANK_LICENSED", reason="No bank name extracted")]
    try:
        licensed = await licensed_banks()
    except LookupUnavailable as exc:
        return [], [SkippedCheck(check_code="REG.BANK_LICENSED", reason=f"MAS directory unavailable: {exc}")]
    if _licensed_name(bank_name, licensed):
        return [], []
    return [RegistryFinding(
        check_code="REG.BANK_LICENSED", severity="signal", source=SOURCE,
        summary=f"\"{bank_name}\" isn't a bank licensed by MAS (local, full, qualifying full, wholesale or "
                "merchant bank)",
        field="bank_name", stated=bank_name,
        severity_note="Genuine for a statement from an overseas bank, or if the statement uses a trading name "
                      "the directory doesn't list",
    )], []
