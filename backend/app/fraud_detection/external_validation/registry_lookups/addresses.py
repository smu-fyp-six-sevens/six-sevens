"""Addresses on a document against OneMap, HDB's register and URA's planning data.

REG.POSTAL_CODE_EXISTS; REG.ADDRESS_MATCH (block and street are at that postal code); REG.RESIDENTIAL_ADDRESS (a home
can be lived in); REG.COMPANY_ADDRESS_RESIDENTIAL (company at a home, only a signal: the Home Office Schemes allow it);
REG.UNIT_STOREY (an HDB unit is no higher than its block); REG.UNIT_MISSING (a home in flats or a condo has a unit number).

Best source first: an HDB block by HDB's register; a private building by URA's approved-use answer for the unit ("Y" is
an approved home, "NA" a non-home or a unit URA has no record of), then by zoning (Business 1/2 allows no homes;
Residential with Commercial at 1st Storey is shophouse land, which URA's data leaves out; Commercial, White and the
mixed zones allow homes and offices, so zoning alone can't decide there).

Home address, private building:
* zoned Business 1/2                                 inconsistent
* approved for residential use                       fine
* unit not approved, shophouse zone                  fine
* unit not approved, Residential zone or no zone     signal
* unit not approved, any other zone                  implausible
* no unit given, not approved                        signal (REG.UNIT_MISSING)
Company address: HDB flats only, a unit URA approves, or (only if URA gave no answer) a purely Residential zone: signal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field as dc_field
from typing import Any

from ...field_values import also_printed_in, describe_path, text_values
from ...numerical_validation.models import SkippedCheck
from . import hdb, land_use, onemap, ura
from .http import LookupUnavailable
from .models import RegistryFinding

# At most this many distinct addresses per document (a shareholder table can list many), to bound the lookups.
_MAX_ADDRESSES = 10
# Address fields that hold where a person lives, so the building should be residential.
RESIDENTIAL_FIELDS = frozenset({"individual_address", "residential_address", "home_address"})
# On these types the plain "address" field is the person's home address.
RESIDENTIAL_ADDRESS_TYPES = frozenset({"nric", "credit_report", "proof_of_address"})
# Address fields that hold where a company is registered or operates.
COMPANY_FIELDS = frozenset({
    "company_address", "registered_address", "registered_office_address", "business_address",
    "company_registered_address",
})
# URA zones whose definition lists no residential use (Written Statement, Table 1): industry.
NO_HOMES_ZONES = frozenset({"BUSINESS 1", "BUSINESS 2"})
# The URA zone for homes only; Residential with Commercial at 1st Storey and the mixed zones allow businesses.
HOMES_ONLY_ZONE = "RESIDENTIAL"
# Zones of shophouses (URA's example development for this zone), whose homes URA's approved-use data omits.
SHOPHOUSE_ZONES = frozenset({"RESIDENTIAL WITH COMMERCIAL AT 1ST STOREY"})
# Zones meant for homes, where a unit URA has no record of is more likely a gap in its data than a non-home.
HOME_ZONES = frozenset({"RESIDENTIAL", "RESIDENTIAL / INSTITUTION"})

# Six digits not part of a longer number, so "S460022" and "Singapore 460022" both count.
_POSTAL_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
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
    company: bool = False
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
        address.company |= value.key.lower() in COMPANY_FIELDS
    return found


def _finding(code: str, severity: Any, summary: str, source: str, address: _Address, **kwargs: Any) -> RegistryFinding:
    return RegistryFinding(
        check_code=code, severity=severity, summary=summary, source=source,
        field=address.field, stated=address.text, also_fields=address.paths[1:], **kwargs,
    )


Outcome = tuple[list[RegistryFinding], list[SkippedCheck]]


def _building(address: _Address, results: list[dict]) -> dict:
    """The OneMap result for the address's own block, else the postal code's first."""
    block_match = _BLOCK_RE.match(address.text)
    return next((r for r in results if block_match and str(r.get("BLK_NO", "")).upper() == block_match.group(1).upper()),
                results[0])


async def _zone(address: _Address, building: dict) -> tuple[str | None, SkippedCheck | None]:
    """The URA zone at the building's map point, or why it couldn't be looked up."""
    try:
        lon, lat = float(building["LONGITUDE"]), float(building["LATITUDE"])
    except (KeyError, TypeError, ValueError):
        return None, None
    try:
        return await land_use.zone_at(lon, lat), None
    except LookupUnavailable as exc:
        return None, SkippedCheck(check_code="REG.BUILDING_USE", reason=f"{address.field}: zoning unavailable: {exc}")


async def _building_use(address: _Address, results: list[dict]) -> Outcome:
    """Whether a home address can be lived in, and whether a company address is someone's home."""
    if not (address.residential or address.company):
        return [], []
    building = _building(address, results)
    block = None
    if building.get("BLK_NO") and building.get("ROAD_NAME"):
        block = await hdb.hdb_block(str(building["BLK_NO"]), str(building["ROAD_NAME"]))
    findings: list[RegistryFinding] = []
    unit = ura.unit_of(address.text)
    if block is not None:
        # An HDB block: HDB's register says exactly what's in it.
        if unit and unit[0].isdigit() and block.max_floor and int(unit[0]) > block.max_floor:
            findings.append(_finding(
                "REG.UNIT_STOREY", "impossible",
                f"{address.label} gives unit #{unit[0]}-{unit[1]}, on storey {int(unit[0])}, but HDB lists block "
                f"{block.block} {block.street} as {block.max_floor} storeys high{address.also}",
                hdb.SOURCE, address, expected=f"a storey from 1 to {block.max_floor}",
            ))
        if address.residential and block.residential and unit is None:
            findings.append(_finding(
                "REG.UNIT_MISSING", "signal",
                f"{address.label} gives no unit number, but HDB block {block.block} {block.street} is a block of "
                f"flats: a home there has one{address.also}",
                hdb.SOURCE, address, expected="a unit number (#storey-unit)",
            ))
        if address.residential and not block.residential:
            findings.append(_finding(
                "REG.RESIDENTIAL_ADDRESS", "inconsistent",
                f"{address.label} is HDB block {block.block} {block.street}, which HDB lists as having no residential "
                "units" + (" (commercial use)" if block.commercial else "") + address.also,
                hdb.SOURCE, address, expected="a residential building",
            ))
        if address.company and block.residential and not block.commercial:
            findings.append(_finding(
                "REG.COMPANY_ADDRESS_RESIDENTIAL", "signal",
                f"{address.label} is HDB block {block.block} {block.street}, which HDB lists as flats only, with no "
                f"commercial units: the company is registered at a home{address.also}",
                hdb.SOURCE, address, expected="a commercial or industrial building",
                severity_note="Allowed under HDB's Home Office Scheme for small businesses; worth confirming against "
                              "the company's size and activity",
            ))
        return findings, []

    zone, zone_skip = await _zone(address, building)
    approved, ura_skip = await _approved_use(address, building)
    skips = [skip for skip in (zone_skip, ura_skip) if skip]
    where = f"#{unit[0]}-{unit[1]} at " if unit else ""
    building_name = f"{building.get('BLK_NO')} {building.get('ROAD_NAME')}"
    zoned = f" (zoned {zone.title()})" if zone else ""

    if address.residential:
        if zone in NO_HOMES_ZONES:
            findings.append(_finding(
                "REG.RESIDENTIAL_ADDRESS", "inconsistent",
                f"{address.label} is on a site URA zones {zone.title()} (industrial), where its Master Plan allows "
                f"no residential use{address.also}",
                land_use.SOURCE, address, expected="a residential building",
                severity_note="Zoning is the planned use, looked up at the postal code's map point; a site on a "
                              "zone boundary can read as its neighbour's zone",
            ))
        elif approved is False and unit is None and zone not in SHOPHOUSE_ZONES:
            # A landed house is approved without a unit; a condominium only with one, and an office never.
            findings.append(_finding(
                "REG.UNIT_MISSING", "signal",
                f"{address.label} gives no unit number, and URA has no approval for residential use for {building_name}"
                f"{zoned} as given: a home in a condominium has a unit number, and a building with none approved "
                f"isn't a home{address.also}",
                ura.SOURCE, address, expected="a unit number (#storey-unit), or a house approved for residential use",
                severity_note="URA's approved-use data leaves out shophouse homes and developments without TOP",
            ))
        elif approved is False and unit and zone not in SHOPHOUSE_ZONES:
            # "implausible" needs a KNOWN zone that is neither meant for homes nor shophouse land: with no zone
            # (no index, or the point is in no polygon) nothing says it isn't a home, so it's only a signal.
            if zone is None:
                severity, what = "signal", ("zoning wasn't available for this site, so it can't be told whether the "
                                            "unit is a non-home unit or one URA has no record of")
            elif zone in HOME_ZONES:
                severity, what = "signal", "the unit may not exist"
            else:
                severity, what = "implausible", ("it is an office, shop or other non-home unit, or a unit that "
                                                 "doesn't exist")
            findings.append(_finding(
                "REG.RESIDENTIAL_ADDRESS", severity,
                f"URA has no approval for residential use for {where}{building_name}{zoned}, the unit "
                f"{address.label} gives: {what}{address.also}",
                ura.SOURCE, address, expected="a unit approved for residential use",
                severity_note="URA's approved-use data leaves out shophouse homes and developments without TOP, "
                              "and \"no approval\" also covers a unit it has no record of",
            ))

    if address.company:
        if approved:
            findings.append(_finding(
                "REG.COMPANY_ADDRESS_RESIDENTIAL", "signal",
                f"URA has {where}{building_name} approved for residential use: {address.label} is a private home, "
                f"so the company is registered at a home{address.also}",
                ura.SOURCE, address, expected="a commercial or industrial address",
                severity_note="Allowed under URA's Home Office Scheme for small businesses; worth confirming against "
                              "the company's size and activity",
            ))
        elif approved is None and zone == HOMES_ONLY_ZONE:
            # Only without URA's answer: "NA" (not approved, or no record) is the weaker signal and outweighs the zone.
            findings.append(_finding(
                "REG.COMPANY_ADDRESS_RESIDENTIAL", "signal",
                f"{address.label} is on a site URA zones purely Residential (a private home: house or "
                f"condominium): the company is registered at a home{address.also}",
                land_use.SOURCE, address, expected="a commercial or industrial building",
                severity_note="Allowed under URA's Home Office Scheme for small businesses; worth confirming against "
                              "the company's size and activity",
            ))
    return findings, skips


async def _approved_use(address: _Address, building: dict) -> tuple[bool | None, SkippedCheck | None]:
    """URA's answer for the address (True: approved home; False: "NA"), or why there is none."""
    block, road = str(building.get("BLK_NO") or ""), str(building.get("ROAD_NAME") or "")
    if not ura.configured() or not block or block.upper() == "NIL" or not road:
        return None, None
    try:
        return await ura.approved_residential_use(block, road, ura.unit_of(address.text)), None
    except LookupUnavailable as exc:
        return None, SkippedCheck(check_code="REG.BUILDING_USE", reason=f"{address.field}: URA approved use unavailable: {exc}")


async def _check_one(address: _Address) -> Outcome:
    results = await onemap.addresses_at_postal_code(address.postal)
    if not results:
        return [_finding(
            "REG.POSTAL_CODE_EXISTS", "impossible",
            f"Postal code {address.postal} in {address.label} doesn't exist in OneMap's register of Singapore "
            f"addresses{address.also}",
            onemap.SOURCE, address,
        )], []
    if not _matches(address.text, results):
        actual = "; ".join(str(r.get("ADDRESS")) for r in results[:3])
        return [_finding(
            "REG.ADDRESS_MATCH", "inconsistent",
            f"{address.label} gives \"{address.text}\", but postal code {address.postal} is {actual}{address.also}",
            onemap.SOURCE, address, expected=actual,
        )], []
    return await _building_use(address, results)


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
            found, skips = await _check_one(address)
            findings += found
            skipped += skips
        except LookupUnavailable as exc:
            skipped.append(SkippedCheck(check_code="REG.ADDRESS", reason=f"{address.field}: address lookup unavailable: {exc}"))
    return findings, skipped
