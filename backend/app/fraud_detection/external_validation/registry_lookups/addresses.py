"""Addresses on a document against OneMap and HDB's block register.

* REG.POSTAL_CODE_EXISTS  the postal code is a real Singapore postal code
* REG.ADDRESS_MATCH       the block and street match what's at that postal code
* REG.RESIDENTIAL_ADDRESS a residential address isn't an HDB block with no homes in it
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any

from ...field_values import also_printed_in, describe_path, text_values
from ...numerical_validation.models import SkippedCheck
from . import hdb, onemap
from .http import LookupUnavailable
from .models import RegistryFinding

# At most this many distinct addresses per document (a shareholder table can list many), to bound the lookups.
_MAX_ADDRESSES = 10
# Address fields that hold where a person lives, so the building should be residential.
RESIDENTIAL_FIELDS = frozenset({"individual_address", "residential_address", "home_address"})
# On these types the plain "address" field is the person's home address.
RESIDENTIAL_ADDRESS_TYPES = frozenset({"nric", "credit_report", "proof_of_address"})

_BLOCK_RE = re.compile(r"^\s*(?:blk|block)?\s*(\d{1,4}[A-Z]?)\b(?!\s*-)", re.IGNORECASE)


def _street_part(address: str) -> str:
    """The text before the unit number, postal code or first comma."""
    return re.split(r"#|,|\bsingapore\b|\(?s\)?\s*\d{6}", address, maxsplit=1, flags=re.IGNORECASE)[0]


def _names_in(address: str, name: str) -> bool:
    """The address contains this road or building name, word for word, allowing abbreviations either way
    ("BEDOK NTH AVE 3" for "BEDOK NORTH AVENUE 3"). Every word counts, direction and number included:
    Bedok North Avenue 3 is not Bedok South Avenue 1."""
    wanted = hdb.street_tokens(name)
    words = hdb.street_tokens(address)
    if not wanted or wanted == ["NIL"]:
        return False
    return any(
        all(hdb.abbreviates(w, n) or hdb.abbreviates(n, w) for w, n in zip(words[i:i + len(wanted)], wanted))
        for i in range(len(words) - len(wanted) + 1)
    )


def _matches(address: str, results: list[dict]) -> bool:
    """Some building at the postal code has the address's block number (if it gives one) and its road or
    building name. Both must hold for the same building: a matching block number on another road isn't it."""
    block = _BLOCK_RE.match(address)
    street = _street_part(address)
    if not block and not re.search(r"[A-Za-z]{2}", street):
        return True  # nothing to compare
    for result in results:
        if block and block.group(1).upper() != str(result.get("BLK_NO", "")).upper():
            continue
        if _names_in(address, str(result.get("ROAD_NAME", ""))) or _names_in(address, str(result.get("BUILDING", ""))):
            return True
    return False


@dataclass
class _Address:
    """One distinct address, and every field it was printed in."""
    text: str
    postal: str
    paths: list[str] = dc_field(default_factory=list)
    residential: bool = False
    # The document's fields, to describe each path as the officer reads it ("Shareholder 2 (NG BEE LENG), Address").
    fields: dict[str, Any] = dc_field(default_factory=dict, repr=False)

    @property
    def field(self) -> str:
        return self.paths[0]

    @property
    def label(self) -> str:
        return describe_path(self.fields, self.field)

    @property
    def also(self) -> str:
        """'. Also printed in ...' for the address's other fields, or ''."""
        return also_printed_in(self.fields, self.paths[1:])


def _is_residential(path_key: str, top_level: bool, document_type: str | None) -> bool:
    """Where a person lives, so the building should have homes. A nested plain "address" (a shareholder
    table's) isn't: the shareholder may be a company."""
    return path_key in RESIDENTIAL_FIELDS or (top_level and path_key == "address" and document_type in RESIDENTIAL_ADDRESS_TYPES)


def _same_address(a: str, b: str) -> bool:
    """Same block and street, allowing abbreviations and different punctuation or unit formatting:
    "22 Bedok North Avenue 3, #10-12" and "BLK 22 BEDOK NTH AVE 3 #10-12"."""
    block_a, block_b = _BLOCK_RE.match(a), _BLOCK_RE.match(b)
    if (block_a.group(1).upper() if block_a else None) != (block_b.group(1).upper() if block_b else None):
        return False
    words_a = [w for w in hdb.street_tokens(_street_part(a)) if w not in ("BLK", "BLOCK")]
    words_b = [w for w in hdb.street_tokens(_street_part(b)) if w not in ("BLK", "BLOCK")]
    return len(words_a) == len(words_b) and all(
        hdb.abbreviates(x, y) or hdb.abbreviates(y, x) for x, y in zip(words_a, words_b)
    )


def _addresses(fields: dict[str, Any], document_type: str | None) -> list[_Address]:
    """Every distinct Singapore address in the document, nested ones included, top-level fields first."""
    found: list[_Address] = []
    for value in text_values(fields):
        if not value.key.lower().endswith("address"):
            continue
        postal = onemap.POSTAL_CODE_RE.search(value.text)
        if not postal:
            continue
        address = next((a for a in found if a.postal == postal.group(1) and _same_address(a.text, value.text)), None)
        if address is None:
            address = _Address(text=value.text, postal=postal.group(1), fields=fields)
            found.append(address)
        if value.path not in address.paths:
            address.paths.append(value.path)
        address.residential |= _is_residential(value.key, value.top_level, document_type)
    return found


def _finding(code: str, severity: Any, summary: str, source: str, address: _Address, **kwargs: Any) -> RegistryFinding:
    return RegistryFinding(
        check_code=code, severity=severity, summary=summary, source=source,
        field=address.field, stated=address.text, also_fields=address.paths[1:], **kwargs,
    )


async def _check_one(address: _Address) -> list[RegistryFinding]:
    results = await onemap.addresses_at_postal_code(address.postal)
    if not results:
        return [_finding(
            "REG.POSTAL_CODE_EXISTS", "impossible",
            f"Postal code {address.postal} in {address.label} doesn't exist in OneMap's register of Singapore "
            f"addresses{address.also}",
            onemap.SOURCE, address,
        )]
    if not _matches(address.text, results):
        actual = "; ".join(str(r.get("ADDRESS")) for r in results[:3])
        return [_finding(
            "REG.ADDRESS_MATCH", "inconsistent",
            f"{address.label} gives \"{address.text}\", but postal code {address.postal} is {actual}{address.also}",
            onemap.SOURCE, address, expected=actual,
        )]
    if not address.residential:
        return []
    block_match = _BLOCK_RE.match(address.text)
    result = next((r for r in results if block_match and str(r.get("BLK_NO", "")).upper() == block_match.group(1).upper()),
                  results[0])
    if not result.get("BLK_NO") or not result.get("ROAD_NAME"):
        return []
    block = await hdb.hdb_block(str(result["BLK_NO"]), str(result["ROAD_NAME"]))
    if block is None or block.residential:
        return []
    return [_finding(
        "REG.RESIDENTIAL_ADDRESS", "inconsistent",
        f"{address.label} is HDB block {block.block} {block.street}, which HDB lists as having no residential units"
        + (" (commercial use)" if block.commercial else "") + address.also,
        hdb.SOURCE, address, expected="a residential building",
    )]


async def check_addresses(fields: dict[str, Any], document_type: str | None) -> tuple[list[RegistryFinding], list[SkippedCheck]]:
    addresses = _addresses(fields, document_type)
    if not addresses:
        return [], [SkippedCheck(check_code="REG.ADDRESS", reason="No address with a Singapore postal code extracted")]
    findings: list[RegistryFinding] = []
    skipped: list[SkippedCheck] = []
    if len(addresses) > _MAX_ADDRESSES:
        rest = addresses[_MAX_ADDRESSES:]
        skipped.append(SkippedCheck(
            check_code="REG.ADDRESS",
            reason=f"{len(rest)} more address(es) not looked up (limit {_MAX_ADDRESSES} per document): "
                   + ", ".join(a.field for a in rest),
        ))
    for address in addresses[:_MAX_ADDRESSES]:
        try:
            findings += await _check_one(address)
        except LookupUnavailable as exc:
            skipped.append(SkippedCheck(check_code="REG.ADDRESS", reason=f"{address.field}: address lookup unavailable: {exc}"))
    return findings, skipped
