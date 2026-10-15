"""
Content Validation Specialist — "Company Identity Consistency" (SCRUM-255).

As a loan officer, I want the company name compared across every company
document in a case so that documents with discrepancies are flagged.

AC1: company names that differ only by how the legal suffix is written
     ("Pte Ltd" vs "Pte. Ltd." vs "Private Limited") are treated as
     matching. Letter case and "&" vs "AND" are ignored too. A suffix that
     states a different entity type ("LLP" vs "Pte Ltd") is a discrepancy;
     a name with no suffix at all (a bank's account name) matches any type.
     A bank statement's account name cut off at a word boundary ("CRIMSON
     TEXTILE" for "Crimson Textile Manufacturing Pte Ltd") also matches.
     Every other difference, including spacing and punctuation within the
     name, counts as a discrepancy.
AC2: company names that don't otherwise exactly match are flagged to the
     officer — one finding per distinct conflicting name, listing every
     document in the case that carries it. Run by the Content Validation
     specialist's company_name_consistency_tool, whose findings can't be
     dismissed or rated below medium (see specialists._MIN_SEVERITY_BY_TOOL),
     so they always flag the document.

Every document in the case is compared except IDs (NRIC, passport), proof
of address (utility bills and personal bank statements), rejected
documents and documents still being extracted. A document with no
company_name (e.g. a payslip with no employer) is fine: it just has
nothing to compare. An unrecognised document can name several companies
(a purchase order's buyer and supplier), so it matches when the name being
compared appears in any of its extracted values, not only company_name.

The document under review is compared against every other document in its
case, not just the certificate of incumbency. Deliberately separate from
case_match.py: that's the rough "does this upload belong to this case"
gate run at upload time, and its fuzzy company-name match is too lenient
for a fraud check.

Works from extracted_fields alone — no file download or re-ingestion.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field

from ...models import DocumentRecord, DocumentStatus, DocumentType, is_personal_bank_statement

# Legal-form words, matched case-insensitively and with their punctuation
# ("Pte.", "Ltd.,") as a run at the END of the name only — "Co" in the
# middle of a name is part of the name.
_LEGAL_SUFFIX_WORDS = (
    "PTE", "PRIVATE", "LTD", "LIMITED", "LLP", "LLC", "LP", "INC", "INCORPORATED",
    "CORP", "CORPORATION", "CO", "COMPANY", "PLC", "BHD", "BERHAD", "SDN", "SENDIRIAN",
    "LIABILITY", "PARTNERSHIP",
)
_LEGAL_SUFFIX_RE = re.compile(
    r"(?:[\s,.]*\b(?:" + "|".join(_LEGAL_SUFFIX_WORDS) + r")\b\.?)+[\s,.]*$",
    re.IGNORECASE,
)
_AMPERSAND_RE = re.compile(r"\s*&\s*|\s+and\s+", re.IGNORECASE)

# The legal suffix also states the entity type, and an LLP is not a Pte Ltd
# even when the rest of the name is identical. Abbreviations are spelled out
# first, then spellings of the same type share one label. A suffix not
# listed here is its own type.
_SUFFIX_ABBREVIATIONS = {
    "PTE": "PRIVATE", "LTD": "LIMITED", "INC": "INCORPORATED", "CORP": "CORPORATION",
    "CO": "COMPANY", "SDN": "SENDIRIAN", "BHD": "BERHAD",
}
_ENTITY_TYPES = {
    ("PRIVATE", "LIMITED"): "Pte Ltd",
    ("COMPANY", "LIMITED"): "Pte Ltd",
    # "PTE" alone: a name cut short before "LTD" (a bank's account-name field).
    ("PRIVATE",): "Pte Ltd",
    ("LLP",): "LLP",
    ("LIMITED", "LIABILITY", "PARTNERSHIP"): "LLP",
    ("LP",): "LP",
    ("LIMITED", "PARTNERSHIP"): "LP",
    ("LLC",): "LLC",
    ("LIMITED", "LIABILITY", "COMPANY"): "LLC",
    ("SENDIRIAN", "BERHAD"): "Sdn Bhd",
}

# Not compared: ID documents and proof of address. A bank statement held by
# an individual is proof of address too (see models.is_personal_bank_statement).
_EXCLUDED_DOCUMENT_TYPES: frozenset[str] = frozenset({
    DocumentType.NRIC.value,
    DocumentType.PASSPORT.value,
    DocumentType.PROOF_OF_ADDRESS.value,
})

# Rejected, or not extracted yet so its company_name isn't known.
_EXCLUDED_STATUSES: frozenset[DocumentStatus] = frozenset({
    DocumentStatus.REJECTED,
    DocumentStatus.UPLOADED, DocumentStatus.QUEUED, DocumentStatus.PROCESSING,
    DocumentStatus.EXTRACTING,
})


class ComparedDocument(BaseModel):
    document_id: str
    filename: str
    document_type: Optional[str] = None
    company_name: str
    matches: bool


class CompanyNameDiscrepancy(BaseModel):
    """One company name, different from this document's, and every document that carries it."""

    stated: str  # this document's company_name
    conflicting: str  # the other documents' company_name, as first seen
    document_ids: list[str]
    filenames: list[str]
    severity: str = "inconsistent"
    summary: str


class CompanyNameConsistencyResult(BaseModel):
    document_id: str
    company_name: Optional[str] = None
    compared_documents: list[ComparedDocument] = Field(default_factory=list)
    discrepancies: list[CompanyNameDiscrepancy] = Field(default_factory=list)
    # Why nothing was compared, when nothing was. Not an error: an ID,
    # a proof of address or a document with no company name simply has
    # nothing to check.
    skipped_reason: Optional[str] = None


def normalize_company_name(value: object) -> Optional[str]:
    """
    Ignores letter case, the trailing legal suffix and "&" vs "AND" (plus
    surrounding whitespace). Everything else must match exactly, so
    "A.B.C." vs "ABC" and "ACME" vs "ACNE" are discrepancies.
    """
    if not isinstance(value, str):
        return None
    name = _LEGAL_SUFFIX_RE.sub("", value.strip())
    return _normalize_text(name) or None


def _normalize_text(value: str) -> str:
    return " ".join(_AMPERSAND_RE.sub(" & ", value.upper()).split())


def legal_entity_type(value: object) -> Optional[str]:
    """The entity type the name's legal suffix states ("Pte Ltd", "LLP", ...), or None without one."""
    if not isinstance(value, str):
        return None
    suffix = _LEGAL_SUFFIX_RE.search(value.strip())
    tokens: list[str] = re.findall(r"[A-Z]+", suffix.group(0).upper()) if suffix else []
    words = tuple(_SUFFIX_ABBREVIATIONS.get(token, token) for token in tokens)
    if not words:
        return None
    return _ENTITY_TYPES.get(words, " ".join(words).title())


def _entity_types_agree(a: object, b: object) -> bool:
    """A name with no suffix (a bank's account name) says nothing about the type, so it agrees."""
    type_a, type_b = legal_entity_type(a), legal_entity_type(b)
    return type_a is None or type_b is None or type_a == type_b


def cross_document_company_names_match(a: object, b: object) -> bool:
    """Same name once the legal suffix is set aside, and the suffixes don't state different entity types."""
    normalized_a, normalized_b = normalize_company_name(a), normalize_company_name(b)
    return normalized_a is not None and normalized_a == normalized_b and _entity_types_agree(a, b)


def _entity_type_note(a: str, b: str) -> str:
    """Says so when the names differ only in the entity type their suffixes state."""
    if normalize_company_name(a) != normalize_company_name(b) or _entity_types_agree(a, b):
        return ""
    return f" The legal entity type differs ({legal_entity_type(a)} vs {legal_entity_type(b)})."


# A cut-off account name keeps at least this many words of the full name.
_MIN_ACCOUNT_NAME_WORDS = 2


def _account_name_cut_short(document: DocumentRecord, account_name: str, full_name: str) -> bool:
    """
    A bank statement's account name that the bank cut off at a word
    boundary: OCBC prints one line of the name, so "CRIMSON TEXTILE
    MANUFACTURING PTE LTD" comes out as "CRIMSON TEXTILE". It matches when
    its words are the first whole words of the full name.

    Bank statements only: elsewhere a shorter name is a different name. A
    cut-off name has lost its legal suffix, so one that still carries a
    suffix ("CRIMSON TEXTILE PTE LTD") is a different, shorter company,
    and a partial word ("CRIMSON TEX") or a single word ("CRIMSON") is not
    accepted.
    """
    if document.document_type != DocumentType.BANK_STATEMENT.value:
        return False
    if legal_entity_type(account_name) is not None:
        return False
    short, full = normalize_company_name(account_name), normalize_company_name(full_name)
    if short is None or full is None:
        return False
    short_words, full_words = short.split(), full.split()
    return (
        _MIN_ACCOUNT_NAME_WORDS <= len(short_words) < len(full_words)
        and full_words[: len(short_words)] == short_words
    )


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    return []


def _mentions(document: DocumentRecord, company_name: str) -> bool:
    """Whether the company name appears, as whole words, in any of the document's extracted values."""
    normalized = normalize_company_name(company_name)
    if normalized is None:
        return False
    pattern = re.compile(r"(?<![A-Z0-9])" + re.escape(normalized) + r"(?![A-Z0-9])")
    return any(pattern.search(_normalize_text(s)) for s in _strings(document.extracted_fields or {}))


def _company_name(fields: Optional[dict]) -> Optional[str]:
    value = (fields or {}).get("company_name")
    return value.strip() if isinstance(value, str) and value.strip() else None


def is_compared_document(document: DocumentRecord) -> bool:
    """Whether the document's company name takes part in the comparison."""
    if document.document_type in _EXCLUDED_DOCUMENT_TYPES:
        return False
    return not is_personal_bank_statement(document.document_type, document.extracted_fields)


def check_company_name_consistency(
    document: DocumentRecord, case_documents: list[DocumentRecord]
) -> CompanyNameConsistencyResult:
    """
    `document` must carry its extracted_fields; `case_documents` is every
    document in its case (the document itself may be among them).
    """
    result = CompanyNameConsistencyResult(document_id=document.id)

    if not is_compared_document(document):
        result.skipped_reason = "ID and proof-of-address documents are not compared"
        return result

    company_name = _company_name(document.extracted_fields)
    result.company_name = company_name
    if company_name is None:
        result.skipped_reason = "No company name was extracted from this document"
        return result

    others = [
        other for other in case_documents
        if other.id != document.id
        and other.status not in _EXCLUDED_STATUSES
        and is_compared_document(other)
        and _company_name(other.extracted_fields) is not None
    ]
    if not others:
        result.skipped_reason = "No other document in the case has a company name to compare against"
        return result
    by_name: dict[tuple[str, Optional[str]], list[DocumentRecord]] = {}
    for other in others:
        other_name = _company_name(other.extracted_fields)
        assert other_name is not None  # filtered above
        matches = (
            cross_document_company_names_match(company_name, other_name)
            or _account_name_cut_short(document, company_name, other_name)
            or _account_name_cut_short(other, other_name, company_name)
            or (other.document_type == DocumentType.UNRECOGNISED.value and _mentions(other, company_name))
        )
        result.compared_documents.append(ComparedDocument(
            document_id=other.id,
            filename=other.filename,
            document_type=other.document_type,
            company_name=other_name,
            matches=matches,
        ))
        if not matches:
            key = (normalize_company_name(other_name) or other_name, legal_entity_type(other_name))
            by_name.setdefault(key, []).append(other)

    for group in by_name.values():
        conflicting = _company_name(group[0].extracted_fields)
        assert conflicting is not None
        filenames = [d.filename for d in group]
        result.discrepancies.append(CompanyNameDiscrepancy(
            stated=company_name,
            conflicting=conflicting,
            document_ids=[d.id for d in group],
            filenames=filenames,
            summary=(
                f'Company name "{company_name}" differs from "{conflicting}" on '
                f"{len(group)} other document(s) in the case: {', '.join(filenames)}."
                + _entity_type_note(company_name, conflicting)
            ),
        ))
    return result
