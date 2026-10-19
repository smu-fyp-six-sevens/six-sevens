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

from collections import Counter
from enum import Enum
from typing import Iterable

from pydantic import BaseModel, Field

from .identifiers import (
    COMPANY_ONLY_DOCUMENT_TYPES,
    Person,
    company_names_match,
    company_tokens,
    document_people,
    id_key,
    individual_names_match,
    text_values,
    uen_key,
)
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


# Documents about a person rather than the company. Their company_name (if
# any) isn't the applicant company — a payslip's is the employer, which may
# well be somewhere else — so only the individual check applies to them.
_PERSONAL_DOCUMENT_TYPES: set[str] = {
    DocumentType.PAYSLIP.value,
    DocumentType.NRIC.value,
    DocumentType.PASSPORT.value,
    DocumentType.CREDIT_REPORT.value,
}



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
    reference_document_id: str | None = None
    reference_filename: str | None = None
    other_document_id: str | None = None
    other_filename: str | None = None
    # What the incumbency holds for the same thing — for an individual,
    # every officer/shareholder it was compared against.
    case_value: str | None = None
    reason: str


class IncumbencyPerson(BaseModel):
    name: str | None = None
    id_number: str | None = None


class IncumbencyVerification(BaseModel):
    status: IncumbencyStatus
    document_id: str | None = None
    uen: str | None = None
    company_name: str | None = None
    individuals: list[IncumbencyPerson] = Field(default_factory=list)
    corroborating_document_ids: list[str] = Field(default_factory=list)
    conflicting_document_ids: list[str] = Field(default_factory=list)
    required_corroborations: int = MIN_CORROBORATING_DOCUMENTS
    reasons: list[str] = Field(default_factory=list)


class DocumentCaseMatch(BaseModel):
    document_id: str
    filename: str
    document_type: str | None = None
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



# --- field extraction ---------------------------------------------------------



def _document_uen(fields: dict) -> tuple[str | None, str | None, str | None]:
    """(field name, raw value, normalised UEN) of the first UEN-bearing field."""
    for field in _UEN_FIELDS:
        for raw in text_values(fields.get(field)):
            normalized = uen_key(raw)
            if normalized:
                return field, raw, normalized
    return None, None, None




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


def _pick_incumbency(documents: list[DocumentRecord]) -> DocumentRecord | None:
    candidates = [
        doc for doc in documents
        if doc.document_type == DocumentType.CERTIFICATE_OF_INCUMBENCY.value
        and doc.extracted_fields
    ]
    return max(candidates, key=lambda d: d.uploaded_at) if candidates else None


def _company_key(comparison: FieldComparison) -> str:
    """Groups conflicting documents that name the same other company."""
    if comparison.check == "company_uen":
        return f"UEN {uen_key(comparison.document_value)}"
    return f'company "{" ".join(company_tokens(comparison.document_value))}"'


def _verify_incumbency(
    case: CaseRecord, documents: list[DocumentRecord]
) -> tuple[IncumbencyVerification, DocumentRecord | None]:
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
    company_name = next(iter(text_values(fields.get("company_name"))), None)
    verification = IncumbencyVerification(
        status=IncumbencyStatus.INSUFFICIENT_EVIDENCE,
        document_id=incumbency.id,
        uen=uen,
        company_name=company_name,
        individuals=[IncumbencyPerson(name=p.name, id_number=p.id_number) for p in document_people(fields)],
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
) -> FieldComparison | None:
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

    company_name = next(iter(text_values(fields.get("company_name"))), None)
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
    person: Person, reference: IncumbencyVerification
) -> list[FieldComparison] | None:
    reference_ids = {id_key(p.id_number) for p in reference.individuals} - {None}
    id_number = id_key(person.id_number)
    if id_number and reference_ids:
        # id_key only returns truthy when given a real string, so
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
                if id_key(p.id_number) == id_number and p.name
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
    incumbency_doc: DocumentRecord | None,
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
    if doc.document_type not in COMPANY_ONLY_DOCUMENT_TYPES:
        comparisons.extend(
            c
            for p in document_people(fields)
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
    # The company or a person isn't the case's: the document looks misfiled, so an officer reviews it.
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
            DocumentMatchStatus.MANUAL_REVIEW,
            "; ".join(c.reason for c in mismatches),
            comparisons,
            case_mismatch=True,
        )
    return result(DocumentMatchStatus.MATCH, "Matches the case's company and individuals", comparisons)




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
    return CaseMatchResult(
        case_id=case.id,
        incumbency=verification,
        documents=matches,
        mismatched_document_ids=[
            m.document_id for m in matches if m.case_mismatch
        ],
    )
