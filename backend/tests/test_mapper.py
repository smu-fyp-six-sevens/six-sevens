from __future__ import annotations

from datetime import datetime, timezone

from app.mapper import (
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
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": "1 Main St"},
        ),
        _make_document(
            "doc-2",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Bryan Lim", "individual_address": None},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 2)


def test_progress_skips_id_document_with_no_name_or_id_number() -> None:
    # An accepted id_document where extraction found neither individual_name
    # nor id_number contributes no identity at all — it shouldn't crash or
    # be counted as some kind of anonymous identity.
    documents = [
        _make_document(
            "doc-1",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_address": "1 Main St"},
        ),
        _make_document(
            "doc-2",
            document_type="id_document",
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
            document_type="id_document",
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


def test_progress_ignores_manual_review_documents() -> None:
    """An id_document not yet ACCEPTED isn't a confirmed identity."""
    documents = [
        _make_document(
            "doc-1",
            document_type="id_document",
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
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": "1 Main St"},
        ),
        # Bryan: NRIC has no address, but a separate utility bill covers him.
        _make_document(
            "doc-2",
            document_type="id_document",
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
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Cheryl Ong", "individual_address": None},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (2, 3)


def test_progress_with_three_identities_all_covered() -> None:
    documents = [
        _make_document(
            f"doc-{i}",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": name, "individual_address": f"{i} Main St"},
        )
        for i, name in enumerate(["Amanda Koh", "Bryan Lim", "Cheryl Ong"])
    ]
    assert compute_identity_proof_of_address_progress(documents) == (3, 3)


def test_progress_deduplicates_same_person_across_two_id_documents() -> None:
    """
    The same individual submitting both an NRIC and a passport is still
    one identity, not two — matched by normalized name, so this must not
    double-count them just because there are two id_document rows.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "Amanda Koh", "individual_address": None},
        ),
        _make_document(
            "doc-2",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={"individual_name": "  amanda   koh ", "individual_address": "1 Main St"},
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)


def test_progress_deduplicates_same_id_number_despite_mismatched_names() -> None:
    """
    An NRIC uploaded as two separate images (front/back) produces two
    id_document rows. OCR on the back image can garble or drop the name
    entirely, so name-matching alone would wrongly treat this as two
    identities. Sharing the same id_number should still merge them into one.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "id_number": "S1234567D",
                "individual_address": None,
            },
        ),
        _make_document(
            "doc-2",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "AMANDAKOH",  # garbled OCR from the back image
                "id_number": "s1234567d",
                "individual_address": "1 Main St",
            },
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)


def test_progress_links_nric_front_and_back_uploaded_as_separate_files() -> None:
    """
    Mirrors an actual Singapore NRIC (see doc_gen_scripts/nric_passport/
    nric_generator.py): the front prints the name and id_number but no
    address, and the back prints the id_number again alongside the address
    but no name at all. Uploaded as two independent files, each becomes its
    own DocumentRecord and is classified/extracted independently (there is
    no grouping step anywhere in the upload pipeline — see
    app/routers/upload.py's _ingest_one). Under the old name-only matching,
    the back row's address would have been silently dropped entirely
    (normalized name is None, so it can't join to anything) and this person
    would incorrectly show as missing proof_of_address. Matching by
    id_number instead links the two rows into one covered identity.
    """
    documents = [
        _make_document(
            "doc-front",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "id_number": "S1234567D",
                "individual_address": None,
            },
        ),
        _make_document(
            "doc-back",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": None,
                "id_number": "S1234567D",
                "individual_address": "2 Orchard Turn, #01-01, Singapore 238801",
            },
        ),
    ]
    assert compute_identity_proof_of_address_progress(documents) == (1, 1)


def test_progress_keeps_distinct_id_numbers_as_separate_identities() -> None:
    """
    Different id_numbers means different identities (not merged into one,
    the way pure name-matching would). Coverage is still checked by name,
    since proof_of_address documents carry no id_number to join on — so two
    different people who happen to share a name are both counted as
    covered here once either of them has an address on file. That's a
    known, pre-existing limitation of name-based address matching (the old
    purely-name-keyed code had the same blind spot, just via merging them
    into a single identity outright instead), not something this id_number
    grouping is expected to solve.
    """
    documents = [
        _make_document(
            "doc-1",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "id_number": "S1234567D",
                "individual_address": "1 Main St",
            },
        ),
        _make_document(
            "doc-2",
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "Amanda Koh",
                "id_number": "T7654321Z",
                "individual_address": None,
            },
        ),
    ]
    # Two identities (distinct id_numbers) — both read as covered because
    # address-matching falls back to the shared name across identities.
    assert compute_identity_proof_of_address_progress(documents) == (2, 2)


def test_unmatched_names_is_empty_with_no_address_documents() -> None:
    documents = [
        _make_document(
            "doc-1",
            document_type="id_document",
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
            document_type="id_document",
            classification_outcome=ClassificationOutcome.ACCEPTED,
            extracted_fields={
                "individual_name": "LIM HUI LING",
                "id_number": "S8523456B",
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
            document_type="id_document",
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
