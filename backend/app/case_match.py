"""
Case-match check — does each uploaded document actually belong to this
case's company (and its people), as part of fraud detection after approval?

The certificate of incumbency (ACRA Business Profile) is the reference: it's
compulsory for every case and is the one document that authoritatively lists
the company's UEN, name, officers and shareholders. Three steps:

1. Verify the incumbency itself. It's only trusted once at least
   MIN_CORROBORATING_DOCUMENTS other documents name the same company — by
   UEN, or by company name for a document with no UEN (e.g. a corporate
   bank statement). If the other documents instead agree among themselves
   on a *different* company, it's
   the incumbency that looks wrong, so it goes to manual review rather than
   every other document being flagged against a bad reference.
2. Compare each document's company against the incumbency — UEN first when
   the document has one, otherwise company name with fuzzy matching (see
   company_names_match).
3. Compare each individual a document names against the incumbency's
   officers/shareholders — ID number first, otherwise name. Mismatches are
   returned for the fraud detection report; they do not block approval.

Computed live from the documents table on every read, never stored — the same
reasoning as case_service.py: the result depends on every document in the
case, so any new upload can change it, and there's no single event that
would keep a stored copy honest.
"""
from __future__ import annotations

import re
from collections import Counter
from difflib import SequenceMatcher
from enum import Enum
from typing import Iterable, NamedTuple, Optional

from pydantic import BaseModel, Field

from .models import (
    CaseRecord,
    DocumentRecord,
    DocumentStatus,
    DocumentType,
    is_personal_bank_statement,
)

# Step 1: how many other documents must carry the incumbency's UEN before
# it's trusted as the case's reference.
MIN_CORROBORATING_DOCUMENTS = 3

# Where a document's UEN lives, by field name. A notice of assessment's tax
# reference number IS the company's UEN, and a FATCA/CRS declaration's TIN
# is too (see extraction_prompts.EXTRACTION_SCHEMAS's comments on both).
_UEN_FIELDS: tuple[str, ...] = ("uen", "tax_reference_number", "tin")

_INDIVIDUAL_NAME_FIELDS: tuple[str, ...] = ("individual_name", "account_holder_name")
# nric_number, not passport_number: the incumbency lists each person's NRIC,
# which a passport also carries (see mapper._collect_identity_state).
_INDIVIDUAL_ID_FIELDS: tuple[str, ...] = ("nric_number", "id_number")
# Role-prefixed person keys — the certificate of incumbency's extracted
# fields list each officer and shareholder as director_name,
# secretary_name, shareholder_1_name, shareholder_2_name, ... each with a
# matching <prefix>_id_number. The role list keeps out other *_name keys
# that aren't people (company_name, audit_firm_name, bank_name, ...).
_ROLE_NAME_KEY = re.compile(
    r"^(?P<prefix>(?:director|secretary|shareholder|officer|member|partner|manager)"
    r"(?:_\d+)?)_name$"
)
_ROLE_ID_SUFFIXES: tuple[str, ...] = ("_id_number", "_id", "_nric_number")
# Array fields listing several people, each an object with name/id keys —
# in case extraction returns officers/shareholders that way instead.
_PEOPLE_LIST_FIELDS: tuple[str, ...] = ("officers", "shareholders")
# The key each list entry's ID number comes under. Extraction has used
# several over time — the current incumbency schema returns
# "identification_no" (the ACRA table's "Identification No." column) — so
# accept any of them; missing it silently drops every officer's ID.
_LIST_ENTRY_ID_KEYS: tuple[str, ...] = ("id_number", "identification_no", "nric_number", "id")

# Individuals aren't checked on a constitution: its subscribers are the
# people who formed the company, who may no longer be officers or
# shareholders on the current certificate of incumbency. Its UEN/company
# is still checked.
_COMPANY_ONLY_DOCUMENT_TYPES: set[str] = {DocumentType.COMPANY_CONSTITUTION.value}

# Documents about a person rather than the company. Their company_name (if
# any) isn't the applicant company — a payslip's is the employer, which may
# well be somewhere else — so only the individual check applies to them.
_PERSONAL_DOCUMENT_TYPES: set[str] = {
    DocumentType.PAYSLIP.value,
    DocumentType.NRIC.value,
    DocumentType.PASSPORT.value,
    DocumentType.CREDIT_REPORT.value,
}

# Trailing legal-form words dropped before comparing company names, so
# "ACME Pte. Ltd." and "ACME Private Limited" compare equal. Only stripped
# from the END of the name — "CO" in the middle of a name is meaningful.
_COMPANY_SUFFIXES: set[str] = {
    "PTE", "PRIVATE", "LTD", "LIMITED", "LLP", "LLC", "INC", "INCORPORATED",
    "CORP", "CORPORATION", "CO", "COMPANY", "PLC", "BHD", "SDN", "S",
}

# Singapore UEN formats: business (8 digits + letter), local company
# (9 digits + letter), and others (T/S/R + 2 digits + 2 letters + 4 digits +
# letter). Used to pull the UEN out of a value like "UEN: 201912345K".
_UEN_PATTERN = re.compile(r"\d{8,9}[A-Z]|[TSR]\d{2}[A-Z]{2}\d{4}[A-Z]")


class IncumbencyStatus(str, Enum):
    MISSING = "missing"
    # Fewer than MIN_CORROBORATING_DOCUMENTS other documents name its company yet,
    # and nothing contradicts the incumbency — more uploads needed.
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    VERIFIED = "verified"
    MANUAL_REVIEW = "manual_review"


class CheckOutcome(str, Enum):
    MATCH = "match"
    MISMATCH = "mismatch"


class DocumentMatchStatus(str, Enum):
    # Belongs to this case — can proceed to fraud detection (AC2).
    MATCH = "match"
    # Identity differs from another case document. It can still proceed to
    # fraud detection, where the comparison is shown as a finding.
    MISMATCH = "mismatch"
    # Needs an officer's eye because the incumbency itself is in doubt or the
    # document has no company/individual details to compare at all.
    MANUAL_REVIEW = "manual_review"
    # Can't decide yet: not extracted, or the incumbency is missing or not
    # yet corroborated.
    PENDING = "pending"


class FieldComparison(BaseModel):
    """One document-vs-case comparison — AC4's "what caused the mismatch"."""

    check: str  # "company_uen" | "company_name" | "individual_id" | "individual_name"
    outcome: CheckOutcome
    document_field: str
    document_value: str
    # The certificate is the comparison source; include its identity so the
    # finding can point officers to both documents involved.
    reference_document_id: Optional[str] = None
    reference_filename: Optional[str] = None
    other_document_id: Optional[str] = None
    other_filename: Optional[str] = None
    # What the incumbency holds for the same thing — for an individual,
    # every officer/shareholder it was compared against.
    case_value: Optional[str] = None
    reason: str


class IncumbencyPerson(BaseModel):
    name: Optional[str] = None
    id_number: Optional[str] = None


class IncumbencyVerification(BaseModel):
    status: IncumbencyStatus
    document_id: Optional[str] = None
    uen: Optional[str] = None
    company_name: Optional[str] = None
    individuals: list[IncumbencyPerson] = Field(default_factory=list)
    corroborating_document_ids: list[str] = Field(default_factory=list)
    conflicting_document_ids: list[str] = Field(default_factory=list)
    required_corroborations: int = MIN_CORROBORATING_DOCUMENTS
    reasons: list[str] = Field(default_factory=list)


class DocumentCaseMatch(BaseModel):
    document_id: str
    filename: str
    document_type: Optional[str] = None
    status: DocumentMatchStatus
    can_proceed_to_fraud_detection: bool
    # The document's company or an individual on it differs from the case.
    # Mismatches are assessed by fraud detection after approval.
    case_mismatch: bool = False
    reason: str
    comparisons: list[FieldComparison] = Field(default_factory=list)


class CaseMatchResult(BaseModel):
    case_id: str
    incumbency: IncumbencyVerification
    documents: list[DocumentCaseMatch] = Field(default_factory=list)
    mismatched_document_ids: list[str] = Field(default_factory=list)


# --- normalisation ------------------------------------------------------------


def _strings(value: object) -> list[str]:
    """
    Every non-empty string in an extracted value. extracted_fields is LLM
    output, not a validated schema — a field can come back as a string, a
    list of strings, or (for an incumbency listing several people in flat
    fields) one "A; B" joined string.
    """
    if isinstance(value, str):
        return [part.strip() for part in re.split(r"[;\n]", value) if part.strip()]
    if isinstance(value, list):
        return [s for item in value if isinstance(item, str) for s in _strings(item)]
    return []


def normalize_uen(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", value.upper())
    if not cleaned:
        return None
    found = _UEN_PATTERN.search(cleaned)
    return found.group(0) if found else cleaned


def _normalize_id(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", value.upper())
    return cleaned or None


def _name_tokens(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return re.sub(r"[^A-Z0-9]+", " ", value.upper()).split()


def _company_tokens(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    tokens = _name_tokens(value.upper().replace("&", " AND "))
    while tokens and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return tokens


def company_names_match(a: object, b: object) -> bool:
    """
    Fuzzy, but deliberately not typo-tolerant: "ACME Trading Pte Ltd" matches
    "ACME Pte Ltd" (one name is the other plus extra words, same leading
    word), but "ACNE Pte Ltd" does not — a one-letter difference in a
    company name is exactly what a document from a different company looks
    like, so edit-distance matching would defeat the point of this check.
    """
    tokens_a, tokens_b = _company_tokens(a), _company_tokens(b)
    if not tokens_a or not tokens_b:
        return False
    if tokens_a == tokens_b or "".join(tokens_a) == "".join(tokens_b):
        return True
    shorter, longer = sorted((tokens_a, tokens_b), key=len)
    return shorter[0] == longer[0] and set(shorter) <= set(longer)


def individual_names_match(a: object, b: object) -> bool:
    """Same words in any order — "Tan Wei Ming" matches "Wei Ming Tan"."""
    tokens_a, tokens_b = _name_tokens(a), _name_tokens(b)
    return bool(tokens_a) and sorted(tokens_a) == sorted(tokens_b)


def _identity_name_similarity(a: object, b: object) -> float:
    """Similarity used only to link a name-only document to an identified person.

    Exact matching remains the decision rule. A near name can link two records
    for comparison, but any difference is then sent to manual review.
    """
    tokens_a, tokens_b = _name_tokens(a), _name_tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    return SequenceMatcher(None, " ".join(sorted(tokens_a)), " ".join(sorted(tokens_b))).ratio()


# --- field extraction ---------------------------------------------------------


class _Person(NamedTuple):
    name: Optional[str]
    id_number: Optional[str]
    name_field: str
    id_field: str


def _document_uen(fields: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """(field name, raw value, normalised UEN) of the first UEN-bearing field."""
    for field in _UEN_FIELDS:
        for raw in _strings(fields.get(field)):
            normalized = normalize_uen(raw)
            if normalized:
                return field, raw, normalized
    return None, None, None


def _role_prefixed_people(fields: dict) -> list[_Person]:
    """
    People stored as flat, role-prefixed keys — how the certificate of
    incumbency actually comes back from extraction: director_name +
    director_id_number, secretary_name + secretary_id_number,
    shareholder_1_name + shareholder_1_id_number, shareholder_2_..., etc.
    """
    people: list[_Person] = []
    for key, value in fields.items():
        found = _ROLE_NAME_KEY.match(key)
        if not found or not isinstance(value, str) or not value.strip():
            continue
        prefix = found.group("prefix")
        id_field = next(
            (f"{prefix}{suffix}" for suffix in _ROLE_ID_SUFFIXES if fields.get(f"{prefix}{suffix}")),
            f"{prefix}_id_number",
        )
        raw_id = fields.get(id_field)
        people.append(
            _Person(value.strip(), raw_id if isinstance(raw_id, str) else None, key, id_field)
        )
    return people


def _list_field_people(fields: dict) -> list[_Person]:
    """People stored as an array of {name, id_number} objects."""
    people: list[_Person] = []
    for list_field in _PEOPLE_LIST_FIELDS:
        entries = fields.get(list_field)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name") if isinstance(entry.get("name"), str) else None
            id_key = next((k for k in _LIST_ENTRY_ID_KEYS if isinstance(entry.get(k), str) and entry[k]), None)
            id_number = entry[id_key] if id_key else None
            if name or id_number:
                people.append(
                    _Person(name, id_number, f"{list_field}.name", f"{list_field}.{id_key or 'id_number'}")
                )
    return people


def _individual_field_people(fields: dict) -> list[_Person]:
    """
    People in the generic individual_name/id_number fields most document
    types use. A name list and an ID list of the same length are paired up
    by position; otherwise each is recorded on its own.
    """
    for name_field in _INDIVIDUAL_NAME_FIELDS:
        names = _strings(fields.get(name_field))
        if not names:
            continue
        id_field, ids = next(
            ((f, _strings(fields.get(f))) for f in _INDIVIDUAL_ID_FIELDS if fields.get(f)),
            (_INDIVIDUAL_ID_FIELDS[0], []),
        )
        if len(ids) == len(names):
            return [_Person(n, i, name_field, id_field) for n, i in zip(names, ids)]
        return [
            *(_Person(n, None, name_field, id_field) for n in names),
            *(_Person(None, i, name_field, id_field) for i in ids),
        ]

    # IDs with no name at all, e.g. the back of an NRIC.
    for id_field in _INDIVIDUAL_ID_FIELDS:
        ids = _strings(fields.get(id_field))
        if ids:
            return [_Person(None, i, _INDIVIDUAL_NAME_FIELDS[0], id_field) for i in ids]
    return []


def _people(fields: dict) -> list[_Person]:
    """
    Every distinct individual a document names, from whichever shape the
    extraction used. The same person often appears several times — on the
    incumbency sample, the director is also shareholder_1 and the
    individual_name — so entries are de-duplicated by ID number, or by name
    for an entry with no ID.
    """
    people: list[_Person] = []
    seen_ids: set[str] = set()
    seen_names: set[tuple[str, ...]] = set()
    for person in (
        # Lists first: they're the complete set (every officer/shareholder,
        # with IDs), so a flat duplicate without an ID is recognised and dropped.
        *_list_field_people(fields),
        *_role_prefixed_people(fields),
        *_individual_field_people(fields),
    ):
        id_number = _normalize_id(person.id_number)
        name = tuple(sorted(_name_tokens(person.name)))
        if id_number in seen_ids or (id_number is None and name and name in seen_names):
            continue
        if id_number:
            seen_ids.add(id_number)
        if name:
            seen_names.add(name)
        people.append(person)
    return people


def _is_personal_document(doc: DocumentRecord, fields: dict) -> bool:
    if doc.document_type in _PERSONAL_DOCUMENT_TYPES:
        return True
    if is_personal_bank_statement(doc.document_type, fields):
        return True
    # Same rule as models.compute_fulfilled_requirements: a source_of_wealth
    # in a person's name with no UEN is a personal brokerage statement, whose
    # company_name is the broker's, not the applicant's.
    return (
        doc.document_type == DocumentType.SOURCE_OF_WEALTH.value
        and bool(fields.get("account_holder_name") or fields.get("individual_name"))
        and not fields.get("uen")
    )


# --- step 1: verify the incumbency --------------------------------------------


def _pick_incumbency(documents: list[DocumentRecord]) -> Optional[DocumentRecord]:
    candidates = [
        doc for doc in documents
        if doc.document_type == DocumentType.CERTIFICATE_OF_INCUMBENCY.value
        and doc.extracted_fields
    ]
    return max(candidates, key=lambda d: d.uploaded_at) if candidates else None


def _company_key(comparison: FieldComparison) -> str:
    """Groups conflicting documents that name the same other company."""
    if comparison.check == "company_uen":
        return f"UEN {normalize_uen(comparison.document_value)}"
    return f'company "{" ".join(_company_tokens(comparison.document_value))}"'


def _verify_incumbency(
    case: CaseRecord, documents: list[DocumentRecord]
) -> tuple[IncumbencyVerification, Optional[DocumentRecord]]:
    incumbency = _pick_incumbency(documents)
    if incumbency is None:
        uploaded = any(
            d.document_type == DocumentType.CERTIFICATE_OF_INCUMBENCY.value for d in documents
        )
        reason = (
            "Certificate of incumbency has not finished extraction yet"
            if uploaded
            else "No certificate of incumbency has been uploaded — it is required for every case"
        )
        return IncumbencyVerification(status=IncumbencyStatus.MISSING, reasons=[reason]), None

    fields = incumbency.extracted_fields or {}
    _, _, uen = _document_uen(fields)
    company_name = next(iter(_strings(fields.get("company_name"))), None)
    verification = IncumbencyVerification(
        status=IncumbencyStatus.INSUFFICIENT_EVIDENCE,
        document_id=incumbency.id,
        uen=uen,
        company_name=company_name,
        individuals=[IncumbencyPerson(name=p.name, id_number=p.id_number) for p in _people(fields)],
    )

    review_reasons: list[str] = []
    if uen is None:
        review_reasons.append("No UEN could be extracted from the certificate of incumbency")

    # Another incumbency in the same case naming a different company.
    for other in documents:
        if (
            other.id != incumbency.id
            and other.document_type == DocumentType.CERTIFICATE_OF_INCUMBENCY.value
            and other.extracted_fields
        ):
            _, _, other_uen = _document_uen(other.extracted_fields)
            if uen and other_uen and other_uen != uen:
                review_reasons.append(
                    f"Another certificate of incumbency ({other.filename}) shows UEN "
                    f"{other_uen}, but {incumbency.filename} shows {uen}"
                )

    # Deliberately not compared against case.company_name: it's a free-text
    # label an officer types at case creation (often a placeholder like
    # "Case 67"), not verified company data. The certificate is judged only
    # by whether the case's other documents agree with its UEN/company name.

    if uen is not None:
        # Same comparison as step 2 (UEN when the document has one, otherwise
        # fuzzy company name), so a corporate bank statement — no UEN, but
        # the company's name — corroborates too. Personal documents have no
        # company to compare and don't count either way.
        conflicting_companies: Counter[str] = Counter()
        for doc in documents:
            if doc.document_type == DocumentType.CERTIFICATE_OF_INCUMBENCY.value:
                continue
            comparison = _compare_company(doc, doc.extracted_fields or {}, verification)
            if comparison is None:
                continue
            if comparison.outcome == CheckOutcome.MATCH:
                verification.corroborating_document_ids.append(doc.id)
            else:
                verification.conflicting_document_ids.append(doc.id)
                conflicting_companies[_company_key(comparison)] += 1

        matches = len(verification.corroborating_document_ids)
        if matches < MIN_CORROBORATING_DOCUMENTS and conflicting_companies:
            consensus, consensus_count = conflicting_companies.most_common(1)[0]
            # The rest of the case agrees on a different company than the
            # incumbency does — distrust the incumbency, not the rest.
            if consensus_count >= 2 and consensus_count > matches:
                review_reasons.append(
                    f"{consensus_count} other documents agree on {consensus}, but the "
                    f"certificate of incumbency shows {company_name or 'company'} ({uen}) "
                    f"(only {matches} document(s) match it)"
                )

    if review_reasons:
        verification.status = IncumbencyStatus.MANUAL_REVIEW
        verification.reasons = review_reasons
    elif len(verification.corroborating_document_ids) >= MIN_CORROBORATING_DOCUMENTS:
        verification.status = IncumbencyStatus.VERIFIED
        verification.reasons = [
            f"{len(verification.corroborating_document_ids)} other documents match the "
            f"certificate of incumbency's company ({uen})"
        ]
    else:
        verification.reasons = [
            f"Only {len(verification.corroborating_document_ids)} other document(s) match the "
            f"certificate of incumbency's company ({uen}) — at least {MIN_CORROBORATING_DOCUMENTS} are needed to verify the "
            "certificate of incumbency"
        ]
    return verification, incumbency


# --- steps 2 & 3: compare each document ---------------------------------------


def _compare_company(
    doc: DocumentRecord, fields: dict, reference: IncumbencyVerification
) -> Optional[FieldComparison]:
    if _is_personal_document(doc, fields):
        return None

    uen_field, raw_uen, uen = _document_uen(fields)
    if uen is not None and reference.uen is not None:
        # _document_uen returns all three of these together or none at all.
        assert uen_field is not None and raw_uen is not None
        matched = uen == reference.uen
        return FieldComparison(
            check="company_uen",
            outcome=CheckOutcome.MATCH if matched else CheckOutcome.MISMATCH,
            document_field=uen_field,
            document_value=raw_uen,
            case_value=reference.uen,
            reason=(
                "UEN matches the certificate of incumbency"
                if matched
                else f"UEN {uen} does not match the certificate of incumbency's UEN {reference.uen}"
            ),
        )

    company_name = next(iter(_strings(fields.get("company_name"))), None)
    if company_name is None or reference.company_name is None:
        return None
    matched = company_names_match(company_name, reference.company_name)
    return FieldComparison(
        check="company_name",
        outcome=CheckOutcome.MATCH if matched else CheckOutcome.MISMATCH,
        document_field="company_name",
        document_value=company_name,
        case_value=reference.company_name,
        reason=(
            "Company name matches the certificate of incumbency"
            if matched
            else f'Company "{company_name}" does not match the certificate of incumbency\'s '
            f'"{reference.company_name}"'
        ),
    )


def _compare_person(
    person: _Person, reference: IncumbencyVerification
) -> Optional[list[FieldComparison]]:
    reference_ids = {_normalize_id(p.id_number) for p in reference.individuals} - {None}
    id_number = _normalize_id(person.id_number)
    if id_number and reference_ids:
        # _normalize_id only returns truthy when given a real string, so
        # person.id_number (the raw value it was normalised from) is too.
        assert person.id_number is not None
        matched = id_number in reference_ids
        who = f" ({person.name})" if person.name else ""
        comparisons = [FieldComparison(
            check="individual_id",
            outcome=CheckOutcome.MATCH if matched else CheckOutcome.MISMATCH,
            document_field=person.id_field,
            document_value=person.id_number,
            case_value=", ".join(
                p.id_number for p in reference.individuals if p.id_number
            ),
            reason=(
                f"ID {person.id_number}{who} is listed on the certificate of incumbency"
                if matched
                else f"ID {person.id_number}{who} is not any officer or shareholder on the "
                "certificate of incumbency"
            ),
        )]
        # An ID match identifies the corresponding officer. Check that
        # document's name too; otherwise a correct ID could hide a different
        # name printed on another document.
        if matched and person.name:
            matching_people = [
                p for p in reference.individuals
                if _normalize_id(p.id_number) == id_number and p.name
            ]
            if matching_people and not any(
                individual_names_match(person.name, p.name) for p in matching_people
            ):
                comparisons.append(FieldComparison(
                    check="individual_name",
                    outcome=CheckOutcome.MISMATCH,
                    document_field=person.name_field,
                    document_value=person.name,
                    case_value=", ".join(p.name for p in matching_people if p.name),
                    reason=f"Name {person.name} does not match the name listed for ID {person.id_number} on the certificate of incumbency",
                ))
        return comparisons

    reference_names = [p.name for p in reference.individuals if p.name]
    if person.name and reference_names:
        matched = any(individual_names_match(person.name, ref) for ref in reference_names)
        return [FieldComparison(
            check="individual_name",
            outcome=CheckOutcome.MATCH if matched else CheckOutcome.MISMATCH,
            document_field=person.name_field,
            document_value=person.name,
            case_value=", ".join(reference_names),
            reason=(
                f"{person.name} is listed on the certificate of incumbency"
                if matched
                else f"{person.name} is not any officer or shareholder on the certificate of "
                "incumbency"
            ),
        )]
    return None


def _match_document(
    doc: DocumentRecord,
    incumbency_doc: Optional[DocumentRecord],
    reference: IncumbencyVerification,
) -> DocumentCaseMatch:
    def result(
        status: DocumentMatchStatus, reason: str, comparisons=(), case_mismatch: bool = False
    ) -> DocumentCaseMatch:
        return DocumentCaseMatch(
            document_id=doc.id,
            filename=doc.filename,
            document_type=doc.document_type,
            status=status,
            can_proceed_to_fraud_detection=status in {
                DocumentMatchStatus.MATCH,
                DocumentMatchStatus.MISMATCH,
            },
            case_mismatch=case_mismatch,
            reason=reason,
            comparisons=list(comparisons),
        )

    if incumbency_doc is not None and doc.id == incumbency_doc.id:
        if reference.status == IncumbencyStatus.VERIFIED:
            return result(DocumentMatchStatus.MATCH, "Verified as the case's certificate of incumbency")
        if reference.status == IncumbencyStatus.MANUAL_REVIEW:
            return result(DocumentMatchStatus.MANUAL_REVIEW, "; ".join(reference.reasons))
        return result(DocumentMatchStatus.PENDING, "; ".join(reference.reasons))

    if not doc.extracted_fields:
        return result(DocumentMatchStatus.PENDING, "Fields have not been extracted yet")

    if reference.status == IncumbencyStatus.MISSING:
        return result(DocumentMatchStatus.PENDING, "; ".join(reference.reasons))

    fields = doc.extracted_fields
    comparisons: list[FieldComparison] = []
    company = _compare_company(doc, fields, reference)
    if company is not None:
        comparisons.append(company)
    if doc.document_type not in _COMPANY_ONLY_DOCUMENT_TYPES:
        comparisons.extend(
            c
            for p in _people(fields)
            for c in (_compare_person(p, reference) or [])
        )

    if incumbency_doc is not None:
        comparisons = [
            comparison.model_copy(update={
                "reference_document_id": incumbency_doc.id,
                "reference_filename": incumbency_doc.filename,
            })
            for comparison in comparisons
        ]

    # Comparisons are still returned while the incumbency is unverified, so
    # an officer can see what they'd show — they just aren't acted on yet.
    if reference.status == IncumbencyStatus.MANUAL_REVIEW:
        return result(
            DocumentMatchStatus.PENDING,
            "Waiting for the certificate of incumbency to be manually reviewed",
            comparisons,
        )
    if reference.status == IncumbencyStatus.INSUFFICIENT_EVIDENCE:
        return result(
            DocumentMatchStatus.PENDING,
            "Waiting for the certificate of incumbency to be verified",
            comparisons,
        )

    if not comparisons:
        return result(
            DocumentMatchStatus.MANUAL_REVIEW,
            "No company or individual details were extracted to compare against the case",
        )
    mismatches = [c for c in comparisons if c.outcome == CheckOutcome.MISMATCH]
    company_mismatches = [
        comparison for comparison in mismatches
        if comparison.check in {"company_uen", "company_name"}
    ]
    if company_mismatches:
        return result(
            DocumentMatchStatus.MANUAL_REVIEW,
            "; ".join(comparison.reason for comparison in company_mismatches),
            comparisons,
            case_mismatch=True,
        )
    if mismatches:
        return result(
            DocumentMatchStatus.MISMATCH,
            "; ".join(c.reason for c in mismatches),
            comparisons,
            case_mismatch=True,
        )
    return result(DocumentMatchStatus.MATCH, "Matches the case's company and individuals", comparisons)


def _person_link_score(a: _Person, b: _Person) -> float:
    """Whether two extracted person records could refer to the same person.

    Equal IDs are strongest. Exact names also link records, including when
    their IDs conflict so the ID difference can be reported. When one side has
    no ID, a close name can link the records for review; it never makes the
    values a match.
    """
    id_a, id_b = _normalize_id(a.id_number), _normalize_id(b.id_number)
    if id_a and id_b:
        if id_a == id_b:
            return 1.0
        return 1.0 if individual_names_match(a.name, b.name) else 0.0

    if individual_names_match(a.name, b.name):
        return 1.0
    return _identity_name_similarity(a.name, b.name)


def _unique_person_pairs(
    left: list[_Person], right: list[_Person], *, min_score: float = 0.86
) -> list[tuple[_Person, _Person]]:
    """Link only reciprocal, unambiguous best candidates across two documents."""
    scores = [[_person_link_score(a, b) for b in right] for a in left]
    left_best: dict[int, int] = {}
    right_best: dict[int, int] = {}

    for i, row in enumerate(scores):
        best = max(row, default=0.0)
        if best < min_score or row.count(best) != 1:
            continue
        left_best[i] = row.index(best)

    for j in range(len(right)):
        column = [scores[i][j] for i in range(len(left))]
        best = max(column, default=0.0)
        if best < min_score or column.count(best) != 1:
            continue
        right_best[j] = column.index(best)

    return [
        (left[i], right[j])
        for i, j in left_best.items()
        if right_best.get(j) == i
    ]


def _pairwise_person_findings(
    left_doc: DocumentRecord,
    left_person: _Person,
    right_doc: DocumentRecord,
    right_person: _Person,
) -> list[tuple[str, FieldComparison, str]]:
    """Return reciprocal comparisons for differences in a linked person pair."""
    findings: list[tuple[str, FieldComparison, str]] = []
    left_id, right_id = _normalize_id(left_person.id_number), _normalize_id(right_person.id_number)
    if left_id and right_id and left_id != right_id:
        for doc, person, other_doc, other in (
            (left_doc, left_person, right_doc, right_person),
            (right_doc, right_person, left_doc, left_person),
        ):
            value, other_value = person.id_number or "", other.id_number or ""
            comparison = FieldComparison(
                check="individual_id",
                outcome=CheckOutcome.MISMATCH,
                document_field=person.id_field,
                document_value=value,
                other_document_id=other_doc.id,
                other_filename=other_doc.filename,
                case_value=other_value,
                reason=(
                    f"ID {value} in {doc.filename} differs from ID {other_value} "
                    f"in {other_doc.filename}"
                ),
            )
            findings.append((doc.id, comparison, comparison.reason))

    if (
        left_person.name
        and right_person.name
        and not individual_names_match(left_person.name, right_person.name)
    ):
        for doc, person, other_doc, other in (
            (left_doc, left_person, right_doc, right_person),
            (right_doc, right_person, left_doc, left_person),
        ):
            comparison = FieldComparison(
                check="individual_name",
                outcome=CheckOutcome.MISMATCH,
                document_field=person.name_field,
                document_value=person.name,
                other_document_id=other_doc.id,
                other_filename=other_doc.filename,
                case_value=other.name,
                reason=(
                    f'Name "{person.name}" in {doc.filename} differs from '
                    f'"{other.name}" in {other_doc.filename}'
                ),
            )
            findings.append((doc.id, comparison, comparison.reason))
    return findings


def _apply_pairwise_person_checks(
    matches: list[DocumentCaseMatch],
    documents: list[DocumentRecord],
    incumbency_doc: Optional[DocumentRecord],
) -> list[DocumentCaseMatch]:
    """Add cross-document identity mismatches to both documents in each pair."""
    match_by_id = {match.document_id: match for match in matches}
    docs_with_people = [
        doc for doc in documents
        if doc.extracted_fields
        and doc.id != (incumbency_doc.id if incumbency_doc else None)
        and doc.document_type not in _COMPANY_ONLY_DOCUMENT_TYPES
    ]
    findings_by_id: dict[str, list[tuple[FieldComparison, str]]] = {}

    for index, left_doc in enumerate(docs_with_people):
        for right_doc in docs_with_people[index + 1:]:
            for left_person, right_person in _unique_person_pairs(
                _people(left_doc.extracted_fields or {}),
                _people(right_doc.extracted_fields or {}),
            ):
                for doc_id, comparison, reason in _pairwise_person_findings(
                    left_doc, left_person, right_doc, right_person
                ):
                    findings_by_id.setdefault(doc_id, []).append((comparison, reason))

    updated: list[DocumentCaseMatch] = []
    for match in matches:
        additions = findings_by_id.get(match.document_id, [])
        if not additions:
            updated.append(match)
            continue
        reasons = [reason for _, reason in additions]
        company_mismatch = any(
            comparison.outcome == CheckOutcome.MISMATCH
            and comparison.check in {"company_uen", "company_name"}
            for comparison in match.comparisons
        )
        prior_reason = (
            match.reason
            if match.status in {DocumentMatchStatus.MISMATCH, DocumentMatchStatus.MANUAL_REVIEW}
            else ""
        )
        updated.append(match.model_copy(update={
            "status": (
                DocumentMatchStatus.MANUAL_REVIEW
                if company_mismatch else DocumentMatchStatus.MISMATCH
            ),
            "can_proceed_to_fraud_detection": not company_mismatch,
            "case_mismatch": True,
            "reason": "; ".join(([prior_reason] if prior_reason else []) + reasons),
            "comparisons": [*match.comparisons, *(comparison for comparison, _ in additions)],
        }))
    return updated


def _rejected(doc: DocumentRecord) -> DocumentCaseMatch:
    return DocumentCaseMatch(
        document_id=doc.id,
        filename=doc.filename,
        document_type=doc.document_type,
        status=DocumentMatchStatus.PENDING,
        can_proceed_to_fraud_detection=False,
        reason="Rejected by a loan officer — excluded from the case match",
    )


def check_case_match(case: CaseRecord, documents: Iterable[DocumentRecord]) -> CaseMatchResult:
    documents = list(documents)
    # A document a loan officer rejected is out of the case: it can't be the
    # reference incumbency, and it can't corroborate or contradict one.
    active = [doc for doc in documents if doc.status != DocumentStatus.REJECTED]
    verification, incumbency_doc = _verify_incumbency(case, active)
    matches = [
        _match_document(doc, incumbency_doc, verification)
        if doc.status != DocumentStatus.REJECTED
        else _rejected(doc)
        for doc in documents
    ]
    # Cross-document person comparisons do not depend on trusting the
    # certificate of incumbency. They compare the identity details extracted
    # directly from each pair of documents (for example, an NRIC and a
    # personal bank statement), so run them even when the case has no
    # incumbency document or it has not yet been corroborated.
    matches = _apply_pairwise_person_checks(matches, active, incumbency_doc)
    return CaseMatchResult(
        case_id=case.id,
        incumbency=verification,
        documents=matches,
        mismatched_document_ids=[
            m.document_id for m in matches if m.case_mismatch
        ],
    )
