from __future__ import annotations

from datetime import datetime, timezone

from app.mapper import (
    compute_id_document_identity_coverage,
    compute_id_document_identity_progress,
    compute_id_document_missing_side,
    compute_identity_proof_of_address_progress,
    compute_unmatched_proof_of_address_names,
)
from app.models import ClassificationOutcome, DocumentRecord, DocumentStatus


def _make_document(
    document_id: str,
    *,
    document_type: str | None = None,
    classification_outcome: ClassificationOutcome | None = None,
    extracted_fields: dict | None = None,
) -> DocumentRecord:
    now = datetime.now(timezone.utc)
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename=f"{document_id}.pdf",
        storage_path=f"case-1/{document_id}.pdf",
        status=DocumentStatus.ANALYZED,
        uploaded_at=now,
        classification_outcome=classification_outcome,
        document_type=document_type,
        extracted_fields=extracted_fields,
    )


def test_progress_is_zero_of_zero_with_no_identities() -> None:
    assert compute_identity_proof_of_address_progress([]) == (0, 0)


def test_progress_counts_covered_and_total_identities() -> None:
    """The GLDB-20-shaped scenario: one of two identities has an address."""
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": "1 Main St"},
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Bryan Lim", "individual_address": None},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 2)


def test_progress_skips_identity_document_with_no_name_or_nric_number() -> None:
    # An accepted identity document where extraction found neither
    # individual_name nor nric_number contributes no identity at all — it
    # shouldn't crash or be counted as some kind of anonymous identity.
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_address": "1 Main St"},
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": "2 Main St"},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)


def test_progress_tolerates_non_string_individual_name() -> None:
    """
    Real observed failure: a company_constitution naming several directors
    came back from Extraction with individual_name as a list, which crashed
    the whole /cases endpoint for every case, not just this one document.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": None},
        ),
        _make_document(
            "doc-2",
            document_type="company_constitution",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": ["Tan Wei Ming", "Tan Wei Jun"],
                "individual_address": "1 Main St",
            },
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (0, 1)


def test_progress_recognises_passport_as_an_identity_document() -> None:
    """
    passport and nric are two separate DocumentTypes (different extraction
    schemas), but both must count as identity documents here — this guards
    against passport being accidentally left out of IDENTITY_DOCUMENT_TYPES.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="passport",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": None},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (0, 1)


def test_progress_links_passport_to_nric_via_shared_nric_number() -> None:
    """
    A passport's own extraction schema includes nric_number (the "National
    ID No" printed on a real Singapore passport page — see
    extraction_prompts.EXTRACTION_SCHEMAS's "passport" entry), so a
    passport and an NRIC for the same person should merge into one
    identity via that shared number, even with mismatched names (garbled
    OCR on one of them).
    """
    documents = [
        _make_document(
            "doc-passport",
            document_type="passport",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "nric_number": "S1234567D",
                "individual_address": None,
            },
        ),
        _make_document(
            "doc-nric",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "AMANDAKOH",
                "nric_number": "s1234567d",
                "individual_address": "1 Main St",
            },
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)


def test_progress_does_not_link_identities_by_passport_number() -> None:
    """
    passport_number is the passport document's own serial, not a personal
    identifier shared with anything else — two passport rows that happen
    to share a passport_number (shouldn't normally occur, but nothing
    stops a re-extraction glitch from producing it) must NOT be treated as
    the same identity just because of that, unlike nric_number.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="passport",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "passport_number": "E1234567A",
                "individual_address": None,
            },
        ),
        _make_document(
            "doc-2",
            document_type="passport",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Bryan Lim",
                "passport_number": "E1234567A",
                "individual_address": None,
            },
        ),
        _make_document(
            "doc-poa",
            document_type="proof_of_address",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Bryan Lim", "individual_address": "1 Main St"},
        ),
    ]
    # Two distinct names, no nric_number to link them — passport_number
    # alone must not merge these into one identity.
    assert compute_identity_proof_of_address_progress(documents) == (1, 2)


def test_progress_ignores_manual_review_documents() -> None:
    """An id_document not yet ACCEPTED isn't a confirmed identity."""
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.MANUAL_REVIEW,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": "1 Main St"},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (0, 0)


def test_progress_with_three_identities_partial_coverage() -> None:
    """
    Three people, two covered by different means (self, separate document),
    one missing entirely — confirms this scales past the 2-identity case
    rather than something coincidentally working only for exactly two.
    """
    documents = [
        # Amanda: self-covered, her own NRIC shows an address.
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": "1 Main St"},
        ),
        # Bryan: NRIC has no address, but a separate utility bill covers him.
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Bryan Lim", "individual_address": None},
        ),
        _make_document(
            "doc-3",
            document_type="proof_of_address",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Bryan Lim", "individual_address": "2 Side St"},
        ),
        # Cheryl: nothing covers her at all.
        _make_document(
            "doc-4",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Cheryl Ong", "individual_address": None},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (2, 3)


def test_progress_with_three_identities_all_covered() -> None:
    documents = [
        _make_document(
            f"doc-{i}",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": name, "individual_address": f"{i} Main St"},
        )
        for i, name in enumerate(["Amanda Koh", "Bryan Lim", "Cheryl Ong"])
    ]
    assert compute_identity_proof_of_address_progress(documents) == (3, 3)


def test_progress_deduplicates_same_person_across_passport_and_nric() -> None:
    """
    The same individual submitting both an NRIC and a passport is still
    one identity, not two — matched by normalized name, so this must not
    double-count them just because there are two identity-document rows of
    different types (both in IDENTITY_DOCUMENT_TYPES).
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="passport",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": None},
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "  amanda   koh ", "individual_address": "1 Main St"},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)


def test_progress_deduplicates_same_nric_number_despite_mismatched_names() -> None:
    """
    An NRIC uploaded as two separate images (front/back) produces two
    identity-document rows. OCR on the back image can garble or drop the
    name entirely, so name-matching alone would wrongly treat this as two
    identities. Sharing the same nric_number should still merge them into
    one.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "nric_number": "S1234567D",
                "individual_address": None,
            },
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "AMANDAKOH",  # garbled OCR from the back image
                "nric_number": "s1234567d",
                "individual_address": "1 Main St",
            },
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)


def test_progress_links_nric_front_and_back_uploaded_as_separate_files() -> None:
    """
    Mirrors an actual Singapore NRIC (see doc_gen_scripts/nric_passport/
    nric_generator.py): the front prints the name and nric_number but no
    address, and the back prints the nric_number again alongside the
    address but no name at all. Uploaded as two independent files, each
    becomes its own DocumentRecord and is classified/extracted
    independently (there is no grouping step anywhere in the upload
    pipeline — see app/routers/upload.py's _ingest_one). Under the old
    name-only matching, the back row's address would have been silently
    dropped entirely (normalized name is None, so it can't join to
    anything) and this person would incorrectly show as missing
    proof_of_address. Matching by nric_number instead links the two rows
    into one covered identity.
    """
    documents = [
        _make_document(
            "doc-front",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "nric_number": "S1234567D",
                "individual_address": None,
            },
        ),
        _make_document(
            "doc-back",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": None,
                "nric_number": "S1234567D",
                "individual_address": "2 Orchard Turn, #01-01, Singapore 238801",
            },
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)


def test_progress_keeps_distinct_nric_numbers_as_separate_identities() -> None:
    """
    Different nric_numbers means different identities (not merged into one,
    the way pure name-matching would). Coverage is still checked by name,
    since proof_of_address documents carry no nric_number to join on — so
    two different people who happen to share a name are both counted as
    covered here once either of them has an address on file. That's a
    known, pre-existing limitation of name-based address matching (the old
    purely-name-keyed code had the same blind spot, just via merging them
    into a single identity outright instead), not something this
    nric_number grouping is expected to solve.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "nric_number": "S1234567D",
                "individual_address": "1 Main St",
            },
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "nric_number": "T7654321Z",
                "individual_address": None,
            },
        ),
    ]
    # Two identities (distinct nric_numbers) — both read as covered because
    # address-matching falls back to the shared name across identities.
    assert compute_identity_proof_of_address_progress(documents) == (2, 2)


def test_unmatched_names_is_empty_with_no_address_documents() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": None},
        ),
    ]
    assert compute_unmatched_proof_of_address_names(documents) == []


def test_unmatched_names_surfaces_a_proof_of_address_that_matches_no_identity() -> None:
    """
    Real observed scenario: a proof_of_address for "Chan Mei Xuan" was
    uploaded, then an unrelated NRIC for "Lim Hui Ling" that happens to
    carry its own address (self-covered — see the self-coverage tests
    above). Chan Mei Xuan's address never matches Lim Hui Ling's identity
    and isn't counted in proof_of_address_coverage's total at all (there's
    no id_document for her), so without this it just silently does
    nothing — this surfaces it as a "hanging" address instead.
    """
    documents = [
        _make_document(
            "doc-poa",
            document_type="proof_of_address",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Chan Mei Xuan",
                "individual_address": "16 Bukit Panjang Ring Road, #04-16, Singapore 104065",
            },
        ),
        _make_document(
            "doc-nric",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "LIM HUI LING",
                "nric_number": "S8523456B",
                "individual_address": "28 BUKIT BATOK EAST AVENUE #04-47 SINGAPORE 278302",
            },
        ),
    ]
    # Lim Hui Ling is covered by her own NRIC; Chan Mei Xuan is unmatched.
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)
    assert compute_unmatched_proof_of_address_names(documents) == ["Chan Mei Xuan"]


def test_unmatched_names_excludes_a_name_that_does_match_an_identity() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Bryan Lim", "individual_address": None},
        ),
        _make_document(
            "doc-2",
            document_type="proof_of_address",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "bryan   lim", "individual_address": "2 Side St"},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)
    assert compute_unmatched_proof_of_address_names(documents) == []


def test_unmatched_names_ignores_manual_review_documents() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="proof_of_address",
            classification_outcome=ClassificationOutcome.MANUAL_REVIEW,
            extracted_fields={"individual_name": "Chan Mei Xuan", "individual_address": "16 Bukit Panjang"},
        ),
    ]
    assert compute_unmatched_proof_of_address_names(documents) == []


def test_constitution_subscriber_addresses_do_not_cover_anyone() -> None:
    """
    A company_constitution listing people's addresses isn't proof of address
    for them, even when the names match an identity's id_document exactly —
    and those ignored addresses don't surface as "unmatched" either.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Ho Chloe", "nric_number": "S8905952H"},
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Vikram s/o Ramesh", "nric_number": "E8762066G"},
        ),
        _make_document(
            "doc-3",
            document_type="company_constitution",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "subscribers": [
                    {"name": "Ho Chloe", "address": "33 Geylang East Avenue", "id_number": "S8905952H"},
                    {"name": "Ow Fook Chuen", "address": "68 Circular Road", "id_number": "S1234567A"},
                ],
            },
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (0, 2)
    assert compute_unmatched_proof_of_address_names(documents) == []


def test_incumbency_address_does_not_cover_anyone() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Ho Chloe", "nric_number": "S8905952H"},
        ),
        _make_document(
            "doc-2",
            document_type="certificate_of_incumbency",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Ho Chloe", "individual_address": "33 Geylang East Avenue"},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (0, 1)
    assert compute_unmatched_proof_of_address_names(documents) == []


def test_corporate_bank_statement_address_does_not_cover_identity() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh"},
        ),
        _make_document(
            "doc-2",
            document_type="bank_statement",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "company_name": "Acme Pte Ltd",
                "individual_name": "Amanda Koh",
                "individual_address": "1 Main St",
            },
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (0, 1)
    assert compute_unmatched_proof_of_address_names(documents) == []


def test_bank_statement_covers_identity_whose_nric_has_no_address() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh"},
        ),
        _make_document(
            "doc-2",
            document_type="bank_statement",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": "1 Main St"},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)


def test_constitution_subscribers_never_create_a_new_identity() -> None:
    """
    Identities come only from id_document uploads — a constitution naming
    someone (with their own id_number, even) must never itself count as
    "1 identity" or as fulfilling id_document; it can only match an
    identity an actual id_document already established.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="company_constitution",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "subscribers": [
                    {"name": "Ho Chloe", "address": "33 Geylang East Ave", "id_number": "S8905952H"},
                ],
            },
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (0, 0)
    assert compute_id_document_identity_progress(documents) == (0, 0)


def test_constitution_with_malformed_subscribers_does_not_crash() -> None:
    """
    subscribers is LLM output, not a validated schema — a constitution's
    addresses are ignored for proof_of_address regardless, so malformed
    entries must neither crash the mapper nor surface anything.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="company_constitution",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "subscribers": [
                    "not even a dict",
                    {"name": "No Address Person"},
                    {"name": None, "address": "1 Somewhere Road"},
                    {"name": "", "address": "2 Somewhere Road"},
                    {"name": "Well Formed Person", "address": "3 Somewhere Road"},
                ],
            },
        ),
    ]
    assert compute_unmatched_proof_of_address_names(documents) == []


def test_address_document_with_blank_name_is_ignored() -> None:
    """A name that's a string but empty normalises to nothing, so it can't name anyone."""
    documents = [
        _make_document(
            "doc-1",
            document_type="proof_of_address",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "", "individual_address": "1 Main St"},
        ),
    ]
    assert compute_unmatched_proof_of_address_names(documents) == []


def test_id_document_identity_progress_zero_with_no_id_documents() -> None:
    assert compute_id_document_identity_progress([]) == (0, 0)


def test_id_document_identity_progress_one_identity_only_back_uploaded() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"nric_number": "S8905952H", "document_side": "back"},
        ),
    ]
    assert compute_id_document_identity_progress(documents) == (0, 1)
    assert compute_id_document_missing_side(documents) == "front"


def test_id_document_missing_side_is_back_when_only_front_uploaded() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"nric_number": "S8905952H", "document_side": "front"},
        ),
    ]
    assert compute_id_document_missing_side(documents) == "back"


def test_id_document_missing_side_is_none_when_side_was_never_extracted() -> None:
    """
    A single identity that's incomplete but has neither "front" nor "back"
    recorded (document_side missing/unrecognised on its one document) can't
    be described as "X missing" — there's nothing to name.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"nric_number": "S8905952H"},
        ),
    ]
    assert compute_id_document_missing_side(documents) is None


def test_id_document_identity_progress_covered_once_same_identity_has_both_sides() -> None:
    """Front and back matched by the same nric_number — same NRIC, two photos."""
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "nric_number": "S8905952H",
                "document_side": "front",
            },
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"nric_number": "S8905952H", "document_side": "back"},
        ),
    ]
    assert compute_id_document_identity_progress(documents) == (1, 1)
    assert compute_id_document_missing_side(documents) is None


def test_id_document_two_different_identities_half_complete_each_does_not_combine() -> None:
    """
    The bug this was written to catch: person A's back and person B's front
    both being present in the case must NOT read as one complete pair —
    each is a distinct identity (different nric_number), and neither one's
    own ID document is complete on its own.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"nric_number": "S8905952H", "document_side": "back"},
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Bryan Lim",
                "nric_number": "T0123456A",
                "document_side": "front",
            },
        ),
    ]
    assert compute_id_document_identity_progress(documents) == (0, 2)
    assert compute_id_document_identity_coverage(documents) is False
    # More than one identity in play — a single hint can't describe both
    # people's own gap, so this deliberately returns nothing rather than a
    # misleading single answer.
    assert compute_id_document_missing_side(documents) is None


def test_id_document_two_backs_for_same_identity_still_incomplete() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"nric_number": "S8905952H", "document_side": "back"},
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"nric_number": "S8905952H", "document_side": "back"},
        ),
    ]
    assert compute_id_document_identity_progress(documents) == (0, 1)


def test_id_document_identity_progress_ignores_manual_review_and_unknown_side() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="nric",
            classification_outcome=ClassificationOutcome.MANUAL_REVIEW,
            extracted_fields={"nric_number": "S8905952H", "document_side": "front"},
        ),
        _make_document(
            "doc-2",
            document_type="nric",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"nric_number": "T0123456A", "document_side": "unknown"},
        ),
    ]
    assert compute_id_document_identity_progress(documents) == (0, 1)


def test_id_document_single_capture_is_immediately_complete() -> None:
    """A passport bio page (or a combined single scan) needs no counterpart."""
    documents = [
        _make_document(
            "doc-1",
            document_type="passport",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "document_side": "single"},
        ),
    ]
    assert compute_id_document_identity_progress(documents) == (1, 1)
    assert compute_id_document_identity_coverage(documents) is True


