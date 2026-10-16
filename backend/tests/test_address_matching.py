from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.address_matching import find_address_mismatches, normalize_address
from app.models import DocumentRecord, DocumentStatus


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("Blk 22 Bedok North", "22 Bedok North"),
        ("Bedok Sth Ave 1", "Bedok South Avenue 1"),
        ("Tampines Nth UPP CTRL", "Tampines North Upper Central"),
        ("S460022", "Singapore 460022"),
        ("Blk 22 C'wealth Ave", "22 Commonwealth Avenue"),
    ],
)
def test_address_variants_normalize_to_the_same_value(first: str, second: str) -> None:
    assert normalize_address(first) == normalize_address(second)


def _document(doc_id: str, fields: dict, document_type: str = "proof_of_address") -> DocumentRecord:
    return DocumentRecord(
        id=doc_id,
        case_id="case-1",
        filename=f"{doc_id}.pdf",
        document_type=document_type,
        status=DocumentStatus.ANALYZED,
        uploaded_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        extracted_fields=fields,
    )


def test_same_person_addresses_that_differ_only_by_abbreviation_match() -> None:
    first = _document("first", {
        "individual_name": "Jia Tan",
        "individual_address": "Blk 22 Bedok Sth Ave 1, Singapore 460022",
    })
    second = _document("second", {
        "individual_name": "Jia Tan",
        "individual_address": "22 Bedok South Avenue 1, S460022",
    })

    assert find_address_mismatches([first, second]) == []


def test_different_postal_codes_for_same_person_are_a_mismatch() -> None:
    first = _document("first", {
        "individual_name": "Jia Tan",
        "individual_address": "22 Bedok North, Singapore 460022",
    })
    second = _document("second", {
        "individual_name": "Jia Tan",
        "individual_address": "22 Bedok North, Singapore 460023",
    })

    mismatches = find_address_mismatches([first, second])

    assert len(mismatches) == 1
    assert "460022" in mismatches[0].reason
    assert "460023" in mismatches[0].reason


def test_company_addresses_without_company_identity_are_not_compared() -> None:
    purchase_order = _document("purchase-order", {
        "company_address": "10 Bedok North, Singapore 460022",
    }, document_type="unrecognised")
    company_statement = _document("statement", {
        "company_name": "Acme Pte Ltd",
        "uen": "201912345K",
        "company_address": "20 Bedok North, Singapore 460023",
    }, document_type="bank_statement")

    assert find_address_mismatches([purchase_order, company_statement]) == []


def test_company_addresses_with_same_identity_are_compared() -> None:
    first = _document("first", {
        "company_name": "Acme Pte Ltd",
        "company_address": "10 Bedok North, Singapore 460022",
    }, document_type="company_constitution")
    second = _document("second", {
        "company_name": "Acme Pte Ltd",
        "company_address": "10 Bedok North, Singapore 460023",
    }, document_type="bank_statement")

    assert len(find_address_mismatches([first, second])) == 1


def test_a_document_with_a_uen_and_one_with_only_the_company_name_are_compared() -> None:
    # A corporate bank statement prints the account holder's name but not its UEN.
    incumbency = _document("incumbency", {
        "company_name": "HARBOURLINE PRECISION ENGINEERING PTE. LTD.",
        "uen": "201914237K",
        "company_address": "8 Tuas Avenue 10, #01-05, Singapore 639137",
    }, document_type="certificate_of_incumbency")
    statement = _document("statement", {
        "company_name": "Harbourline Precision Engineering Pte Ltd",
        "company_address": "13 Tuas Avenue 10, Singapore 639137",
    }, document_type="bank_statement")

    assert len(find_address_mismatches([incumbency, statement])) == 1


def test_two_different_uens_are_two_companies_even_with_the_same_name() -> None:
    first = _document("first", {
        "company_name": "Acme Pte Ltd", "uen": "201912345K",
        "company_address": "10 Bedok North, Singapore 460022",
    }, document_type="certificate_of_incumbency")
    second = _document("second", {
        "company_name": "Acme Pte Ltd", "uen": "201998765M",
        "company_address": "20 Bedok North, Singapore 460023",
    }, document_type="notice_of_assessment")

    assert find_address_mismatches([first, second]) == []


def test_a_uen_document_and_a_differently_named_one_are_not_compared() -> None:
    incumbency = _document("incumbency", {
        "company_name": "Acme Pte Ltd", "uen": "201912345K",
        "company_address": "10 Bedok North, Singapore 460022",
    }, document_type="certificate_of_incumbency")
    supplier_statement = _document("statement", {
        "company_name": "Other Trading Pte Ltd",
        "company_address": "20 Bedok North, Singapore 460023",
    }, document_type="bank_statement")

    assert find_address_mismatches([incumbency, supplier_statement]) == []
