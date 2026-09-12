"""
Per-category extraction prompts and expected field lists for the
Extraction stage. Kept in Python (not Dify) so field lists, prompt
wording, and escalation logic are testable and version-controlled.
"""

EXTRACTION_SCHEMAS: dict[str, list[str]] = {
    "bank_statement": [
        "company_name", "bank_name", "account_number", "transactions",
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
        "individual_name", "individual_address", "company_name", "company_address",
    ],
    "source_of_wealth": [
        "company_name", "uen", "individual_name",
    ],
    "credit_report": [
        "individual_name", "id_number", "date_of_birth",
        "nationality", "individual_address",
    ],
    "list_of_loans": [
        "company_name", "company_address", "individual_name", "loan_amount" # prob change in the future to others like {lender, amount, currency, status}
    ],
    # Fallback field list for documents the Classifier couldn't match to any
    # category above (document_type == "unrecognised"). Extraction still runs
    # against these rather than being skipped, so a reviewer has *something*
    # pulled out instead of nothing — see build_extraction_prompt.
    "unrecognised": [
        "document_title", "company_name", "individual_name", "id_number",
        "account_number", "amount", "document_date", "address",
    ],
}


# Extra, field-specific instructions appended after the required-field list
# for document types where a plain "extract this field" instruction isn't
# enough to get well-structured output — currently just bank statements'
# transaction history, which needs to come back as a list of records rather
# than a single scalar value.
EXTRA_FIELD_GUIDANCE: dict[str, str] = {
    "bank_statement": (
        'For "transactions", extract an array of objects — one per transaction line '
        'visible in the statement — each with these keys: "date" (as shown in the '
        'document), "description", "amount" (numeric; negative for debits/'
        'withdrawals, positive for credits/deposits), and "balance" (the running '
        "balance after that transaction, if shown, else null). Include every "
        "transaction you can find in the text — do not summarise, truncate, or "
        "only include a sample."
    ),
}


def build_extraction_prompt(document_type: str, raw_text: str) -> str:
    fields = EXTRACTION_SCHEMAS.get(document_type)
    if fields is None:
        raise ValueError(f"No extraction schema defined for document_type: {document_type}")

    field_list = "\n".join(f"- {f}" for f in fields)
    extra_guidance = EXTRA_FIELD_GUIDANCE.get(document_type, "")
    # Precomputed (not inlined in the f-string below) since a backslash can't
    # appear inside an f-string expression on Python <3.12.
    extra_guidance_block = f"\n{extra_guidance}\n" if extra_guidance else ""

    if document_type == "unrecognised":
        return f"""This document could not be matched to any known KYC/onboarding category, so
there is no fixed template for it. It will still be routed for manual
review — your job here is only to pull out whatever of the following
general fields actually appear in the text below, plus any other fields
you notice that seem relevant for a loan application. These are fields
that tend to show up across loan-application documents generally, not
specific to any one type.

Fields to check for:
{field_list}

Use null for any of the fields above that isn't present — do not guess or
invent values. You may also add any other fields you find relevant,
using clear, descriptive snake_case keys.

Document text:
{raw_text}

Respond with ONLY a JSON object, no other text, of this shape:
{{"extracted_fields": {{...}}, "confidence": <float 0.0-1.0>, "escalation": null}}
"""

    return f"""You are extracting structured data from a {document_type.replace('_', ' ')} for a KYC/onboarding pipeline.

Given the document text below, extract these REQUIRED fields. Every one of
them must appear as a key in extracted_fields — use null for any that
aren't present in the text, rather than omitting the key.

Required fields:
{field_list}
{extra_guidance_block}
You may also include any OTHER fields you find in the document that seem
relevant for a loan application (e.g. additional identifiers, dates,
amounts, addresses) beyond the required list above — there's no fixed list
for these, use your judgement about what a loan officer reviewing this
document would find useful. Add them directly into extracted_fields using
clear, descriptive snake_case keys, alongside the required fields.

If the text does NOT actually look like a {document_type.replace('_', ' ')} (e.g. it's clearly a
different document type), do not force-fit these fields. Instead return an
escalation whose "reason" describes what the document actually appears to
be — its title/heading, who it's from or addressed to, and any dates or
reference numbers visible — so the classifier can be re-run with that
description in mind rather than just being told "it doesn't match".

Document text:
{raw_text}

Respond with ONLY a JSON object, no other text, of this shape:
{{"extracted_fields": {{...}}, "confidence": <float 0.0-1.0>, "escalation": null}}

OR, if the document doesn't match:
{{"extracted_fields": {{}}, "confidence": 0.0, "escalation": {{"reason": "<what the document actually looks like>"}}}}
"""