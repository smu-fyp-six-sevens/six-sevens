import pytest

from app.classification.classification import classify_by_filename
from app.models import DocumentType


@pytest.mark.parametrize(
    "filename,expected_type",
    [
        ("Bank_Statement.pdf", DocumentType.BANK_STATEMENT),
        ("Certificate of Incorporation.pdf", DocumentType.CERTIFICATE_OF_INCORPORATION),
        ("payslip_dec2025.pdf", DocumentType.PAYSLIP),
        ("Director_NRIC.jpg", DocumentType.ID_DOCUMENT),
        ("passport_shareholder.png", DocumentType.ID_DOCUMENT),
        ("FATCA-CRS Self Declaration.pdf", DocumentType.FATCA_CRS_DECLARATION),
        ("Certificate of Incumbency.pdf", DocumentType.CERTIFICATE_OF_INCUMBENCY),
        ("Company_Constitution.pdf", DocumentType.COMPANY_CONSTITUTION),
        ("Proof of Address - Utility Bill.pdf", DocumentType.PROOF_OF_ADDRESS),
        ("Source_of_Wealth_Supporting_Docs.pdf", DocumentType.SOURCE_OF_WEALTH),
        ("Credit_Report.pdf", DocumentType.CREDIT_REPORT),
        ("List of Loans.pdf", DocumentType.LIST_OF_LOANS),
        ("Bank_Statement_Sample.png", DocumentType.BANK_STATEMENT),
        ("crs-e_entities_self_cert_form_sgh (1).pdf", DocumentType.FATCA_CRS_DECLARATION),
        ("NRIC-sample.png", DocumentType.ID_DOCUMENT),
        ("passport sample.jpg", DocumentType.ID_DOCUMENT),
    ],
)
def test_classify_by_filename_recognises_expected_type(filename, expected_type) -> None:
    result = classify_by_filename(filename)

    assert result.document_type == expected_type
    assert result.needs_manual_review is False
    assert result.confidence >= 0.6


def test_classify_by_filename_unrecognised_scan() -> None:
    result = classify_by_filename("scan_20260101.pdf")

    assert result.document_type is None
    assert result.confidence == 0.0
    assert result.needs_manual_review is True


@pytest.mark.parametrize(
    "filename",
    [
        "IMG_1234.jpg",
        "Untitled.pdf",
        "document.pdf",
        "New Document.pdf",
        "Scan001.pdf",
        "asdkjhaskjd.png",
        "1234567890.pdf",
        "file (1).pdf",
        "download.pdf",
        "Copy of Copy of file.pdf",
        "temp.pdf",
    ],
)
def test_classify_by_filename_nonsensical_names_are_uncategorised(filename) -> None:
    # None of these carry any recognisable document-type signal, so the
    # classifier should not guess — it should come back empty and flag
    # for manual review rather than force a match.
    result = classify_by_filename(filename)

    assert result.document_type is None
    assert result.confidence == 0.0
    assert result.matched_keywords == []
    assert result.needs_manual_review is True


def test_classify_by_filename_low_confidence_still_flagged_for_review() -> None:
    # "statement" alone is a weak signal (0.4) — below the default 0.6
    # threshold — so it should surface as a guess but still need review.
    result = classify_by_filename("statement.pdf")

    assert result.document_type == DocumentType.BANK_STATEMENT
    assert result.confidence < 0.6
    assert result.needs_manual_review is True


def test_classify_by_filename_bare_loan_is_low_confidence() -> None:
    # "loan" alone is a weak signal (0.4) — below the default 0.6
    # threshold — so it should surface as a guess but still need review.
    result = classify_by_filename("loan.jpg")

    assert result.document_type == DocumentType.LIST_OF_LOANS
    assert result.confidence < 0.6
    assert result.needs_manual_review is True


def test_classify_by_filename_respects_custom_threshold() -> None:
    result = classify_by_filename("statement.pdf", confidence_threshold=0.3)

    assert result.needs_manual_review is False
