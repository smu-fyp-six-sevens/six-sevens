"""Extract, normalize, and compare personal and corporate addresses."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable

from ...models import DocumentRecord, is_personal_bank_statement

_PERSONAL_DOCUMENT_TYPES = {
    "credit_report",
    "nric",
    "passport",
    "payslip",
    "proof_of_address",
}
_COMPANY_ADDRESS_FIELDS = (
    "company_address",
    "company_address_lines",
    "registered_address",
    "registered_office_address",
    "business_address",
)
_PERSON_ADDRESS_FIELDS = ("individual_address", "residential_address")
_PERSON_LIST_FIELDS = ("officers", "shareholders", "subscribers")
_ID_FIELDS = ("nric_number", "id_number", "identification_no", "id")
_ADDRESS_ROLE_RE = re.compile(
    r"^(?P<role>director|secretary|shareholder(?:_\d+)?|officer(?:_\d+)?|"
    r"member(?:_\d+)?|partner(?:_\d+)?|manager(?:_\d+)?)_address$",
    re.IGNORECASE,
)
_POSTAL_CODE_RE = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_COMPANY_SUFFIXES = {
    "PTE", "PRIVATE", "LTD", "LIMITED", "LLP", "LLC", "INC", "INCORPORATED",
    "CORP", "CORPORATION", "CO", "COMPANY", "PLC", "BHD", "SDN",
}
_ADDRESS_ABBREVIATIONS = {
    "AVE": "AVENUE",
    "AV": "AVENUE",
    "BLK": "BLOCK",
    "BL": "BLOCK",
    "BLDG": "BUILDING",
    "CRES": "CRESCENT",
    "CT": "COURT",
    "DR": "DRIVE",
    "JLN": "JALAN",
    "LN": "LANE",
    "PK": "PARK",
    "RD": "ROAD",
    "ST": "STREET",
    "TER": "TERRACE",
}


@dataclass(frozen=True)
class AddressRecord:
    document_id: str
    filename: str
    field_name: str
    value: str
    scope: str
    identity_keys: frozenset[str] = frozenset()
    company_key: str | None = None


@dataclass(frozen=True)
class AddressMismatch:
    first: AddressRecord
    second: AddressRecord
    reason: str


def normalize_address(value: object) -> str:
    """Canonicalize punctuation, case, spacing, and common address abbreviations."""
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(char for char in text if not unicodedata.combining(char)).upper()
    text = re.sub(r"#\s*", " UNIT ", text)
    tokens = re.findall(r"[A-Z0-9]+", text)
    return " ".join(_ADDRESS_ABBREVIATIONS.get(token, token) for token in tokens)


def postal_code(value: object) -> str | None:
    """Return the Singapore six-digit postal code present in an address."""
    if not isinstance(value, str):
        return None
    matches = _POSTAL_CODE_RE.findall(value)
    return matches[-1] if matches else None


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            joined = " ".join(item.strip() for item in value if item.strip())
            return [joined] if joined else []
        return [text for item in value for text in _strings(item)]
    return []


def _normalize_person_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    tokens = re.findall(r"[A-Z0-9]+", value.upper())
    return " ".join(sorted(tokens)) or None


def _identity_keys(name: object, id_number: object) -> frozenset[str]:
    keys: set[str] = set()
    if isinstance(id_number, str):
        normalized_id = re.sub(r"[^A-Z0-9]", "", id_number.upper())
        if normalized_id:
            keys.add(f"id:{normalized_id}")
    normalized_name = _normalize_person_name(name)
    if normalized_name:
        keys.add(f"name:{normalized_name}")
    return frozenset(keys)


def _company_key(fields: dict) -> str | None:
    for field in ("uen", "tax_reference_number", "tin"):
        values = _strings(fields.get(field))
        if values:
            normalized_id = re.sub(r"[^A-Z0-9]", "", values[0].upper())
            if normalized_id:
                return f"id:{normalized_id}"
    names = _strings(fields.get("company_name"))
    if not names:
        return None
    tokens = re.findall(r"[A-Z0-9]+", names[0].upper())
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return f"name:{' '.join(tokens)}" if tokens else None


def _first(fields: dict, keys: Iterable[str]) -> str | None:
    for key in keys:
        values = _strings(fields.get(key))
        if values:
            return values[0]
    return None


def _add_record(
    records: list[AddressRecord],
    doc: DocumentRecord,
    field_name: str,
    value: object,
    scope: str,
    *,
    identity_keys: frozenset[str] = frozenset(),
    company_key: str | None = None,
) -> None:
    for address in _strings(value):
        if not normalize_address(address):
            continue
        record = AddressRecord(
            document_id=doc.id,
            filename=doc.filename,
            field_name=field_name,
            value=address,
            scope=scope,
            identity_keys=identity_keys,
            company_key=company_key,
        )
        if record not in records:
            records.append(record)


def extract_addresses(doc: DocumentRecord) -> list[AddressRecord]:
    """Collect address fields and the person/company each field belongs to."""
    fields = doc.extracted_fields or {}
    if not isinstance(fields, dict):
        return []

    records: list[AddressRecord] = []
    company_key = _company_key(fields)
    personal_doc = (
        doc.document_type in _PERSONAL_DOCUMENT_TYPES
        or is_personal_bank_statement(doc.document_type, fields)
        or (
            doc.document_type == "source_of_wealth"
            and bool(fields.get("account_holder_name") or fields.get("individual_name"))
            and not fields.get("uen")
        )
    )

    name = _first(fields, ("individual_name", "account_holder_name"))
    id_number = _first(fields, _ID_FIELDS)
    identity_keys = _identity_keys(name, id_number)
    for field_name in _PERSON_ADDRESS_FIELDS:
        _add_record(
            records, doc, field_name, fields.get(field_name), "personal",
            identity_keys=identity_keys,
        )

    for field_name, value in fields.items():
        role = _ADDRESS_ROLE_RE.match(field_name)
        if not role:
            continue
        prefix = role.group("role")
        role_name = _first(fields, (f"{prefix}_name",))
        role_id = _first(fields, tuple(f"{prefix}_{suffix}" for suffix in _ID_FIELDS))
        _add_record(
            records, doc, field_name, value, "personal",
            identity_keys=_identity_keys(role_name, role_id),
        )

    for list_field in _PERSON_LIST_FIELDS:
        entries = fields.get(list_field)
        if not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            address = entry.get("address") or entry.get("residential_address")
            if not address:
                continue
            person_id = _first(entry, _ID_FIELDS)
            _add_record(
                records, doc, f"{list_field}[{index}].address", address, "personal",
                identity_keys=_identity_keys(entry.get("name"), person_id),
            )

    if not personal_doc:
        for field_name in _COMPANY_ADDRESS_FIELDS:
            _add_record(
                records, doc, field_name, fields.get(field_name), "company",
                company_key=company_key,
            )

    generic_addresses = fields.get("address")
    if generic_addresses:
        if identity_keys:
            _add_record(
                records, doc, "address", generic_addresses, "personal",
                identity_keys=identity_keys,
            )
        elif company_key and not personal_doc:
            _add_record(
                records, doc, "address", generic_addresses, "company",
                company_key=company_key,
            )
    return records


def _same_entity(first: AddressRecord, second: AddressRecord) -> bool:
    if first.scope != second.scope or first.document_id == second.document_id:
        return False
    if first.scope == "personal":
        return bool(first.identity_keys & second.identity_keys)
    return not (first.company_key and second.company_key and first.company_key != second.company_key)


def find_address_mismatches(documents: Iterable[DocumentRecord]) -> list[AddressMismatch]:
    """Compare addresses for the same person/company across different documents."""
    records = [record for doc in documents for record in extract_addresses(doc)]
    mismatches: list[AddressMismatch] = []
    seen: set[tuple[str, str, str, str]] = set()
    for first, second in combinations(records, 2):
        if not _same_entity(first, second):
            continue
        if normalize_address(first.value) == normalize_address(second.value):
            continue

        pair_key = (first.document_id, first.field_name, second.document_id, second.field_name)
        if pair_key in seen:
            continue
        seen.add(pair_key)
        first_postal, second_postal = postal_code(first.value), postal_code(second.value)
        if first_postal and second_postal and first_postal != second_postal:
            reason = (
                f"Postal code {first_postal} in {first.filename} differs from postal code "
                f"{second_postal} in {second.filename}"
            )
        else:
            reason = f"Address in {first.filename} differs from address in {second.filename}"
        mismatches.append(AddressMismatch(first, second, reason))
    return mismatches
