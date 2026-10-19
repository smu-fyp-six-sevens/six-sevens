import pytest
from app.langgraph.extraction_prompts import build_extraction_prompt, schema_field_names, EXTRACTION_SCHEMAS


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


# What each type's prompt must (and must not) say: (document type, strings it contains, strings it lacks).
_PROMPT_CONTENT = [
    pytest.param("bank_statement", ["account_number", "bank_name", "transactions"], [], id="bank statement fields"),
    pytest.param(
        "bank_statement", ["entity_name", "entity_address", "entity_type", '"corporate"', '"individual"'], [],
        id="a bank statement distinguishes a personal from a corporate holder",
    ),
    pytest.param(
        "bank_statement", ["array of objects", '"amount"', '"balance"'], [], id="bank statement transaction structure",
    ),
    # supporting_documents names the supplier or customer in its requests from this.
    pytest.param("bank_statement", ['"counterparty"'], [], id="bank statement transactions name a counterparty"),
    # nationality and date_of_expiry were dropped: the generated card renders neither, so they were always null.
    pytest.param(
        "nric", ["nric_number", "date_of_birth", "country", "individual_address", "date_of_issue"],
        ["nationality", "date_of_expiry"], id="nric fields",
    ),
    pytest.param(
        "nric",
        ['"front": individual_name, date_of_birth, and country', '"back": individual_address and date_of_issue'],
        [], id="nric guidance depends on the document side",
    ),
    # Regression: this guidance was once keyed "id_document", which never matched a real type.
    pytest.param(
        "nric", ["document_side", '"front"', '"back"', "nric_number"], ["id_number"],
        id="nric side guidance uses nric_number",
    ),
    pytest.param(
        "passport", ["passport_number", "nric_number", "date_of_birth", "nationality"], [], id="passport fields",
    ),
    # A passport bio page is self-contained, never a front/back pair; the mapper treats it as "single".
    pytest.param("passport", [], ["document_side"], id="a passport has no document side"),
    pytest.param(
        "credit_report",
        ['"bankruptcy_number"', '"order_date"', '"petition_date"', '"original_order_date"', '"gazette_date"',
         "An empty array is correct if the section shows no rows"],
        [], id="credit report bankruptcy guidance allows an empty array",
    ),
    # A brokerage account statement is a valid source of wealth for an individual, so a missing UEN isn't a misclassification.
    pytest.param(
        "source_of_wealth",
        ["broker_name", "account_number", "portfolio_value", "holdings",
         "do not escalate just because company_name/uen"],
        [], id="source of wealth covers personal brokerage statements",
    ),
    # Without it a personal statement's address had no canonical field and the model invented one.
    pytest.param("source_of_wealth", ["individual_address"], [], id="source of wealth asks for an individual address"),
    pytest.param(
        "certificate_of_incumbency", ["ACRA Business Profile", "BizFile", "Do NOT escalate"], [],
        id="an ACRA business profile counts as an incumbency",
    ),
    # A constitution lists several subscribers; flat fields forced them into one joined string.
    pytest.param(
        "company_constitution", ["subscribers", "SEPARATE entry"], ["- individual_name", "- individual_address"],
        id="a constitution asks for a subscribers array",
    ),
]


@pytest.mark.parametrize(("document_type", "present", "absent"), _PROMPT_CONTENT)
def test_a_typed_prompt_asks_for_what_its_type_needs(document_type, present, absent):
    prompt = build_extraction_prompt(document_type, "sample text")

    for text in present:
        assert text in prompt
    for text in absent:
        assert text not in prompt


def test_typed_prompt_marks_schema_fields_required_and_allows_extras():
    prompt = build_extraction_prompt("payslip", "sample text")
    assert "REQUIRED" in prompt
    assert "OTHER fields" in prompt
    assert "loan application" in prompt.lower()


def test_additional_fields_are_told_to_omit_not_null_when_absent():
    # An absent additional field should never appear on the frontend as a
    # dangling "—" row — the LLM is told to just leave the key out rather
    # than including it with a null value. Whitespace normalized since the
    # prompt's own source line-wraps mid-sentence.
    prompt = " ".join(build_extraction_prompt("payslip", "sample text").split())
    assert "OMIT its key from extracted_fields entirely" in prompt
    assert "do NOT include it with a null value" in prompt
    # Required fields keep the opposite instruction — always present, null
    # only when truly absent.
    assert "must always appear as a key even when absent" in prompt


def test_unrecognised_fields_are_also_told_to_omit_when_absent():
    prompt = " ".join(build_extraction_prompt("unrecognised", "sample text").split())
    assert "OMIT its key from extracted_fields entirely" in prompt


def test_nric_only_nric_number_is_unconditionally_required():
    """
    Everything else (name/dob/country on front, address/date_of_issue on
    back) legitimately depends on document_side — see
    test_nric_document_side_guidance_is_conditional_on_side below for the
    prose that makes that conditional-required behaviour explicit.
    """
    prompt = build_extraction_prompt("nric", "sample text")
    required_section = prompt.split("ADDITIONAL fields")[0]
    assert "- nric_number" in required_section
    assert "- individual_name" not in required_section
    assert "- individual_address" not in required_section


def test_credit_report_requires_default_and_bankruptcy_records():
    # Both sections (Default Records / Bankruptcy Proceedings) are
    # unconditionally rendered in the real report even though their row
    # content is usually empty — grounded in
    # credit_bureau_report_generator.py's build_persona(), which always
    # passes a default_records/bankruptcy_rows list (often just []).
    assert "defaults" in EXTRACTION_SCHEMAS["credit_report"]["required"]
    assert "bankruptcy_proceedings" in EXTRACTION_SCHEMAS["credit_report"]["required"]
    prompt = build_extraction_prompt("credit_report", "sample text")
    assert "REQUIRED" in prompt
    assert "bankruptcy_proceedings" in prompt


def test_unrecognised_document_type_builds_general_prompt():
    prompt = build_extraction_prompt("unrecognised", "sample text")
    for field in schema_field_names("unrecognised"):
        assert field in prompt
    # There's no expected type here to escalate against, so the general
    # prompt shouldn't ask the model to judge a mismatch — just extract.
    assert "does not actually look like" not in prompt.lower()