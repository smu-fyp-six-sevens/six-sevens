"""
Cross-document identity matching for proof_of_address — a distinct concern
from case_status.py's other job (documents_status rollup, submission_finalized
sync): this is specifically about joining documents by extracted
individual_name to figure out who still needs an address on file.
"""
from __future__ import annotations

from typing import Optional

from .models import ClassificationOutcome, DocumentRecord, DocumentType


def _normalize_name(name: object) -> Optional[str]:
    # extracted_fields is LLM output, not a validated schema — individual_name
    # usually comes back as a single string, but a document naming several
    # people (e.g. a company constitution listing multiple directors) can
    # legitimately come back as a list instead. Not a string means it can't
    # be joined against anything here, so treat it as no usable name rather
    # than crashing the whole case list over one document's shape.
    if not isinstance(name, str) or not name:
        return None
    return " ".join(name.strip().upper().split())


def compute_identity_proof_of_address_progress(documents: list[DocumentRecord]) -> tuple[int, int]:
    """
    proof_of_address is the one requirement that isn't case-wide from a
    single document: uploading multiple ID documents means multiple
    identities are in play, and each one needs its own address on file —
    not just one address anywhere in the case. An identity is "covered"
    either by its own ID document carrying an address, or by a separate
    document (e.g. a utility bill) whose extracted individual_name matches.

    Matching is by normalized individual_name rather than id_number, since
    only id_document/credit_report extract an id_number at all — a
    proof_of_address document has no such field to join on.

    Returns (covered, total) identities — total is how many accepted
    id_documents have been identified in the case at all, so (0, 0) means
    there's nothing to report coverage against yet, distinct from (0, 2).
    """
    accepted = [
        doc for doc in documents if doc.classification_outcome == ClassificationOutcome.ACCEPTED
    ]

    identities = {
        _normalize_name((doc.extracted_fields or {}).get("individual_name"))
        for doc in accepted
        if doc.document_type == DocumentType.ID_DOCUMENT.value
    }
    identities.discard(None)

    names_with_address = {
        _normalize_name((doc.extracted_fields or {}).get("individual_name"))
        for doc in accepted
        if (doc.extracted_fields or {}).get("individual_address")
    }
    names_with_address.discard(None)

    covered = sum(1 for name in identities if name in names_with_address)
    return covered, len(identities)


def compute_identity_proof_of_address_coverage(documents: list[DocumentRecord]) -> bool:
    """
    Returns False when there are no accepted ID documents yet —
    proof_of_address only makes sense once at least one identity has
    actually been identified.
    """
    covered, total = compute_identity_proof_of_address_progress(documents)
    return total > 0 and covered == total
