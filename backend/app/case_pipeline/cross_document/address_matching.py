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
    "CTRL": "CENTRAL",
    "CWEALTH": "COMMONWEALTH",
    "CT": "COURT",
    "DR": "DRIVE",
    "JLN": "JALAN",
    "LN": "LANE",
    "LWR": "LOWER",
    "NTH": "NORTH",
    "PK": "PARK",
    "RD": "ROAD",
    "STH": "SOUTH",
    "ST": "STREET",
    "TER": "TERRACE",
    "UPP": "UPPER",
}


@dataclass(frozen=True)
class AddressRecord:
    document_id: str
    filename: str
    field_name: str
    value: str
    scope: str
    identity_keys: frozenset[str] = frozenset()
    # The company the address belongs to: "id:<UEN>" and/or "name:<name without Pte Ltd etc.>".
    company_keys: frozenset[str] = frozenset()


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
    text = re.sub(r"\bC['’]?WEALTH\b", "COMMONWEALTH", text)
    tokens: list[str] = re.findall(r"[A-Z0-9]+", text)
    normalized: list[str] = []
    for index, token in enumerate(tokens):
        # Singapore's older six-digit postal format is often written S123456.
        # It is equivalent to the current Singapore 123456 format.
        if re.fullmatch(r"S\d{6}", token):
            token = token[1:]
        if token == "SINGAPORE":
            continue
        token = _ADDRESS_ABBREVIATIONS.get(token, token)
        # In a Singapore address, "Blk/Block 22" and "22" identify the same
        # block; other uses of "block" (for example "Block A") are retained.
        if token == "BLOCK" and index + 1 < len(tokens) and tokens[index + 1].isdigit():
            continue
        normalized.append(token)
    return " ".join(normalized)


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


def _company_keys(fields: dict) -> frozenset[str]:
    """The document's company, by UEN and by name: either can be missing (a bank statement prints the
    account holder's name but not its UEN)."""
    keys: set[str] = set()
    for field in ("uen", "tax_reference_number", "tin"):
        values = _strings(fields.get(field))
        if values:
            normalized_id = re.sub(r"[^A-Z0-9]", "", values[0].upper())
            if normalized_id:
                keys.add(f"id:{normalized_id}")
                break
    names = _strings(fields.get("company_name"))
    if names:
        tokens = re.findall(r"[A-Z0-9]+", names[0].upper())
        while tokens and tokens[-1] in _COMPANY_SUFFIXES:
            tokens.pop()
        if tokens:
            keys.add(f"name:{' '.join(tokens)}")
    return frozenset(keys)


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
    company_keys: frozenset[str] = frozenset(),
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
            company_keys=company_keys,
        )
        if record not in records:
            records.append(record)


def extract_addresses(doc: DocumentRecord) -> list[AddressRecord]:
    """Collect address fields and the person/company each field belongs to."""
    fields = doc.extracted_fields or {}
    if not isinstance(fields, dict):
        return []

    records: list[AddressRecord] = []
    company_keys = _company_keys(fields)
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
                company_keys=company_keys,
            )

    generic_addresses = fields.get("address")
    if generic_addresses:
        if identity_keys:
            _add_record(
                records, doc, "address", generic_addresses, "personal",
                identity_keys=identity_keys,
            )
        elif company_keys and not personal_doc:
            _add_record(
                records, doc, "address", generic_addresses, "company",
                company_keys=company_keys,
            )
    return records


def _same_entity(first: AddressRecord, second: AddressRecord) -> bool:
    if first.scope != second.scope or first.document_id == second.document_id:
        return False
    if first.scope == "personal":
        return bool(first.identity_keys & second.identity_keys)
    # If extraction did not identify a company, its address could belong to a
    # supplier, customer, or other third party. Do not compare it as though it
    # belonged to the case company.
    if not first.company_keys or not second.company_keys:
        return False
    # The UEN decides when both documents have one: two different UENs are two companies, whatever
    # their names. When only one has it (a bank statement doesn't print the account holder's UEN),
    # the company name decides.
    first_ids = {k for k in first.company_keys if k.startswith("id:")}
    second_ids = {k for k in second.company_keys if k.startswith("id:")}
    if first_ids and second_ids:
        return bool(first_ids & second_ids)
    return bool(first.company_keys & second.company_keys)


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
