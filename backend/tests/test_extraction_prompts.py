import pytest
from app.extraction_prompts import build_extraction_prompt, EXTRACTION_SCHEMAS


def test_known_document_type_builds_prompt():
    prompt = build_extraction_prompt("payslip", "Employee: John Doe\nGross pay: $5000")
    assert "individual_name" in prompt
    assert "John Doe" in prompt
    assert "escalation" in prompt.lower()


def test_unknown_document_type_raises():
    with pytest.raises(ValueError, match="No extraction schema"):
        build_extraction_prompt("not_a_real_type", "some text")


@pytest.mark.parametrize("document_type", list(EXTRACTION_SCHEMAS.keys()))
def test_every_category_builds_a_prompt(document_type):
    prompt = build_extraction_prompt(document_type, "sample document text")
    assert len(prompt) > 0
    for field in EXTRACTION_SCHEMAS[document_type]:
        assert field in prompt


def test_bank_statement_fields():
    prompt = build_extraction_prompt("bank_statement", "sample text")
    assert "account_number" in prompt
    assert "bank_name" in prompt


def test_id_document_fields():
    prompt = build_extraction_prompt("id_document", "sample text")
    assert "id_number" in prompt
    assert "date_of_birth" in prompt
    assert "nationality" in prompt