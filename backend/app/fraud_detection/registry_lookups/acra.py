"""ACRA's register, via data.gov.sg: does the company exist, under this name, and is it live?

Two data.gov.sg collections, both published by ACRA and updated monthly:
* "Registered Entities with Unique Entity Number" (collection 1): searchable by UEN.
  Gives the registered name, status, issue date and registered street/postal code.
* "ACRA Information on Corporate Entities" (collection 2): the detailed record
  (incorporation date, full registered address, up to 15 former names), split into 27
  datasets by the first letter of the entity name. Found from collection 1's name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from ...case_match import company_names_match, normalize_uen
from ..numerical_validation.common import _parse_field_date
from ..numerical_validation.models import SkippedCheck
from .datagov import datastore_search
from .http import LookupUnavailable
from .models import RegistryFinding

SOURCE = "ACRA (data.gov.sg)"
UEN_DATASETS = (
    "d_3f960c10fed6145404ca7b821f263b87",  # Entities Registered with ACRA
    "d_b1d2b840ab9e993570c037b706b39bb8",  # Entities Registered with Other UEN Issuance Agencies
)
DETAIL_DATASETS = {
    "A": "d_8575e84912df3c28995b8e6e0e05205a", "B": "d_3a3807c023c61ddfba947dc069eb53f2",
    "C": "d_c0650f23e94c42e7a20921f4c5b75c24", "D": "d_acbc938ec77af18f94cecc4a7c9ec720",
    "E": "d_124a9bd407c7a25f8335b93b86e50fdd", "F": "d_4526d47d6714d3b052eed4a30b8b1ed6",
    "G": "d_b58303c68e9cf0d2ae93b73ffdbfbfa1", "H": "d_fa2ed456cf2b8597bb7e064b08fc3c7c",
    "I": "d_85518d970b8178975850457f60f1e738", "J": "d_478f45a9c541cbe679ca55d1cd2b970b",
    "K": "d_5573b0db0575db32190a2ad27919a7aa", "L": "d_a2141adf93ec2a3c2ec2837b78d6d46e",
    "M": "d_9af9317c646a1c881bb5591c91817cc6", "N": "d_67e99e6eabc4aad9b5d48663b579746a",
    "O": "d_5c4ef48b025fdfbc80056401f06e3df9", "P": "d_181005ca270b45408b4cdfc954980ca2",
    "Q": "d_4130f1d9d365d9f1633536e959f62bb7", "R": "d_2b8c54b2a490d2fa36b925289e5d9572",
    "S": "d_df7d2d661c0c11a7c367c9ee4bf896c1", "T": "d_72f37e5c5d192951ddc5513c2b134482",
    "U": "d_0cc5f52a1f298b916f317800251057f3", "V": "d_e97e8e7fc55b85a38babf66b0fa46b73",
    "W": "d_af2042c77ffaf0db5d75561ce9ef5688", "X": "d_1cd970d8351b42be4a308d628a6dd9d3",
    "Y": "d_31af23fdb79119ed185c256f03cb5773", "Z": "d_4e3db8955fdcda6f9944097bef3d2724",
    "OTHERS": "d_300ddc8da4e8f7bdc1bfc62d0d99e2e7",
}
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


async def lookup_entity(uen: str) -> AcraEntity | None:
    basic = None
    for dataset in UEN_DATASETS:
        rows = await datastore_search(dataset, {"uen": uen}, limit=1)
        if rows:
            basic = rows[0]
            break
    if basic is None:
        return None
    name = _value(basic, "entity_name")
    letter = name[:1].upper()
    detail_rows = await datastore_search(DETAIL_DATASETS.get(letter if letter.isalpha() else "OTHERS",
                                                            DETAIL_DATASETS["OTHERS"]), {"uen": uen}, limit=1)
    detail = detail_rows[0] if detail_rows else {}
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
    )


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
        entity = await lookup_entity(uen)
    except LookupUnavailable as exc:
        return [], [SkippedCheck(check_code="REG.ACRA_ENTITY", reason=f"ACRA lookup unavailable: {exc}")]

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
