from __future__ import annotations

from datetime import datetime, timezone

from app.mapper import compute_identity_proof_of_address_progress
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
