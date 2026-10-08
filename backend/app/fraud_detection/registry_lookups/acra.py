"""ACRA's register, via data.gov.sg: does the company exist, under this name, and is it live?

Two data.gov.sg collections, both published by ACRA and updated monthly:
* "Registered Entities with Unique Entity Number" (collection 1): searchable by UEN.
  Gives the registered name, status, issue date and registered street/postal code.
* "ACRA Information on Corporate Entities" (collection 2): the detailed record
  (incorporation date, full registered address, up to 15 former names), split into 27
  datasets by the first letter of the entity name. Found from collection 1's name.

The datasets in each collection are looked up from data.gov.sg's catalogue (cached for an
hour), never hardcoded, so a republish under new dataset IDs is picked up on its own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from ...case_match import company_names_match, normalize_uen
from ..numerical_validation.common import _parse_field_date
from ..numerical_validation.models import SkippedCheck
from .datagov import collection, dataset_name, datastore_search
from .http import LookupUnavailable
from .models import RegistryFinding

SOURCE = "ACRA (data.gov.sg)"
UEN_COLLECTION = 1  # "Registered Entities with Unique Entity Number"
DETAIL_COLLECTION = 2  # "ACRA Information on Corporate Entities"
# Collection 2's datasets are named "ACRA Information on Corporate Entities ('O')".
_LETTER_RE = re.compile(r"\('([^']+)'\)\s*$")
# ACRA publishes monthly. Past this, "not in the register" may just mean the copy is old.
_STALE_AFTER_DAYS = 45
# Where a document's UEN lives: a NOA's tax reference number and a FATCA form's TIN are the UEN.
UEN_FIELDS = ("uen", "tax_reference_number", "tin")
# Types whose company_address is the company's current registered address, so it should match ACRA's.
REGISTERED_ADDRESS_TYPES = frozenset({"certificate_of_incumbency"})

_GONE_RE = re.compile(r"struck off|deregistered|dissolved|ceased|cancel|terminated|expired|amalgamated", re.I)
_DISTRESS_RE = re.compile(r"liquidation|judicial management|receivership|winding up", re.I)
_POSTAL_RE = re.compile(r"\b(\d{6})\b")


@dataclass(frozen=True)
class AcraEntity:
    uen: str
    name: str
    status: str
    incorporation_date: date | None
    postal_code: str | None
    address: str
    former_names: tuple[str, ...]


def _value(record: dict, key: str) -> str:
    value = str(record.get(key) or "").strip()
    return "" if value.lower() == "na" else value


async def _uen_datasets() -> tuple[list[str], datetime | None]:
    """Collection 1's datasets, ACRA's own first (it registers most companies)."""
    uen_collection = await collection(UEN_COLLECTION)
    names = {dataset_id: await dataset_name(dataset_id) for dataset_id in uen_collection.dataset_ids}
    ordered = sorted(uen_collection.dataset_ids, key=lambda d: 0 if "with ACRA" in names[d] else 1)
    return ordered, uen_collection.last_updated


async def _detail_dataset(entity_name: str) -> str | None:
    """The collection 2 dataset holding names starting with this entity's first letter."""
    letter = entity_name[:1].upper()
    wanted = letter if letter.isalpha() else "OTHERS"
    for dataset_id in (await collection(DETAIL_COLLECTION)).dataset_ids:
        match = _LETTER_RE.search(await dataset_name(dataset_id))
        if match and match.group(1).upper() == wanted:
            return dataset_id
    return None


async def _detail_record(uen: str, entity_name: str) -> dict:
    """The detailed record, or {} if it can't be fetched: it only enriches the basic one."""
    try:
        dataset_id = await _detail_dataset(entity_name)
        rows = await datastore_search(dataset_id, {"uen": uen}, limit=1) if dataset_id else []
    except LookupUnavailable:
        return {}
    return rows[0] if rows else {}


def _is_stale(last_updated: datetime | None) -> bool:
    if last_updated is None:
        return False
    updated = last_updated if last_updated.tzinfo else last_updated.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - updated).days > _STALE_AFTER_DAYS


async def lookup_entity(uen: str) -> tuple[AcraEntity | None, datetime | None]:
    """The entity, and when ACRA's register was last updated (to judge a "not found")."""
    datasets, last_updated = await _uen_datasets()
    basic = None
    for dataset_id in datasets:
        rows = await datastore_search(dataset_id, {"uen": uen}, limit=1)
        if rows:
            basic = rows[0]
            break
    if basic is None:
        return None, last_updated
    name = _value(basic, "entity_name")
    detail = await _detail_record(uen, name)
    address_parts = [_value(detail, k) for k in ("block", "street_name", "building_name")]
    unit = "-".join(p for p in (_value(detail, "level_no"), _value(detail, "unit_no")) if p)
    postal = _value(detail, "postal_code") or _value(basic, "reg_postal_code")
    address = " ".join(p for p in address_parts if p) or _value(basic, "reg_street_name")
    if unit:
        address += f" #{unit}"
    if postal:
        address += f" SINGAPORE {postal}"
    return AcraEntity(
        uen=uen,
        name=name,
        status=_value(detail, "entity_status_description") or _value(basic, "uen_status_desc"),
        incorporation_date=_parse_field_date(_value(detail, "registration_incorporation_date")
                                             or _value(basic, "uen_issue_date")),
        postal_code=postal or None,
        address=address,
        former_names=tuple(n for i in range(1, 16) if (n := _value(detail, f"former_entity_name{i}"))),
    ), last_updated


def _finding(code: str, severity: Any, summary: str, **kwargs: Any) -> RegistryFinding:
    return RegistryFinding(check_code=code, severity=severity, summary=summary, source=SOURCE, **kwargs)


def _uen(fields: dict[str, Any]) -> tuple[str, str] | None:
    for name in UEN_FIELDS:
        uen = normalize_uen(fields.get(name))
        if uen:
            return name, uen
    return None


async def check_entity(fields: dict[str, Any], document_type: str | None) -> tuple[list[RegistryFinding], list[SkippedCheck]]:
    found = _uen(fields)
    if found is None:
        return [], [SkippedCheck(check_code="REG.ACRA_ENTITY", reason="No UEN extracted from this document")]
    uen_field, uen = found
    try:
        entity, last_updated = await lookup_entity(uen)
    except LookupUnavailable as exc:
        return [], [SkippedCheck(check_code="REG.ACRA_ENTITY", reason=f"ACRA lookup unavailable: {exc}")]

    if entity is None and _is_stale(last_updated):
        return [], [SkippedCheck(
            check_code="REG.UEN_EXISTS",
            reason=f"UEN {uen} isn't in ACRA's register, but data.gov.sg's copy was last updated "
                   f"{last_updated.date().isoformat() if last_updated else 'at an unknown date'}, so a "
                   "recently registered company may be missing",
        )]
    if entity is None:
        return [_finding(
            "REG.UEN_EXISTS", "impossible",
            f"UEN {uen} is not in ACRA's register or any other UEN issuance agency's register",
            field=uen_field, stated=uen,
            severity_note="The register is published monthly, so a company registered in the last few "
                          "weeks may not appear yet",
        )], []

    findings = _status_findings(entity, uen_field)
    findings += _name_findings(entity, fields.get("company_name"))
    findings += _date_findings(entity, fields.get("incorporation_date"))
    findings += _address_findings(entity, fields.get("company_address"), document_type)
    return findings, []


def _status_findings(entity: AcraEntity, uen_field: str) -> list[RegistryFinding]:
    if _GONE_RE.search(entity.status):
        severity, meaning = "impossible", "no longer exists as a registered entity"
    elif _DISTRESS_RE.search(entity.status):
        severity, meaning = "implausible", "is being wound up or is under an insolvency process"
    else:
        return []
    return [_finding(
        "REG.ENTITY_STATUS", severity,
        f"ACRA lists {entity.name} ({entity.uen}) as \"{entity.status}\": the company {meaning}",
        field=uen_field, stated=entity.uen, expected="a live company",
    )]


def _name_findings(entity: AcraEntity, stated: Any) -> list[RegistryFinding]:
    if not isinstance(stated, str) or not stated.strip() or company_names_match(stated, entity.name):
        return []
    former = next((n for n in entity.former_names if company_names_match(stated, n)), None)
    if former:
        return [_finding(
            "REG.ENTITY_NAME", "signal",
            f"The document names the company \"{stated}\", a former name of {entity.uen}; ACRA's current name "
            f"is \"{entity.name}\"",
            field="company_name", stated=stated, expected=entity.name,
            severity_note="Genuine if the document predates the name change",
        )]
    return [_finding(
        "REG.ENTITY_NAME", "inconsistent",
        f"The document names the company \"{stated}\", but ACRA registers UEN {entity.uen} as \"{entity.name}\"",
        field="company_name", stated=stated, expected=entity.name,
    )]


def _date_findings(entity: AcraEntity, stated: Any) -> list[RegistryFinding]:
    stated_date = _parse_field_date(stated)
    if stated_date is None or entity.incorporation_date is None or stated_date == entity.incorporation_date:
        return []
    return [_finding(
        "REG.INCORPORATION_DATE", "inconsistent",
        f"The document gives an incorporation date of {stated_date.isoformat()}, but ACRA's register says "
        f"{entity.incorporation_date.isoformat()}",
        field="incorporation_date", stated=stated_date.isoformat(), expected=entity.incorporation_date.isoformat(),
    )]


def _address_findings(entity: AcraEntity, stated: Any, document_type: str | None) -> list[RegistryFinding]:
    match = _POSTAL_RE.search(str(stated or ""))
    if not match or not entity.postal_code or match.group(1) == entity.postal_code:
        return []
    registered = document_type in REGISTERED_ADDRESS_TYPES
    return [_finding(
        "REG.REGISTERED_ADDRESS", "inconsistent" if registered else "signal",
        f"The document's company address has postal code {match.group(1)}, but ACRA's registered address "
        f"is {entity.address}",
        field="company_address", stated=str(stated), expected=entity.address,
        severity_note=None if registered else
        "A company can move after a document is issued, and ACRA's dataset is updated monthly",
    )]
