from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.case_service import (
    _enrich_with_extracted_fields,
    compute_documents_status,
    compute_requirement_coverage,
)
from app.fraud_detection.font_consistency import ExtractedField
from app.config import settings
from app.models import (
    ADDITIONAL_DOCUMENT_TYPES,
    REQUIRED_DOCUMENT_TYPES,
    CaseDocumentsStatus,
    RoutingStatus,
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
    routing_status: RoutingStatus | None = None,
    document_type: str | None = None,
    fulfilled_requirements: list[str] | None = None,
    extracted_fields: dict | None = None,
    # Confident by default: below the threshold an accepted document doesn't
    # count until an officer confirms its type (models.counts_towards_checklist).
    classification_confidence: float | None = 0.97,
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
        routing_status=routing_status,
        document_type=document_type,
        classification_confidence=classification_confidence,
        fulfilled_requirements=fulfilled_requirements or [],
        extracted_fields=extracted_fields,
    )


def test_enrich_with_extracted_fields_fills_in_fields_for_matching_documents() -> None:
    doc_with_fields = _make_document("doc-1", DocumentStatus.ANALYZED)
    doc_without_fields = _make_document("doc-2", DocumentStatus.ANALYZED)

    with patch("app.case_service.extracted_fields_repository") as mock_repo:
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


def test_enrich_with_extracted_fields_reports_empty_required_fields() -> None:
    # The case overview's Manual Review status reads missing_required_fields
    # off the snapshot, so a blank or absent required field has to show up here.
    payslip = _make_document("doc-1", DocumentStatus.ANALYZED, document_type="payslip")

    with patch("app.case_service.extracted_fields_repository") as mock_repo:
        mock_repo.get_extracted_fields_for_documents.return_value = {
            "doc-1": [
                ExtractedField(field_name="individual_name", field_type="string", field_value="Jane Tan", confidence=0.95),
                ExtractedField(field_name="company_name", field_type="string", field_value="  ", confidence=0.95),
            ],
        }

        enriched = _enrich_with_extracted_fields([payslip])

    assert enriched[0].missing_required_fields == ["company_name", "net_pay"]


def test_enrich_with_extracted_fields_reports_all_required_fields_when_none_extracted() -> None:
    payslip = _make_document("doc-1", DocumentStatus.ANALYZED, document_type="payslip")

    with patch("app.case_service.extracted_fields_repository") as mock_repo:
        mock_repo.get_extracted_fields_for_documents.return_value = {}

        enriched = _enrich_with_extracted_fields([payslip])

    assert enriched[0].missing_required_fields == ["company_name", "individual_name", "net_pay"]


def test_enrich_with_extracted_fields_skips_missing_fields_while_in_flight() -> None:
    # Extraction hasn't run yet — reporting every field as missing would flip
    # the overview to Manual Review instead of Pending.
    payslip = _make_document("doc-1", DocumentStatus.EXTRACTING, document_type="payslip")

    with patch("app.case_service.extracted_fields_repository") as mock_repo:
        mock_repo.get_extracted_fields_for_documents.return_value = {}

        enriched = _enrich_with_extracted_fields([payslip])

    assert enriched[0].missing_required_fields == []


def test_enrich_with_extracted_fields_handles_empty_document_list() -> None:
    with patch("app.case_service.extracted_fields_repository") as mock_repo:
        mock_repo.get_extracted_fields_for_documents.return_value = {}

        assert _enrich_with_extracted_fields([]) == []


def test_no_documents_returns_no_documents_status() -> None:
    assert compute_documents_status([]) == CaseDocumentsStatus.NO_DOCUMENTS


def test_all_analyzed_returns_analyzed() -> None:
    documents = [_make_document(f"doc-{i}", DocumentStatus.ANALYZED) for i in range(3)]
    assert compute_documents_status(documents) == CaseDocumentsStatus.ANALYZED


def test_one_stuck_among_many_analyzed_returns_stuck() -> None:
    """The exact scenario from the ask: 9 analyzed + 1 stuck reads 'stuck'."""
    old_enough = settings.STUCK_WAITING_SECONDS + 5
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
    old_enough = settings.STUCK_RUNNING_SECONDS + 5
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
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.MANUAL_REVIEW,
            fulfilled_requirements=["bank_statement"],
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "bank_statement" not in fulfilled
    assert "bank_statement" in missing


def test_unconfirmed_low_confidence_type_does_not_count_until_officer_confirms() -> None:
    """
    Save fields flips routing to ACCEPTED without confirming the type, so a
    payslip guessed at 0.6 must not tick the checklist until the officer
    confirms it in the type dropdown (which writes 1.0) — the same bar
    approval uses.
    """
    saved_but_unconfirmed = _make_document(
        "doc-1",
        DocumentStatus.ANALYZED,
        routing_status=RoutingStatus.ACCEPTED,
        document_type="payslip",
        classification_confidence=0.6,
        fulfilled_requirements=["payslip"],
    )
    fulfilled, missing = compute_requirement_coverage([saved_but_unconfirmed])
    assert "payslip" not in fulfilled

    confirmed = saved_but_unconfirmed.model_copy(update={"classification_confidence": 1.0})
    fulfilled, missing = compute_requirement_coverage([confirmed])
    assert "payslip" in fulfilled


def test_rejected_document_does_not_count_as_fulfilled() -> None:
    """A loan officer rejecting a document un-ticks its checklist item."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.REJECTED,
            routing_status=RoutingStatus.ACCEPTED,
            fulfilled_requirements=["bank_statement"],
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert "bank_statement" not in fulfilled
    assert "bank_statement" in missing


def test_failed_document_does_not_count_as_fulfilled() -> None:
    """An officer's type pick on a document whose extraction rerun then failed doesn't tick its item."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.FAILED,
            routing_status=RoutingStatus.ACCEPTED,
            document_type="bank_statement",
            fulfilled_requirements=["bank_statement"],
            classification_confidence=1.0,
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
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.ACCEPTED,
            document_type="passport",
            fulfilled_requirements=["id_document"],
            extracted_fields={
                "individual_name": "Jane Tan",
                "individual_address": None,
                # No document_side here — a passport's bio page is always
                # self-contained, so mapper.py hardcodes "single" by
                # document_type rather than reading it from extracted_fields.
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
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.ACCEPTED,
            document_type="nric",
            fulfilled_requirements=["id_document"],
            extracted_fields={"nric_number": "S8905952H", "document_side": "back"},
        ),
        _make_document(
            "doc-2",
            DocumentStatus.ANALYZED,
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.ACCEPTED,
            document_type="nric",
            extracted_fields={"individual_name": "Jane Tan", "individual_address": "1 Main St"},
        ),
        _make_document(
            "doc-2",
            DocumentStatus.ANALYZED,
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.ACCEPTED,
            document_type="nric",
            extracted_fields={"individual_name": "Jane Tan", "individual_address": None},
        ),
        _make_document(
            "doc-2",
            DocumentStatus.ANALYZED,
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.ACCEPTED,
            document_type="nric",
            extracted_fields={"individual_name": "Jane Tan", "individual_address": "1 Main St"},
        ),
        _make_document(
            "doc-2",
            DocumentStatus.ANALYZED,
            routing_status=RoutingStatus.ACCEPTED,
            document_type="nric",
            extracted_fields={"individual_name": "John Lim", "individual_address": None},
        ),
        _make_document(
            "doc-3",
            DocumentStatus.ANALYZED,
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.ACCEPTED,
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
            routing_status=RoutingStatus.ACCEPTED,
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


def test_a_case_with_every_requirement_met_has_nothing_missing() -> None:
    _, missing = compute_requirement_coverage(_make_fully_covered_documents())

    assert missing == []


def test_a_case_missing_a_requirement_lists_it() -> None:
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            routing_status=RoutingStatus.ACCEPTED,
            fulfilled_requirements=["bank_statement"],
        )
    ]

    _, missing = compute_requirement_coverage(documents)

    assert missing and set(missing) <= set(REQUIRED_DOCUMENT_TYPES)


def test_coverage_drops_when_a_document_is_reclassified_away() -> None:
    """A /retry that reclassifies a document away from a requirement nothing else covers leaves it missing again."""
    dropped = _make_fully_covered_documents()
    # doc-0 (the bank statement, not the identity document) is reclassified away: its requirements go empty,
    # leaving the NRIC's own address-based proof_of_address coverage untouched.
    dropped[0] = dropped[0].model_copy(update={"fulfilled_requirements": []})

    _, missing = compute_requirement_coverage(dropped)

    assert missing != []


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


def test_case_is_complete_without_additional_document() -> None:
    """AC2: a case with every required document is complete, additional or not."""
    documents = _make_fully_covered_documents()
    assert not any(doc.document_type in ADDITIONAL_DOCUMENT_TYPES for doc in documents)

    _, missing = compute_requirement_coverage(documents)
    assert missing == []


# What one document fulfils, by type and extracted fields: (document type, fields, requirements it fulfils).
_FULFILMENT = [
    pytest.param("list_of_loans", None, ["list_of_loans"], id="an additional document fulfils its own requirement"),
    # Classification runs before extraction, so with no fields yet each type takes its default reading.
    pytest.param("proof_of_address", None, ["proof_of_address"], id="proof of address before extraction is personal"),
    pytest.param("bank_statement", None, ["bank_statement"], id="a bank statement before extraction is corporate"),
    pytest.param("source_of_wealth", None, ["source_of_wealth"], id="source of wealth before extraction is corporate"),
    pytest.param(
        "proof_of_address", {"individual_name": "Jane Tan", "individual_address": "1 Main St"},
        ["proof_of_address"], id="a personal proof of address fulfils only the personal item",
    ),
    # A company address says nothing about any person's residence.
    pytest.param(
        "proof_of_address", {"company_name": "Acme Pte Ltd", "company_address": "10 Robinson Rd"},
        ["company_proof_of_address"], id="a company proof of address fulfils only the company item",
    ),
    pytest.param(
        "proof_of_address", {"individual_address": "1 Main St", "company_address": "10 Robinson Rd"},
        ["company_proof_of_address", "proof_of_address"], id="proof of address with both addresses fulfils both",
    ),
    # The regression: a personal statement used to also tick the corporate bank_statement item.
    pytest.param(
        "bank_statement", {"individual_name": "Jane Tan", "individual_address": "1 Main St"},
        ["proof_of_address"], id="a personal bank statement fulfils only proof of address",
    ),
    pytest.param(
        "bank_statement", {"individual_name": "Jane Tan"}, [], id="a personal bank statement without an address fulfils nothing",
    ),
    pytest.param(
        "bank_statement", {"company_name": "Acme Pte Ltd", "account_number": "123"},
        ["bank_statement"], id="a corporate bank statement fulfils only the bank statement",
    ),
    # company_name present means a company holds the account, whatever else was extracted.
    pytest.param(
        "bank_statement",
        {"company_name": "Acme Pte Ltd", "individual_name": "Jane Tan", "individual_address": "1 Main St"},
        ["bank_statement"], id="a corporate statement with a stray individual address is not proof of address",
    ),
    # Only a proof_of_address document counts as company proof (a bank statement also carries a company address).
    pytest.param(
        "bank_statement", {"company_address": "10 Robinson Rd"}, ["bank_statement"],
        id="a company address on another document type is not company proof",
    ),
    pytest.param(
        "source_of_wealth", {"company_name": "Golden Harvest Trading Pte Ltd", "uen": "201812345A"},
        ["source_of_wealth"], id="a corporate source of wealth with a UEN fulfils only the corporate item",
    ),
    # The broker's own Pte. Ltd. name must not make a personal statement read as corporate.
    pytest.param(
        "source_of_wealth",
        {
            "account_holder_name": "Nurul Hassan", "broker_name": "Moomoo Financial Singapore Pte. Ltd.",
            "company_name": "Moomoo Financial Singapore Pte. Ltd.",
        },
        ["source_of_personal_wealth"], id="a personal brokerage statement fulfils only the personal item",
    ),
    pytest.param(
        "source_of_wealth", {"account_holder_name": "Nurul Hassan", "individual_address": "16 Eng Kong Terrace"},
        ["source_of_personal_wealth"], id="a personal source of wealth with an address is not proof of address",
    ),
    # A UEN marks it corporate even if a person (for example a director) is named on it.
    pytest.param(
        "source_of_wealth", {"individual_name": "Tan Wei Ming", "uen": "201812345A"},
        ["source_of_wealth"], id="a company statement naming a director stays corporate",
    ),
]


@pytest.mark.parametrize(("document_type", "fields", "expected"), _FULFILMENT)
def test_a_document_fulfils_the_requirements_its_type_and_fields_imply(document_type, fields, expected) -> None:
    assert sorted(compute_fulfilled_requirements(document_type, fields)) == sorted(expected)


def test_uploaded_additional_document_does_not_change_completeness() -> None:
    """AC3/AC4: an additional item shows as fulfilled but neither hides a missing required item nor adds one."""
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            routing_status=RoutingStatus.ACCEPTED,
            document_type="list_of_loans",
            fulfilled_requirements=["list_of_loans"],
        )
    ]
    fulfilled, missing = compute_requirement_coverage(documents)
    assert fulfilled == ["list_of_loans"]
    assert missing == REQUIRED_DOCUMENT_TYPES


def test_only_nric_utility_bill_or_bank_statement_address_fulfils_proof_of_address() -> None:
    fields = {"individual_name": "Jane Tan", "individual_address": "1 Main St"}
    assert compute_fulfilled_requirements("nric", fields) == ["id_document", "proof_of_address"]
    assert compute_fulfilled_requirements("bank_statement", fields) == ["proof_of_address"]
    assert compute_fulfilled_requirements("proof_of_address", fields) == ["proof_of_address"]
    # Company documents that merely list people's addresses aren't proof of address.
    for document_type in ("certificate_of_incumbency", "company_constitution"):
        assert compute_fulfilled_requirements(document_type, fields) == [document_type]


def test_accepted_company_proof_of_address_is_ticked_but_never_missing() -> None:
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            routing_status=RoutingStatus.ACCEPTED,
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


def test_accepted_personal_source_of_wealth_is_ticked_but_never_missing() -> None:
    documents = [
        _make_document(
            "doc-1",
            DocumentStatus.ANALYZED,
            routing_status=RoutingStatus.ACCEPTED,
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
