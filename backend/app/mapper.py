"""
Cross-document identity matching for proof_of_address — a distinct concern
from case_status.py's other job (documents_status rollup, submission_finalized
sync): this is specifically about joining documents by extracted
individual_name to figure out who still needs an address on file.
"""
from __future__ import annotations

from typing import NamedTuple, Optional

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


def _normalize_id_number(id_number: object) -> Optional[str]:
    if not isinstance(id_number, str) or not id_number:
        return None
    return "".join(id_number.strip().upper().split())


class _IdentityState(NamedTuple):
    # identity key (id_number, or name when no id_number was extracted) ->
    # (normalized names seen under this identity, has its own address)
    identities: dict[str, tuple[set[str], bool]]
    # accepted documents (any type) carrying a non-empty individual_address
    address_docs: list[DocumentRecord]
    # normalized names drawn from address_docs
    names_with_address: set[str]


def _collect_identity_state(documents: list[DocumentRecord]) -> _IdentityState:
    accepted = [
        doc for doc in documents if doc.classification_outcome == ClassificationOutcome.ACCEPTED
    ]

    identities: dict[str, tuple[set[str], bool]] = {}
    for doc in accepted:
        if doc.document_type != DocumentType.ID_DOCUMENT.value:
            continue
        fields = doc.extracted_fields or {}
        name = _normalize_name(fields.get("individual_name"))
        id_number = _normalize_id_number(fields.get("id_number"))
        key = id_number or name
        if key is None:
            continue
        names, has_address = identities.get(key, (set(), False))
        if name:
            names.add(name)
        if fields.get("individual_address"):
            has_address = True
        identities[key] = (names, has_address)

    address_docs = [doc for doc in accepted if (doc.extracted_fields or {}).get("individual_address")]
    names_with_address = {
        normalized
        for doc in address_docs
        if (normalized := _normalize_name((doc.extracted_fields or {}).get("individual_name"))) is not None
    }

    return _IdentityState(identities, address_docs, names_with_address)


def compute_identity_proof_of_address_progress(documents: list[DocumentRecord]) -> tuple[int, int]:
    """
    proof_of_address is the one requirement that isn't case-wide from a
    single document: uploading multiple ID documents means multiple
    identities are in play, and each one needs its own address on file —
    not just one address anywhere in the case. An identity is "covered"
    either by its own ID document carrying an address, or by a separate
    document (e.g. a utility bill) whose extracted individual_name matches.

    Identities are grouped by normalized id_number where an id_document
    provides one, rather than by name alone. This matters because a single
    NRIC is often uploaded as two separate images (front/back) that get
    extracted as two separate id_document rows — OCR on the back image can
    garble or drop the name entirely, so name-matching alone would treat
    them as two different people instead of recognising they share the same
    id_number. Documents without an extracted id_number still fall back to
    matching by normalized name, same as before.

    Coverage against proof_of_address is still joined by name, since a
    proof_of_address document has no id_number field to join on at all
    (see extraction_prompts.EXTRACTION_SCHEMAS) — an identity counts as
    covered if any name seen for it (across its id_number-linked documents)
    matches a document carrying an address. Known limitation: this name
    match is global, not scoped per identity, so two different people who
    happen to share a name but have distinct id_numbers can both read as
    covered once either one has an address on file — pre-existing behaviour
    of name-based matching, not something id_number grouping fixes.

    Returns (covered, total) identities — total is how many accepted
    id_documents have been identified in the case at all, so (0, 0) means
    there's nothing to report coverage against yet, distinct from (0, 2).
    An address document (proof_of_address, bank_statement, ...) whose name
    doesn't match any identified id_document isn't counted here at all — see
    compute_unmatched_proof_of_address_names for surfacing those.
    """
    identities, _, names_with_address = _collect_identity_state(documents)

    covered = sum(
        1
        for names, has_address in identities.values()
        if has_address or (names & names_with_address)
    )
    return covered, len(identities)


def compute_identity_proof_of_address_coverage(documents: list[DocumentRecord]) -> bool:
    """
    Returns False when there are no accepted ID documents yet —
    proof_of_address only makes sense once at least one identity has
    actually been identified.
    """
    covered, total = compute_identity_proof_of_address_progress(documents)
    return total > 0 and covered == total


def compute_unmatched_proof_of_address_names(documents: list[DocumentRecord]) -> list[str]:
    """
    Names carrying an address (from an accepted proof_of_address,
    bank_statement, or any other document type that happens to extract one)
    that don't match any identified id_document identity in the case — e.g.
    a proof_of_address uploaded for someone whose ID hasn't been uploaded
    yet, or for a different person entirely. These addresses sit unused:
    they don't count toward proof_of_address_coverage's total (which is
    id_document-driven) and don't cover anyone, so without surfacing them
    separately a reviewer has no way to see that an uploaded address is just
    sitting there unmatched rather than confirming it silently did nothing.

    Returned as display-cased names (as extracted, not the uppercased join
    key), de-duplicated by normalized name, in first-seen order.
    """
    identities, address_docs, _ = _collect_identity_state(documents)

    matched_names: set[str] = set()
    for names, _ in identities.values():
        matched_names |= names

    unmatched: dict[str, str] = {}
    for doc in address_docs:
        raw_name = (doc.extracted_fields or {}).get("individual_name")
        normalized = _normalize_name(raw_name)
        if normalized is None or normalized in matched_names or not isinstance(raw_name, str):
            continue
        unmatched.setdefault(normalized, raw_name.strip())
    return list(unmatched.values())
