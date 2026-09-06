"""
Per-category extraction prompts and expected field lists for the
Extraction stage. Kept in Python (not Dify) so field lists, prompt
wording, and escalation logic are testable and version-controlled.
"""

EXTRACTION_SCHEMAS: dict[str, list[str]] = {
    "bank_statement": [
        "company_name", "bank_name", "account_number",
    ],
    "certificate_of_incorporation": [
        "company_name", "uen", "incorporation_date",
        "company_type",
    ],
    "payslip": [
        "individual_name", "company_name","net_pay",
    ],
    "id_document": [
        "individual_name", "id_number", "date_of_birth",
        "nationality", "individual_address",
    ],
    "fatca_crs_declaration": [
        "company_name", "company_address", "tin",
        "declaration_date",
    ],
    "certificate_of_incumbency": [
        "company_name", "uen", "incorporation_date", "company_type",
        "company_address", "individual_name", "individual_address", "id_number"
    ],
    "company_constitution": [
        "company_name", "uen", "company_type", "incorporation_date", "company_address",
        "individual_name", "individual_address", "id_number"
    ],
    "proof_of_address": [
        "individual_name", "individual_address",
    ],
    "source_of_wealth": [
        "company_name", "uen",
    ],
    "credit_report": [
        "individual_name", "id_number", "date_of_birth",
        "nationality", "individual_address",
    ],
    "list_of_loans": [
        "company_name", "company_address", "individual_name", "loan_amount" # prob change in the future to others like {lender, amount, currency, status}
    ],
}


def build_extraction_prompt(document_type: str, raw_text: str) -> str:
    fields = EXTRACTION_SCHEMAS.get(document_type)
    if fields is None:
        raise ValueError(f"No extraction schema defined for document_type: {document_type}")

    field_list = "\n".join(f"- {f}" for f in fields)

    return f"""You are extracting structured data from a {document_type.replace('_', ' ')} for a KYC/onboarding pipeline.

Given the document text below, extract these fields. If a field is not present, use null.

Fields:
{field_list}

If the text does NOT actually look like a {document_type.replace('_', ' ')} (e.g. it's clearly a
different document type), do not force-fit these fields. Instead return an
escalation explaining what it actually looks like.

Document text:
{raw_text}

Respond with ONLY a JSON object, no other text, of this shape:
{{"extracted_fields": {{...}}, "confidence": <float 0.0-1.0>, "escalation": null}}

OR, if the document doesn't match:
{{"extracted_fields": {{}}, "confidence": 0.0, "escalation": {{"reason": "<short explanation>"}}}}
"""