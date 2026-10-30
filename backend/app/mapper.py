"""
The case's requirement checklist (compute_requirement_coverage) and the
cross-document identity matching it rests on for proof_of_address and
id_document: joining documents by extracted individual_name to figure out
who still needs an address or an ID on file. Kept apart from case_service.py
so the stages (case_stage.py) and the analysis can read the checklist too.
"""
from __future__ import annotations

from typing import NamedTuple

from .identifiers import id_key, name_key
from .models import (
    ADDITIONAL_DOCUMENT_TYPES,
    IDENTITY_DOCUMENT_REQUIREMENT,
    IDENTITY_DOCUMENT_TYPES,
    REQUIRED_DOCUMENT_TYPES,
    DocumentRecord,
    DocumentType,
    counts_as_address_proof,
    counts_towards_checklist,
)


class _Identity(NamedTuple):
    names: set[str]
    has_address: bool
    # document_side values ("front"/"back"/"single") seen across this
    # identity's own accepted id_document uploads — see
    # compute_id_document_identity_progress, which is the reason this is
    # tracked per identity rather than case-wide.
    sides: set[str]


class _NamedAddress(NamedTuple):
    raw_name: str
    normalized_name: str


class _IdentityState(NamedTuple):
    # identity key (nric_number, or name when no nric_number was extracted)
    # -> _Identity
    identities: dict[str, _Identity]
    # One entry per person an accepted NRIC / utility bill / bank statement
    # ties an address to — see _named_addresses_from_document.
    named_addresses: list[_NamedAddress]
    # normalized names drawn from named_addresses
    names_with_address: set[str]


def _named_addresses_from_document(doc: DocumentRecord) -> list[_NamedAddress]:
    """
    The (individual_name, individual_address) pair a document ties together,
    if it's a document type that counts as proof of address at all — the
    NRIC, or a personal utility bill / bank statement
    (models.counts_as_address_proof). An address on any other document
    (e.g. a constitution/incumbency listing people) is ignored.

    Deliberately does not create or feed into identities (see
    _collect_identity_state) — an address document only ever gets to match
    an identity an actual id_document established, never to establish one.
    """
    fields = doc.extracted_fields or {}
    if not counts_as_address_proof(doc.document_type, fields):
        return []

    raw_name = fields.get("individual_name")
    if not fields.get("individual_address") or not isinstance(raw_name, str):
        return []
    normalized = name_key(raw_name)
    if normalized is None:
        return []
    return [_NamedAddress(raw_name.strip(), normalized)]


def _collect_identity_state(documents: list[DocumentRecord]) -> _IdentityState:
    accepted = [doc for doc in documents if counts_towards_checklist(doc)]

    # Identities come ONLY from id_document uploads — an address document
    # can match an identity an id_document already established, but never
    # creates one on its own; see _named_addresses_from_document.
    identities: dict[str, _Identity] = {}
    for doc in accepted:
        if doc.document_type not in IDENTITY_DOCUMENT_TYPES:
            continue
        fields = doc.extracted_fields or {}
        name = name_key(fields.get("individual_name"))
        # nric_number, not passport_number: a person's NRIC number is the
        # one identifier that's actually shared between their NRIC and
        # their passport (see extraction_prompts.EXTRACTION_SCHEMAS —
        # "passport" extracts nric_number too, mirroring the "National ID
        # No" field a real Singapore passport page prints). passport_number
        # is the passport's own document serial, unrelated to the person's
        # NRIC and useless for cross-document identity linking.
        id_number = id_key(fields.get("nric_number"))
        key = id_number or name
        if key is None:
            continue
        names, has_address, sides = identities.get(key, (set(), False, set()))
        if name:
            names.add(name)
        if fields.get("individual_address") and counts_as_address_proof(doc.document_type, fields):
            has_address = True
        # A passport bio page is always self-contained — never split into a
        # separate front/back upload — nric is the
        # only type where front/back/single is genuinely extracted.
        if doc.document_type == "passport":
            sides.add("single")
        else:
            side = fields.get("document_side")
            if isinstance(side, str) and side.strip().lower() in ("front", "back", "single"):
                sides.add(side.strip().lower())
        identities[key] = _Identity(names, has_address, sides)

    named_addresses: list[_NamedAddress] = [
        entry for doc in accepted for entry in _named_addresses_from_document(doc)
    ]
    names_with_address = {entry.normalized_name for entry in named_addresses}

    return _IdentityState(identities, named_addresses, names_with_address)


def compute_identity_proof_of_address_progress(documents: list[DocumentRecord]) -> tuple[int, int]:
    """
    proof_of_address is the one requirement that isn't case-wide from a
    single document: uploading multiple ID documents means multiple
    identities are in play, and each one needs its own address on file —
    not just one address anywhere in the case. An identity is "covered"
    either by its own ID document carrying an address, or by a separate
    document (e.g. a utility bill) whose extracted individual_name matches.

    Identities are grouped by normalized nric_number where a document
    provides one, rather than by name alone. This matters because a single
    NRIC is often uploaded as two separate images (front/back) that get
    extracted as two separate identity-document rows — OCR on the back
    image can garble or drop the name entirely, so name-matching alone
    would treat them as two different people instead of recognising they
    share the same nric_number. It's also the field that links a passport
    to its owner's NRIC (see extraction_prompts.EXTRACTION_SCHEMAS's
    "passport" entry) — passport_number is the passport's own serial, not
    a personal identifier, so it's deliberately not used here. Documents
    without an extracted nric_number still fall back to matching by
    normalized name, same as before.

    Coverage against proof_of_address is still joined by name, since a
    proof_of_address document has no nric_number field to join on at all
    (see extraction_prompts.EXTRACTION_SCHEMAS) — an identity counts as
    covered if any name seen for it (across its nric_number-linked
    documents) matches a document carrying an address. Known limitation:
    this name match is global, not scoped per identity, so two different
    people who happen to share a name but have distinct nric_numbers can
    both read as covered once either one has an address on file —
    pre-existing behaviour of name-based matching, not something
    nric_number grouping fixes.

    Returns (covered, total) identities — total is how many accepted
    identity documents have been identified in the case at all, so (0, 0)
    means there's nothing to report coverage against yet, distinct from
    (0, 2). An address document (proof_of_address, bank_statement, ...)
    whose name doesn't match any identified identity document isn't
    counted here at all — see compute_unmatched_proof_of_address_names for
    surfacing those.
    """
    identities, _, names_with_address = _collect_identity_state(documents)

    covered = sum(
        1
        for names, has_address, _ in identities.values()
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
    Names carrying an address (from an accepted proof_of_address or
    bank_statement) that don't match any identified id_document identity in
    the case — e.g. a proof_of_address uploaded for someone whose ID hasn't been uploaded
    yet, or for a different person entirely. These addresses sit unused:
    they don't count toward proof_of_address_coverage's total (which is
    id_document-driven) and don't cover anyone, so without surfacing them
    separately a reviewer has no way to see that an uploaded address is just
    sitting there unmatched rather than confirming it silently did nothing.

    Returned as display-cased names (as extracted, not the uppercased join
    key), de-duplicated by normalized name, in first-seen order.
    """
    identities, named_addresses, _ = _collect_identity_state(documents)

    matched_names: set[str] = set()
    for names, _, _ in identities.values():
        matched_names |= names

    unmatched: dict[str, str] = {}
    for entry in named_addresses:
        if entry.normalized_name in matched_names:
            continue
        unmatched.setdefault(entry.normalized_name, entry.raw_name)
    return list(unmatched.values())


def _is_identity_id_document_complete(sides: set[str]) -> bool:
    """A "single" capture (passport, or a combined scan) needs no counterpart."""
    return "single" in sides or ("front" in sides and "back" in sides)


def compute_id_document_identity_progress(documents: list[DocumentRecord]) -> tuple[int, int]:
    """
    id_document is fulfilled only once EVERY identified individual's own ID
    document is complete (front+back matched by id_number, or a
    self-contained single capture) — mirrors
    compute_identity_proof_of_address_progress's same "every identity"
    requirement, for the same reason: a two-person case where person A's
    back and person B's front were both uploaded must not read as complete
    just because a front and a back both exist somewhere in the case — see
    extraction_prompts.EXTRA_FIELD_GUIDANCE's "nric" entry for how
    document_side ("front" / "back" / "single") gets extracted there; a
    passport's side is hardcoded to "single" in _collect_identity_state
    instead, since it's never genuinely split (see EXTRACTION_SCHEMAS's
    "passport" entry).

    Returns (covered, total) identities, same shape as the proof_of_address
    equivalent — (0, 0) means no id_document has been accepted yet.
    """
    identities, _, _ = _collect_identity_state(documents)
    covered = sum(
        1 for identity in identities.values() if _is_identity_id_document_complete(identity.sides)
    )
    return covered, len(identities)


def compute_id_document_identity_coverage(documents: list[DocumentRecord]) -> bool:
    """Returns False when there are no accepted id_documents yet."""
    covered, total = compute_id_document_identity_progress(documents)
    return total > 0 and covered == total


def compute_id_document_missing_side(documents: list[DocumentRecord]) -> str | None:
    """
    "front" or "back" — whichever is missing — for the frontend's
    human-readable hint ("Back photo present, front missing."). Only
    populated when there's exactly one identity in the case and it's
    incomplete: with more than one identity, a single hint can't describe
    each person's own gap, so callers should fall back to just the
    covered/total fraction in that case.
    """
    identities, _, _ = _collect_identity_state(documents)
    if len(identities) != 1:
        return None
    (identity,) = identities.values()
    if _is_identity_id_document_complete(identity.sides):
        return None
    if "front" in identity.sides:
        return "back"
    if "back" in identity.sides:
        return "front"
    return None


def compute_requirement_coverage(documents: list[DocumentRecord]) -> tuple[list[str], list[str]]:
    """
    A requirement counts as fulfilled only by a document whose
    classification was actually ACCEPTED — one sitting in manual review
    hasn't been confirmed to be what it claims, so it shouldn't retire a
    checklist item yet — and that a loan officer hasn't rejected (see
    models.counts_towards_checklist). A single document can fulfil more than one
    requirement (see DocumentRecord.fulfilled_requirements), so most
    requirements are a union across all of a case's accepted documents, not
    a 1:1 count — proof_of_address is the one exception, requiring coverage
    per identity rather than just once anywhere in the case (see
    compute_identity_proof_of_address_coverage).

    Returns (fulfilled, missing) against the fixed REQUIRED_DOCUMENT_TYPES
    checklist, both in checklist order.
    """
    fulfilled: set[str] = set()
    for doc in documents:
        if counts_towards_checklist(doc):
            fulfilled.update(doc.fulfilled_requirements)

    # Overrides whatever individual documents self-reported for this one
    # requirement — see compute_identity_proof_of_address_coverage for why
    # proof_of_address can't just be unioned like every other requirement.
    proof_of_address = DocumentType.PROOF_OF_ADDRESS.value
    if compute_identity_proof_of_address_coverage(documents):
        fulfilled.add(proof_of_address)
    else:
        fulfilled.discard(proof_of_address)

    # Same idea for id_document: it requires every identified individual's
    # own ID to be complete (front+back, matched by nric_number — not just
    # any front and any back existing somewhere in the case), mirroring
    # proof_of_address's own per-identity requirement above. See
    # compute_id_document_identity_coverage. id_document isn't a real
    # DocumentType (passport/nric are the real classifiable types — see
    # models.IDENTITY_DOCUMENT_TYPES), so it's not DocumentType.X.value
    # like proof_of_address above, just the shared requirement string.
    id_document = IDENTITY_DOCUMENT_REQUIREMENT
    if compute_id_document_identity_coverage(documents):
        fulfilled.add(id_document)
    else:
        fulfilled.discard(id_document)

    # Additional items are reported as fulfilled (so the UI can tick them) but
    # never as missing, so they can't block submission_finalized.
    return (
        [req for req in [*REQUIRED_DOCUMENT_TYPES, *ADDITIONAL_DOCUMENT_TYPES] if req in fulfilled],
        [req for req in REQUIRED_DOCUMENT_TYPES if req not in fulfilled],
    )
