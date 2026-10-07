"""Addresses on a document against OneMap and HDB's block register.

* REG.POSTAL_CODE_EXISTS  the postal code is a real Singapore postal code
* REG.ADDRESS_MATCH       the block and street match what's at that postal code
* REG.RESIDENTIAL_ADDRESS a residential address isn't an HDB block with no homes in it
"""

from __future__ import annotations

import re
from typing import Any

from ..numerical_validation.models import SkippedCheck
from . import hdb, onemap
from .http import LookupUnavailable
from .models import RegistryFinding

# At most this many addresses per document, to stay inside the anonymous rate limits.
_MAX_ADDRESSES = 4
# Address fields that hold where a person lives, so the building should be residential.
RESIDENTIAL_FIELDS = frozenset({"individual_address", "residential_address", "home_address"})
# On these types the plain "address" field is the person's home address.
RESIDENTIAL_ADDRESS_TYPES = frozenset({"nric", "credit_report", "proof_of_address"})

_POSTAL_RE = re.compile(r"(?:singapore|s)?\s*\(?\b(\d{6})\b\)?", re.IGNORECASE)
_BLOCK_RE = re.compile(r"^\s*(?:blk|block)?\s*(\d{1,4}[A-Z]?)\b(?!\s*-)", re.IGNORECASE)
# Words that say what kind of road it is, or a direction; not what distinguishes it.
_GENERIC = frozenset("""STREET ST AVENUE AVE ROAD RD DRIVE DR CRESCENT CRES CLOSE CL PLACE PL LANE LN WAY
    TERRACE TER LINK LK WALK VIEW RISE GROVE CENTRAL CTRL NORTH NTH SOUTH STH EAST WEST UPPER UPP LOWER
    SINGAPORE BLK BLOCK""".split())


def _address_fields(fields: dict[str, Any], document_type: str | None) -> list[tuple[str, str]]:
    found = []
    for name, value in fields.items():
        if isinstance(value, str) and name.endswith("address") and _POSTAL_RE.search(value):
            found.append((name, value))
    return found[:_MAX_ADDRESSES]


def _is_residential_field(name: str, document_type: str | None) -> bool:
    return name in RESIDENTIAL_FIELDS or (name == "address" and document_type in RESIDENTIAL_ADDRESS_TYPES)


def _distinctive(text: str) -> set[str]:
    words = re.sub(r"[^A-Z ]", " ", text.upper()).split()
    return {w for w in words if w not in _GENERIC and len(w) > 1}


def _street_part(address: str) -> str:
    """The text before the unit number, postal code or first comma."""
    return re.split(r"#|,|\bsingapore\b|\(?s\)?\s*\d{6}", address, maxsplit=1, flags=re.IGNORECASE)[0]


def _matches(address: str, results: list[dict]) -> bool:
    block = _BLOCK_RE.match(address)
    words = _distinctive(_street_part(address))
    for result in results:
        if block and block.group(1).upper() == str(result.get("BLK_NO", "")).upper():
            return True
        if words and words & _distinctive(f"{result.get('ROAD_NAME', '')} {result.get('BUILDING', '')}"):
            return True
    return not block and not words  # nothing to compare


def _finding(code: str, severity: Any, summary: str, source: str, **kwargs: Any) -> RegistryFinding:
    return RegistryFinding(check_code=code, severity=severity, summary=summary, source=source, **kwargs)


async def _check_one(field: str, address: str, document_type: str | None) -> list[RegistryFinding]:
    postal = _POSTAL_RE.search(address).group(1)  # type: ignore[union-attr]  # filtered in _address_fields
    results = await onemap.addresses_at_postal_code(postal)
    if not results:
        return [_finding(
            "REG.POSTAL_CODE_EXISTS", "impossible",
            f"Postal code {postal} in {field} doesn't exist in OneMap's register of Singapore addresses",
            onemap.SOURCE, field=field, stated=address,
        )]
    if not _matches(address, results):
        actual = "; ".join(str(r.get("ADDRESS")) for r in results[:3])
        return [_finding(
            "REG.ADDRESS_MATCH", "inconsistent",
            f"{field} gives \"{address}\", but postal code {postal} is {actual}",
            onemap.SOURCE, field=field, stated=address, expected=actual,
        )]
    if not _is_residential_field(field, document_type):
        return []
    block_match = _BLOCK_RE.match(address)
    result = next((r for r in results if block_match and str(r.get("BLK_NO", "")).upper() == block_match.group(1).upper()),
                  results[0])
    if not result.get("BLK_NO") or not result.get("ROAD_NAME"):
        return []
    block = await hdb.hdb_block(str(result["BLK_NO"]), str(result["ROAD_NAME"]))
    if block is None or block.residential:
        return []
    return [_finding(
        "REG.RESIDENTIAL_ADDRESS", "inconsistent",
        f"{field} is HDB block {block.block} {block.street}, which HDB lists as having no residential units"
        + (" (commercial use)" if block.commercial else ""),
        hdb.SOURCE, field=field, stated=address, expected="a residential building",
    )]


async def check_addresses(fields: dict[str, Any], document_type: str | None) -> tuple[list[RegistryFinding], list[SkippedCheck]]:
    addresses = _address_fields(fields, document_type)
    if not addresses:
        return [], [SkippedCheck(check_code="REG.ADDRESS", reason="No address with a Singapore postal code extracted")]
    findings: list[RegistryFinding] = []
    skipped: list[SkippedCheck] = []
    for field, address in addresses:
        try:
            findings += await _check_one(field, address, document_type)
        except LookupUnavailable as exc:
            skipped.append(SkippedCheck(check_code="REG.ADDRESS", reason=f"{field}: address lookup unavailable: {exc}"))
    return findings, skipped
