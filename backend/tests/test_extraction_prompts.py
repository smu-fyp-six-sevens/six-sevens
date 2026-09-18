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


def test_nric_fields():
    prompt = build_extraction_prompt("nric", "sample text")
    assert "nric_number" in prompt
    assert "date_of_birth" in prompt
    assert "nationality" in prompt
    assert "individual_address" in prompt


def test_passport_fields():
    prompt = build_extraction_prompt("passport", "sample text")
    assert "passport_number" in prompt
    assert "nric_number" in prompt
    assert "date_of_birth" in prompt
    assert "nationality" in prompt


def test_nric_document_side_guidance_uses_nric_number_not_id_number():
    # Regression: EXTRA_FIELD_GUIDANCE used to be keyed "id_document", which
    # never matched the real "nric"/"passport" document_type values, so
    # this guidance silently never made it into any real prompt.
    prompt = build_extraction_prompt("nric", "sample text")
    assert "document_side" in prompt
    assert '"front"' in prompt
    assert '"back"' in prompt
    assert "nric_number" in prompt
    assert "id_number" not in prompt


def test_passport_document_side_guidance_defaults_to_single():
    prompt = build_extraction_prompt("passport", "sample text")
    assert "document_side" in prompt
    assert '"single"' in prompt


def test_source_of_wealth_covers_personal_brokerage_statements_too():
    """
    A brokerage/investment account statement (e.g. Moomoo, a margin
    account) is a legitimate source_of_wealth document for an individual,
    not just a company financial statement — the prompt needs to ask for
    account-holder fields and explicitly not treat a missing company_name/
    uen as evidence of misclassification for this variant.
    """
    prompt = build_extraction_prompt("source_of_wealth", "sample text")
    assert "broker_name" in prompt
    assert "account_number" in prompt
    assert "portfolio_value" in prompt
    assert "holdings" in prompt
    assert "do not escalate just because company_name/uen" in prompt


def test_source_of_wealth_asks_for_individual_address():
    # Bug fix: without this, a personal brokerage statement's address had
    # nowhere "official" to go, so the model invented its own ad-hoc field
    # name instead of the canonical individual_address the identity-
    # matching logic (mapper.py) actually looks for.
    prompt = build_extraction_prompt("source_of_wealth", "sample text")
    assert "individual_address" in prompt


def test_company_constitution_asks_for_subscribers_array_not_flat_fields():
    """
    Bug fix: a constitution's holders list is often more than one person —
    the old flat individual_name/individual_address/id_number fields forced
    the model to squash multiple subscribers into one joined string, which
    then couldn't be matched back to anyone's own id_document identity.
    """
    prompt = build_extraction_prompt("company_constitution", "sample text")
    assert "subscribers" in prompt
    assert "SEPARATE entry" in prompt
    assert "- individual_name" not in prompt
    assert "- individual_address" not in prompt


def test_unrecognised_document_type_builds_general_prompt():
    prompt = build_extraction_prompt("unrecognised", "sample text")
    for field in schema_field_names("unrecognised"):
        assert field in prompt
    # There's no expected type here to escalate against, so the general
    # prompt shouldn't ask the model to judge a mismatch — just extract.
    assert "does not actually look like" not in prompt.lower()