from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.case_status import (
    _enrich_with_extracted_fields,
    compute_documents_status,
    compute_requirement_coverage,
    sync_submission_finalized,
)
from app.fraud_detection.font_consistency_v2 import ExtractedField
from app.config import settings
from app.models import (
    ADDITIONAL_DOCUMENT_TYPES,
    REQUIRED_DOCUMENT_TYPES,
    CaseDocumentsStatus,
    ClassificationOutcome,
    DocumentRecord,
    DocumentStatus,
    compute_fulfilled_requirements,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_document(
    document_id: str,
    status: DocumentStatus,
    *,
    uploaded_seconds_ago: float = 0,
    queued_seconds_ago: float | None = None,
    processing_seconds_ago: float | None = None,
    classification_outcome: ClassificationOutcome | None = None,
    document_type: str | None = None,
    fulfilled_requirements: list[str] | None = None,
    extracted_fields: dict | None = None,
) -> DocumentRecord:
    now = _now()
    return DocumentRecord(
        id=document_id,
        case_id="case-1",
        filename=f"{document_id}.pdf",
        storage_path=f"case-1/{document_id}.pdf",
        status=status,
        uploaded_at=now - timedelta(seconds=uploaded_seconds_ago),
        queued_at=(now - timedelta(seconds=queued_seconds_ago)) if queued_seconds_ago else None,
        processing_started_at=(
            (now - timedelta(seconds=processing_seconds_ago)) if processing_seconds_ago else None
        ),
        classification_outcome=classification_outcome,
        document_type=document_type,
        fulfilled_requirements=fulfilled_requirements or [],
        extracted_fields=extracted_fields,
    )


def test_enrich_with_extracted_fields_fills_in_fields_for_matching_documents() -> None:
    doc_with_fields = _make_document("doc-1", DocumentStatus.ANALYZED)
    doc_without_fields = _make_document("doc-2", DocumentStatus.ANALYZED)

    with patch("app.case_status.extracted_fields_repository") as mock_repo:
        mock_repo.get_extracted_fields_for_documents.return_value = {
            "doc-1": [
                ExtractedField(
                    field_name="individual_name",
                    field_type="string",
                    field_value="Jane Tan",
                    confidence=0.95,
                ),
            ],
        }

        enriched = _enrich_with_extracted_fields([doc_with_fields, doc_without_fields])

    mock_repo.get_extracted_fields_for_documents.assert_called_once_with(["doc-1", "doc-2"])
    assert enriched[0].extracted_fields == {
        "individual_name": {"value": "Jane Tan", "confidence": 0.95}
    }
    # No matching row for doc-2 — left as whatever it already had (None here).
    assert enriched[1].extracted_fields is None


def test_enrich_with_extracted_fields_handles_empty_document_list() -> None:
    with patch("app.case_status.extracted_fields_repository") as mock_repo:
        mock_repo.get_extracted_fields_for_documents.return_value = {}

        assert _enrich_with_extracted_fields([]) == []


def test_no_documents_returns_no_documents_status() -> None:
    assert compute_documents_status([]) == CaseDocumentsStatus.NO_DOCUMENTS


def test_all_analyzed_returns_analyzed() -> None:
    documents = [_make_document(f"doc-{i}", DocumentStatus.ANALYZED) for i in range(3)]
    assert compute_documents_status(documents) == CaseDocumentsStatus.ANALYZED


def test_one_stuck_among_many_analyzed_returns_stuck() -> None:
    """The exact scenario from the ask: 9 analyzed + 1 stuck reads 'stuck'."""
    old_enough = settings.STUCK_THRESHOLD_SECONDS + 5
    documents = [_make_document(f"doc-{i}", DocumentStatus.ANALYZED) for i in range(9)]
    documents.append(
        _make_document("doc-stuck", DocumentStatus.QUEUED, queued_seconds_ago=old_enough)
    )

    assert compute_documents_status(documents) == CaseDocumentsStatus.STUCK


def test_recently_queued_document_is_not_stuck() -> None:
    documents = [_make_document("doc-1", DocumentStatus.QUEUED, queued_seconds_ago=1)]
    assert compute_documents_status(documents) == CaseDocumentsStatus.IN_PROGRESS


def test_failed_outranks_in_progress_but_not_stuck() -> None:
    documents = [
        _make_document("doc-1", DocumentStatus.FAILED),
        _make_document("doc-2", DocumentStatus.QUEUED, queued_seconds_ago=1),
        _make_document("doc-3", DocumentStatus.ANALYZED),
    ]
    assert compute_documents_status(documents) == CaseDocumentsStatus.FAILED


def test_stuck_outranks_failed() -> None:
    old_enough = settings.STUCK_THRESHOLD_SECONDS + 5
    documents = [
        _make_document("doc-1", DocumentStatus.FAILED),
        _make_document("doc-2", DocumentStatus.PROCESSING, processing_seconds_ago=old_enough),
    ]
    assert compute_documents_status(documents) == CaseDocumentsStatus.STUCK


def test_mixed_uploaded_queued_processing_returns_in_progress() -> None:
    documents = [
        _make_document("doc-1", DocumentStatus.UPLOADED),
        _make_document("doc-2", DocumentStatus.QUEUED, queued_seconds_ago=1),
        _make_document("doc-3", DocumentStatus.PROCESSING, processing_seconds_ago=1),
    ]
    assert compute_documents_status(documents) == CaseDocumentsStatus.IN_PROGRESS


def test_coverage_with_no_documents_is_all_missing() -> None:
    fulfilled, missing = compute_requirement_coverage([])
    assert fulfilled == []
    assert missing  # the full checklist


def test_single_document_can_fulfil_multiple_requirements() -> None:
    """The story's own example: an NRIC also covers Proof of Address."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            fulfilled_requirements=["id_document", "proof_of_address"],
            extracted_fields={
                "individual_name": "Jane Tan",
                "individual_address": "1 Main St",
                # Self-contained (a single combined capture) rather than
                # "front"/"back" — this test is about one document fulfilling
                # two *different* requirement types at once, not about the
                # front/back completeness mechanic (see
                # mapper.compute_id_document_side_coverage).
                "document_side": "single",
            },
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "id_document" in fulfilled
    assert "proof_of_address" in fulfilled
    assert "id_document" not in missing
    assert "proof_of_address" not in missing


def test_manual_review_document_does_not_count_as_fulfilled() -> None:
    """An unconfirmed classification shouldn't retire a checklist item."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.MANUAL_REVIEW,
            fulfilled_requirements=["bank_statement"],
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "bank_statement" not in fulfilled
    assert "bank_statement" in missing


def test_additional_list_of_loans_is_ticked_but_never_missing() -> None:
    """An additional item shows as fulfilled once accepted, and never blocks the checklist."""
    assert "list_of_loans" not in REQUIRED_DOCUMENT_TYPES

    fulfilled, missing = compute_requirement_coverage([])
    assert "list_of_loans" not in fulfilled
    assert "list_of_loans" not in missing

    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="list_of_loans",
            fulfilled_requirements=["list_of_loans"],
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "list_of_loans" in fulfilled
    assert "list_of_loans" not in missing


def test_passport_does_not_fulfil_proof_of_address() -> None:
    """A passport is an identity document but carries no address, unlike an NRIC."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="passport",
            fulfilled_requirements=["id_document"],
            extracted_fields={
                "individual_name": "Jane Tan",
                "individual_address": None,
                # A passport's bio page is self-contained — there's no
                # separate "back" to wait for, unlike an NRIC.
                "document_side": "single",
            },
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "id_document" in fulfilled
    assert "proof_of_address" in missing


def test_id_document_back_only_does_not_fulfil_requirement() -> None:
    """The back of an NRIC alone isn't a complete identity document."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            fulfilled_requirements=["id_document"],
            extracted_fields={"nric_number": "S8905952H", "document_side": "back"},
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "id_document" not in fulfilled
    assert "id_document" in missing


def test_id_document_front_and_back_together_fulfil_requirement() -> None:
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            fulfilled_requirements=["id_document"],
            # Same nric_number as doc-2 — same NRIC, two separate photos of
            # the same identity. Without a matching nric_number these would
            # register as two different (each incomplete) identities — see
            # test_id_document_two_different_people_half_complete_each_does_not_fulfil
            # below for that exact case.
            extracted_fields={
                "individual_name": "Jane Tan",
                "nric_number": "S8905952H",
                "document_side": "front",
            },
        ),
        _make_document(
            "doc-2",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            fulfilled_requirements=["id_document"],
            extracted_fields={"nric_number": "S8905952H", "document_side": "back"},
        ),
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "id_document" in fulfilled
    assert "id_document" not in missing


def test_id_document_two_different_people_half_complete_each_does_not_fulfil() -> None:
    """
    Regression test for the reported bug: person A's back and person B's
    front both being present must not combine into a false "complete" —
    each is a distinct identity (different nric_number) and neither one's
    own ID document is actually complete.
    """
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            fulfilled_requirements=["id_document"],
            extracted_fields={"nric_number": "S8905952H", "document_side": "back"},
        ),
        _make_document(
            "doc-2",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            fulfilled_requirements=["id_document"],
            extracted_fields={
                "individual_name": "Bryan Lim",
                "nric_number": "T0123456A",
                "document_side": "front",
            },
        ),
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "id_document" not in fulfilled
    assert "id_document" in missing


def test_proof_of_address_requires_coverage_for_every_identity() -> None:
    """
    Two different people's ID documents in the same case — an address
    matched to only one of them must not retire the checklist item, since
    the other identity still has no address on file.
    """
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            extracted_fields={"individual_name": "Jane Tan", "individual_address": "1 Main St"},
        ),
        _make_document(
            "doc-2",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            extracted_fields={"individual_name": "John Lim", "individual_address": None},
        ),
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "proof_of_address" in missing
    assert "proof_of_address" not in fulfilled


def test_proof_of_address_satisfied_by_separate_document_matched_by_name() -> None:
    """
    An identity's address doesn't have to come from their own ID document —
    a separate proof_of_address-type document naming the same person also
    counts, matched by normalized individual_name.
    """
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            extracted_fields={"individual_name": "Jane Tan", "individual_address": None},
        ),
        _make_document(
            "doc-2",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="proof_of_address",
            extracted_fields={"individual_name": "  jane   tan  ", "individual_address": "1 Main St"},
        ),
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "proof_of_address" in fulfilled
    assert "proof_of_address" not in missing


def test_proof_of_address_with_multiple_identities_all_covered() -> None:
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            extracted_fields={"individual_name": "Jane Tan", "individual_address": "1 Main St"},
        ),
        _make_document(
            "doc-2",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric",
            extracted_fields={"individual_name": "John Lim", "individual_address": None},
        ),
        _make_document(
            "doc-3",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="proof_of_address",
            extracted_fields={"individual_name": "John Lim", "individual_address": "2 Side St"},
        ),
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "proof_of_address" in fulfilled
    assert "proof_of_address" not in missing


def test_proof_of_address_missing_when_no_identity_yet() -> None:
    """No ID document uploaded at all — nothing to check coverage against."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="proof_of_address",
            extracted_fields={"individual_name": "Jane Tan", "individual_address": "1 Main St"},
        ),
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "proof_of_address" in missing


def _make_fully_covered_documents() -> list[DocumentRecord]:
    """
    One document per checklist requirement. "id_document" isn't a
    real document_type any document ever has (see
    models.IDENTITY_DOCUMENT_REQUIREMENT) — fulfilled by an actual "nric"
    document here, which also carries its own individual_address so
    proof_of_address's identity-based check (not just its own
    fulfilled_requirements) is satisfied too.
    """
    return [
        _make_document(
            f"doc-{i}",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="nric" if requirement == "id_document" else requirement,
            fulfilled_requirements=[requirement],
            extracted_fields=(
                {
                    "individual_name": "Jane Tan",
                    "individual_address": "1 Main St",
                    "document_side": "single",
                }
                if requirement == "id_document"
                else None
            ),
        )
        for i, requirement in enumerate(REQUIRED_DOCUMENT_TYPES)
    ]


def test_sync_submission_finalized_true_when_all_requirements_met() -> None:
    all_fulfilled = _make_fully_covered_documents()

    with (
        patch("app.case_status.document_repository") as mock_doc_repo,
        patch("app.case_status.case_repository") as mock_case_repo,
    ):
        mock_doc_repo.list_documents.return_value = all_fulfilled

        sync_submission_finalized("case-1")

        mock_case_repo.set_submission_finalized.assert_called_once_with("case-1", finalized=True)


def test_sync_submission_finalized_false_when_incomplete() -> None:
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            fulfilled_requirements=["bank_statement"],
        )
    ]

    with (
        patch("app.case_status.document_repository") as mock_doc_repo,
        patch("app.case_status.case_repository") as mock_case_repo,
    ):
        mock_doc_repo.list_documents.return_value = documents

        sync_submission_finalized("case-1")

        mock_case_repo.set_submission_finalized.assert_called_once_with("case-1", finalized=False)


def test_sync_submission_finalized_reverses_when_coverage_drops() -> None:
    """
    Simulates a /retry reclassifying a document away from a requirement
    nothing else covers — a previously-complete case must flip back to
    finalized=False, not keep a stale True.
    """
    with (
        patch("app.case_status.document_repository") as mock_doc_repo,
        patch("app.case_status.case_repository") as mock_case_repo,
    ):
        mock_doc_repo.list_documents.return_value = _make_fully_covered_documents()
        sync_submission_finalized("case-1")
        mock_case_repo.set_submission_finalized.assert_called_with("case-1", finalized=True)

        # doc-0 (bank_statement, not the identity document) is now
        # reclassified away — its fulfilled_requirements goes empty, leaving
        # the NRIC's own address-based proof_of_address coverage untouched.
        dropped = _make_fully_covered_documents()
        dropped[0] = dropped[0].model_copy(update={"fulfilled_requirements": []})
        mock_doc_repo.list_documents.return_value = dropped

        sync_submission_finalized("case-1")
        mock_case_repo.set_submission_finalized.assert_called_with("case-1", finalized=False)


def test_required_and_additional_requirements_are_distinct() -> None:
    """AC1: the checklist tells required and additional requirements apart."""
    assert ADDITIONAL_DOCUMENT_TYPES == [
        "list_of_loans",
        "company_proof_of_address",
        "source_of_personal_wealth",
    ]
    assert not set(ADDITIONAL_DOCUMENT_TYPES) & set(REQUIRED_DOCUMENT_TYPES)
    # NOA is a required item (see the document requirements table).
    assert "notice_of_assessment" in REQUIRED_DOCUMENT_TYPES


def test_case_finalizes_without_additional_document() -> None:
    """AC2: a case with every required document is complete, additional or not."""
    documents = _make_fully_covered_documents()
    assert not any(doc.document_type in ADDITIONAL_DOCUMENT_TYPES for doc in documents)

    with (
        patch("app.case_status.document_repository") as mock_doc_repo,
        patch("app.case_status.case_repository") as mock_case_repo,
    ):
        mock_doc_repo.list_documents.return_value = documents
        sync_submission_finalized("case-1")
        mock_case_repo.set_submission_finalized.assert_called_once_with("case-1", finalized=True)

    _, missing = compute_requirement_coverage(documents)
    assert missing == []


def test_additional_document_maps_to_its_own_requirement() -> None:
    """AC3: an uploaded additional document fulfils its own requirement."""
    assert compute_fulfilled_requirements("list_of_loans", None) == ["list_of_loans"]


def test_uploaded_additional_document_does_not_change_completeness() -> None:
    """AC3/AC4: an additional item shows as fulfilled but neither hides a missing required item nor adds one."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="list_of_loans",
            fulfilled_requirements=["list_of_loans"],
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert fulfilled == ["list_of_loans"]
    assert missing == REQUIRED_DOCUMENT_TYPES


def test_proof_of_address_before_extraction_is_personal() -> None:
    """Classification runs before extraction, so with no fields yet it stays the personal item."""
    assert compute_fulfilled_requirements("proof_of_address", None) == ["proof_of_address"]


def test_personal_proof_of_address_fulfils_only_personal_item() -> None:
    fulfilled = compute_fulfilled_requirements(
        "proof_of_address",
        {"individual_name": "Jane Tan", "individual_address": "1 Main St"},
    )
    assert fulfilled == ["proof_of_address"]


def test_company_proof_of_address_fulfils_only_the_company_item() -> None:
    """A company address says nothing about any person's residence."""
    fulfilled = compute_fulfilled_requirements(
        "proof_of_address",
        {"company_name": "Acme Pte Ltd", "company_address": "10 Robinson Rd"},
    )
    assert fulfilled == ["company_proof_of_address"]


def test_proof_of_address_with_both_addresses_fulfils_both_items() -> None:
    fulfilled = compute_fulfilled_requirements(
        "proof_of_address",
        {"individual_address": "1 Main St", "company_address": "10 Robinson Rd"},
    )
    assert set(fulfilled) == {"proof_of_address", "company_proof_of_address"}


def test_company_address_on_other_document_types_does_not_fulfil_company_proof() -> None:
    """Only a proof_of_address document counts (a bank statement also carries a company address)."""
    fulfilled = compute_fulfilled_requirements(
        "bank_statement", {"company_address": "10 Robinson Rd"}
    )
    assert fulfilled == ["bank_statement"]


def test_accepted_company_proof_of_address_is_ticked_but_never_missing() -> None:
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="proof_of_address",
            fulfilled_requirements=["company_proof_of_address"],
            extracted_fields={"company_address": "10 Robinson Rd"},
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "company_proof_of_address" in fulfilled
    assert "company_proof_of_address" not in missing
    # It doesn't count as anyone's personal proof of address.
    assert "proof_of_address" not in fulfilled
    assert "proof_of_address" in missing


def test_source_of_wealth_before_extraction_is_corporate() -> None:
    assert compute_fulfilled_requirements("source_of_wealth", None) == ["source_of_wealth"]


def test_corporate_source_of_wealth_with_uen_fulfils_only_the_corporate_item() -> None:
    fulfilled = compute_fulfilled_requirements(
        "source_of_wealth", {"company_name": "Golden Harvest Trading Pte Ltd", "uen": "201812345A"}
    )
    assert fulfilled == ["source_of_wealth"]


def test_personal_brokerage_statement_fulfils_only_the_personal_item() -> None:
    """The broker's own Pte. Ltd. name must not make a personal statement read as corporate."""
    fulfilled = compute_fulfilled_requirements(
        "source_of_wealth",
        {
            "account_holder_name": "Nurul Hassan",
            "broker_name": "Moomoo Financial Singapore Pte. Ltd.",
            "company_name": "Moomoo Financial Singapore Pte. Ltd.",
        },
    )
    assert fulfilled == ["source_of_personal_wealth"]


def test_personal_source_of_wealth_with_address_also_fulfils_proof_of_address() -> None:
    fulfilled = compute_fulfilled_requirements(
        "source_of_wealth",
        {"account_holder_name": "Nurul Hassan", "individual_address": "16 Eng Kong Terrace"},
    )
    assert set(fulfilled) == {"source_of_personal_wealth", "proof_of_address"}


def test_company_statement_naming_a_director_stays_corporate() -> None:
    """A UEN marks it corporate even if a person (e.g. a director) is named on it."""
    fulfilled = compute_fulfilled_requirements(
        "source_of_wealth", {"individual_name": "Tan Wei Ming", "uen": "201812345A"}
    )
    assert fulfilled == ["source_of_wealth"]


def test_accepted_personal_source_of_wealth_is_ticked_but_never_missing() -> None:
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            classification_outcome=ClassificationOutcome.ACCEPTED,
            document_type="source_of_wealth",
            fulfilled_requirements=["source_of_personal_wealth"],
            extracted_fields={"account_holder_name": "Nurul Hassan"},
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "source_of_personal_wealth" in fulfilled
    assert "source_of_personal_wealth" not in missing
    # It doesn't retire the required corporate source of wealth item.
    assert "source_of_wealth" in missing
