import httpx
import pytest

from app.classification import classification as classification_module
from app.classification.classification import (
    ClassificationResult,
    DeepSeekClassificationError,
    classify_by_filename,
    classify_by_llm,
    classify_by_text,
    classify_document,
)
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


# --- Layer 2: classify_by_text ---------------------------------------------
# look out for textual content that indicates the document type, independent of the filename



@pytest.mark.parametrize(
    "text,expected_type",
    [
        (
            "This is your BANK STATEMENT for March.\n"
            "Opening balance: 100.00\nClosing balance: 200.00",
            DocumentType.BANK_STATEMENT,
        ),
        (
            "CERTIFICATE OF INCORPORATION\nCompany Registration Number: 12345",
            DocumentType.CERTIFICATE_OF_INCORPORATION,
        ),
        (
            "PAYSLIP\nBasic Salary: 3000\nNet Pay: 2500",
            DocumentType.PAYSLIP,
        ),
        (
            "NRIC No: S1234567A\nDate of Birth: 01/01/1990",
            DocumentType.ID_DOCUMENT,
        ),
        (
            "FATCA / CRS Self-Certification Form\nTax Residency: Singapore",
            DocumentType.FATCA_CRS_DECLARATION,
        ),
        (
            "This Certificate of Incumbency confirms the company is duly "
            "incorporated and validly existing.",
            DocumentType.CERTIFICATE_OF_INCUMBENCY,
        ),
        (
            "MEMORANDUM AND ARTICLES OF ASSOCIATION",
            DocumentType.COMPANY_CONSTITUTION,
        ),
        (
            "Proof of Address\nUtility Bill\nBilling Address: 123 Main St",
            DocumentType.PROOF_OF_ADDRESS,
        ),
        (
            "Source of Wealth Declaration",
            DocumentType.SOURCE_OF_WEALTH,
        ),
        (
            "Credit Report\nCredit Score: 750\nCCRIS",
            DocumentType.CREDIT_REPORT,
        ),
        (
            "Outstanding Loans\nLoan balance: 5000",
            DocumentType.LIST_OF_LOANS,
        ),
    ],
)
def test_classify_by_text_recognises_expected_type(text, expected_type) -> None:
    result = classify_by_text(text)

    assert result.document_type == expected_type
    assert result.needs_manual_review is False
    assert result.confidence >= 0.6


def test_classify_by_text_unrecognised_body() -> None:
    result = classify_by_text("Dear Sir/Madam, please find attached the requested documents.")

    assert result.document_type is None
    assert result.confidence == 0.0
    assert result.needs_manual_review is True


def test_classify_by_text_bare_weak_word_is_not_enough() -> None:
    # Unlike the filename layer (where a bare "statement" is a weak 0.4
    # signal), the text layer requires a stronger phrase match — a single
    # generic word in a full document body isn't meaningful evidence on
    # its own.
    result = classify_by_text("Please refer to the statement enclosed.")

    assert result.document_type is None
    assert result.needs_manual_review is True


# --- classify_document: merging filename + text -----------------------------


async def test_classify_document_without_text_matches_filename_layer_only() -> None:
    result = await classify_document("Bank_Statement.pdf")
    filename_only = classify_by_filename("Bank_Statement.pdf")

    assert result.document_type == filename_only.document_type
    assert result.confidence == filename_only.confidence


async def test_classify_document_ignores_empty_text() -> None:
    result = await classify_document("Bank_Statement.pdf", extracted_text="")
    filename_only = classify_by_filename("Bank_Statement.pdf")

    assert result.document_type == filename_only.document_type
    assert result.confidence == filename_only.confidence


async def test_classify_document_text_boosts_agreeing_filename(monkeypatch) -> None:
    # "statement.pdf" alone is a weak filename signal (0.4); agreeing body
    # text should push the combined confidence up significantly. LLM layer
    # is stubbed to agree too, so this also covers the full three-layer
    # agreement chain.
    async def fake_llm(extracted_text, *, confidence_threshold=None):
        return ClassificationResult(
            document_type=DocumentType.BANK_STATEMENT,
            confidence=0.9,
            matched_keywords=["mentions opening and closing balances"],
        )

    monkeypatch.setattr(classification_module, "classify_by_llm", fake_llm)

    filename_only_confidence = classify_by_filename("statement.pdf").confidence

    result = await classify_document(
        "statement.pdf",
        extracted_text="BANK STATEMENT\nOpening balance: 100.00\nClosing balance: 200.00",
    )

    assert result.document_type == DocumentType.BANK_STATEMENT
    assert filename_only_confidence < result.confidence <= 1.0
    assert result.needs_manual_review is False


async def test_classify_document_text_overrides_wrong_filename_guess(monkeypatch) -> None:
    # filename weakly suggests a bank statement, but the extracted body is
    # unambiguously a payslip — content should win over the filename.
    async def fake_llm(extracted_text, *, confidence_threshold=None):
        return ClassificationResult(document_type=None, confidence=0.0)

    monkeypatch.setattr(classification_module, "classify_by_llm", fake_llm)

    result = await classify_document(
        "statement.pdf",
        extracted_text="PAYSLIP\nBasic Salary: 3000\nNet Pay: 2500\nGross Pay: 3200",
    )

    assert result.document_type == DocumentType.PAYSLIP


async def test_classify_document_unrecognised_filename_and_text_needs_manual_review(monkeypatch) -> None:
    async def fake_llm(extracted_text, *, confidence_threshold=None):
        return ClassificationResult(document_type=None, confidence=0.0)

    monkeypatch.setattr(classification_module, "classify_by_llm", fake_llm)

    result = await classify_document("scan001.pdf", extracted_text="Thank you for your business.")

    assert result.document_type is None
    assert result.needs_manual_review is True


async def test_classify_document_llm_overrides_agreeing_regex_layers(monkeypatch) -> None:
    # Filename + text-regex both weakly/wrongly suggest bank statement, but
    # the LLM confidently reads it as a payslip — the strongest layer wins.
    async def fake_llm(extracted_text, *, confidence_threshold=None):
        return ClassificationResult(
            document_type=DocumentType.PAYSLIP,
            confidence=0.95,
            matched_keywords=["describes monthly salary breakdown"],
        )

    monkeypatch.setattr(classification_module, "classify_by_llm", fake_llm)

    result = await classify_document(
        "statement.pdf",
        extracted_text="This is your statement of earnings for the month.",
    )

    assert result.document_type == DocumentType.PAYSLIP
    assert "describes monthly salary breakdown" in result.matched_keywords


async def test_classify_document_falls_back_when_llm_layer_unavailable(monkeypatch) -> None:
    # If the LLM call fails outright, classification should degrade to the
    # filename+text-regex result rather than failing the whole document.
    async def failing_llm(extracted_text, *, confidence_threshold=None):
        raise DeepSeekClassificationError("simulated outage")

    monkeypatch.setattr(classification_module, "classify_by_llm", failing_llm)

    text_and_filename_only = await classify_document(
        "statement.pdf",
        extracted_text="BANK STATEMENT\nOpening balance: 100.00\nClosing balance: 200.00",
    )
    text_result = classify_by_text(
        "BANK STATEMENT\nOpening balance: 100.00\nClosing balance: 200.00"
    )

    assert text_and_filename_only.document_type == text_result.document_type
    assert text_and_filename_only.document_type == DocumentType.BANK_STATEMENT


# --- Layer 3: classify_by_llm (using DeepSeek right now) ------------------------------------


def _fake_deepseek_response(content: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "https://api.deepseek.com/chat/completions"),
    )


async def test_classify_by_llm_parses_valid_response(monkeypatch) -> None:
    async def fake_post(self, url, *, headers=None, json=None):
        return _fake_deepseek_response(
            '{"document_type": "payslip", "confidence": 0.88, "evidence": ["mentions net pay"]}'
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await classify_by_llm("PAYSLIP\nNet Pay: 2500")

    assert result.document_type == DocumentType.PAYSLIP
    assert result.confidence == 0.88
    assert result.matched_keywords == ["mentions net pay"]
    assert result.needs_manual_review is False


async def test_classify_by_llm_handles_no_match(monkeypatch) -> None:
    async def fake_post(self, url, *, headers=None, json=None):
        return _fake_deepseek_response('{"document_type": null, "confidence": 0.0, "evidence": []}')

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    result = await classify_by_llm("Dear Sir/Madam, please find attached.")

    assert result.document_type is None
    assert result.confidence == 0.0
    assert result.needs_manual_review is True


async def test_classify_by_llm_raises_on_malformed_response(monkeypatch) -> None:
    async def fake_post(self, url, *, headers=None, json=None):
        return _fake_deepseek_response("not valid json")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(DeepSeekClassificationError):
        await classify_by_llm("some document text")


async def test_classify_by_llm_raises_on_request_failure(monkeypatch) -> None:
    async def fake_post(self, url, *, headers=None, json=None):
        raise httpx.ConnectError("connection refused", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    with pytest.raises(DeepSeekClassificationError):
        await classify_by_llm("some document text")
