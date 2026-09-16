import pytest
from app.extraction_prompts import build_extraction_prompt, schema_field_names, EXTRACTION_SCHEMAS


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
    for field in schema_field_names(document_type):
        assert field in prompt


def test_bank_statement_fields():
    prompt = build_extraction_prompt("bank_statement", "sample text")
    assert "account_number" in prompt
    assert "bank_name" in prompt
    assert "transactions" in prompt


def test_bank_statement_includes_transaction_structure_guidance():
    prompt = build_extraction_prompt("bank_statement", "sample text")
    assert "array of objects" in prompt
    assert '"amount"' in prompt
    assert '"balance"' in prompt


def test_typed_prompt_marks_schema_fields_required_and_allows_extras():
    prompt = build_extraction_prompt("payslip", "sample text")
    assert "REQUIRED" in prompt
    assert "OTHER fields" in prompt
    assert "loan application" in prompt.lower()


def test_id_document_fields():
    prompt = build_extraction_prompt("id_document", "sample text")
    assert "id_number" in prompt
    assert "date_of_birth" in prompt
    assert "nationality" in prompt


def test_unrecognised_document_type_builds_general_prompt():
    prompt = build_extraction_prompt("unrecognised", "sample text")
    for field in schema_field_names("unrecognised"):
        assert field in prompt
    # There's no expected type here to escalate against, so the general
    # prompt shouldn't ask the model to judge a mismatch — just extract.
    assert "does not actually look like" not in prompt.lower()