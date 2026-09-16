"""
Per-category extraction prompts and expected field lists for the
Extraction stage. Kept in Python (not Dify) so field lists, prompt
wording, and escalation logic are testable and version-controlled.

Each document type's schema is split into three tiers rather than one flat
list, since "expected but missing" means different things depending on
which tier a field belongs to:

- required: must be present for this to genuinely be the classified type
  (e.g. id_document needs individual_name + id_number). Missing one is real
  evidence the classification might be wrong.
- additional: known, named fields for this type that legitimately vary by
  subtype (e.g. individual_address — present on an NRIC, absent on a
  passport). Missing is normal here, not a signal of anything.
- optional: not a fixed list at all — whatever else the model finds and
  judges relevant (e.g. an MRZ line). Same freeform bucket every document
  type has always had, just named for clarity instead of being an implicit
  afterthought.
"""

EXTRACTION_SCHEMAS: dict[str, dict[str, list[str]]] = {
    # Tiers below are derived from doc_gen_scripts, not guessed: "required"
    # is whatever every generator/template for that type sets unconditionally
    # (a required kwarg with no fallback, or unconditionally-set dict key);
    # "additional" is whatever only some templates/subtypes set, or that sits
    # behind an `if` branch that can legitimately be empty.
    "bank_statement": {
        # docgen_bankstatement.py's load_case() raises if bank_name can't be
        # resolved via BANK_PROFILES; company_name/account_no are also set
        # unconditionally there, and transactions are always generated —
        # true across the DBS/UOB and OCBC templates alike.
        "required": ["company_name", "bank_name", "account_number", "transactions"],
        "additional": [],
    },
    "certificate_of_incorporation": {
        # generate_from_mastersheet.py actually calls coi_generator.py, not
        # certificate_of_incorporation_generator.py (dead code, never
        # imported) — company_status ("Limited Exempt Private Company") is
        # hardcoded in coi_generator.py's DEFAULT_CONTEXT and never
        # overridden by the caller, so it's always present. former_names is
        # genuinely probabilistic there (rng.random() >= 0.4 -> no former
        # name ~60% of the time), a real subtype-dependent field.
        "required": ["company_name", "uen", "incorporation_date", "company_type"],
        "additional": ["company_former_name"],
    },
    "payslip": {
        # employee_name/employer_name/net figures are all rendered by every
        # one of build_payslip_1/2/3 in generate_payslips.py. company_address
        # (the employer's address, always populated via the mastersheet's
        # Employer Address or a company_facts() fallback) is only actually
        # rendered by build_payslip_1/3, not build_payslip_2.
        "required": ["individual_name", "company_name", "net_pay"],
        "additional": ["company_address"],
    },
    "id_document": {
        # passport_generator.py's own comment: "unlike nric_generator.py,
        # this page carries no address field" — date_of_birth/nationality
        # are rendered on both NRIC and passport, only address is subtype-
        # dependent.
        "required": ["individual_name", "id_number", "date_of_birth", "nationality"],
        "additional": ["individual_address"],
    },
    "fatca_crs_declaration": {
        # docgen_fatca_crs.py.build_personas hardcodes more than the
        # branching in _gldb_values suggests: country_of_incorporation is
        # always "Singapore", tax_residences always has one entry (country=
        # Singapore, tin=facts["uen"]), and us_tin is always "" (falsy) — so
        # the "US TIN vs per-country TIN vs no-TIN-reason" branch in
        # _gldb_values never actually takes the "no TIN" path in practice;
        # TIN is always populated. Likewise `director` falls back to
        # holders[0] when no one has the "director" role, and holders is
        # never empty for a real company — so signatories/declaration_date
        # are effectively always present too, not genuinely conditional.
        "required": [
            "company_name", "company_address", "tin", "declaration_date",
            "country_of_incorporation",
        ],
        "additional": [],
    },
    "certificate_of_incumbency": {
        # docgen_acra_profile.py sets every one of these unconditionally —
        # company_type is always randomly assigned (never left out), a
        # Secretary (name/address/id) is always appended to officers even
        # when there are no director holders, and share_capital/share_type
        # (facts["share_class"], e.g. "Ordinary") both come straight from
        # facts with no fallback.
        "required": [
            "company_name", "uen", "incorporation_date", "company_type",
            "company_address", "individual_name", "individual_address",
            "id_number", "share_capital", "share_class",
        ],
        "additional": [],
    },
    "company_constitution": {
        # docgen_constitution.py's subscribers list (facts["holders"]) is
        # always non-empty, and registered_address/incorporation_date/
        # share_capital are all set unconditionally; issued_shares (the sum
        # of every subscriber's shares) is likewise always computed. Unlike
        # certificate_of_incumbency, this generator never actually reads
        # facts["share_class"] — its legal clauses just say generic "ordinary
        # shares" as fixed boilerplate — so no per-case share_class field
        # exists here to extract. "company_type" isn't a per-case field
        # either — the cover page hardcodes the static string "PRIVATE
        # COMPANY LIMITED BY SHARES" rather than reading a variable — kept as
        # additional rather than treated as a real extracted fact.
        # "notes" (additional): the actual legal terms/clauses making up the
        # bulk of the document (share transfer restrictions, director
        # powers, meeting procedures, etc.) — capturing a summary avoids
        # extraction reducing a whole constitution down to just its
        # identity/share fields and discarding everything else.
        "required": [
            "company_name", "uen", "incorporation_date", "company_address",
            "individual_name", "individual_address", "id_number",
            "share_capital", "issued_shares",
        ],
        "additional": ["company_type", "notes"],
    },
    # Serves two subtypes (personal vs. company proof of address) that reuse
    # the *same* generator field slot for either an individual or a company
    # (see personal_proof_of_residence/utility_bill_generator.py vs.
    # optional_documents/utility/generate_bills.py) — never both on one
    # document. Neither pair is unconditionally required over the other, so
    # both live in additional. (Known limitation: a flat required/additional
    # model can't express "at least one of these two pairs must be present.")
    "proof_of_address": {
        "required": [],
        "additional": ["individual_name", "individual_address", "company_name", "company_address"],
    },
    "source_of_wealth": {
        # corp_source_of_wealth/balance_sheet_income_statement_generator.py
        # is a pure company financial statement — company_name/uen are set
        # unconditionally, but individual_name never appears in it at all.
        # The rest (additional): this generator has real line-item structure
        # (revenue, other income, staff costs, PP&E, finance costs, tax
        # expense, intangibles, inventories, trade receivables, cash, bank
        # borrowings, share capital), each with an explanatory NOTE_DEFS
        # note — summarising the headline figures + notes avoids reducing a
        # whole financial statement down to just company_name/uen.
        "required": ["company_name", "uen"],
        "additional": [
            "individual_name", "revenue", "net_profit", "total_assets",
            "total_liabilities", "share_capital", "notes",
        ],
    },
    "credit_report": {
        # credit_bureau_report_generator.py: all five identity fields are
        # unconditional (address falls back to the case's registered company
        # address rather than ever being left blank). accounts/defaults
        # (additional): real content that's always rendered, BUT build_
        # persona never actually supplies case-specific values for these —
        # they fall back to fixed SAMPLE_ACCOUNTS/SAMPLE_DEFAULT_RECORDS, so
        # today every generated report has identical boilerplate here, not
        # case-varying. Still worth extracting (a real forged report could
        # show inconsistent figures), just flagging it isn't a discriminating
        # signal against the current synthetic dataset.
        "required": ["individual_name", "id_number", "date_of_birth", "nationality", "individual_address"],
        "additional": ["accounts", "defaults"],
    },
    "list_of_loans": {
        # All three loan-letter templates (efs/overdraft/term_loan_facility_
        # generator.py) require company_name, company_address_lines,
        # company_uen, director_name, and an amount field with no defaults —
        # uen was previously missing from this schema despite always being
        # rendered. interest_rate/loan_tenure (additional): a real concept in
        # all three templates, but phrased too differently to treat as
        # required — overdraft has no flat interest_rate_pa (states
        # reference rate + margin instead) and uses a review date rather
        # than a fixed tenure.
        "required": ["company_name", "company_address", "individual_name", "loan_amount", "uen"],
        # loan_terms (additional): the facility letter templates each render a
        # "Facility Details"-style table with more rows than just interest_rate/
        # loan_tenure — term_loan_facility_generator.py alone unconditionally
        # renders Purpose, Tenure, Repayment, Interest Rate, and Security rows,
        # plus prose covenants (prepayment fee schedule, security perfection).
        # Rather than growing this schema's flat field list every time a
        # template adds another named term, loan_terms is a catch-all array
        # for whatever named contractual terms are actually present.
        "additional": ["interest_rate", "loan_tenure", "loan_terms"],
        # prob change in the future to others like {lender, amount, currency, status}
    },
    "notice_of_assessment": {
        # generate_noa.py's generate_noa(): company_name, company_address,
        # tax_ref (the mastersheet's "Tax Reference Number (UEN)"),
        # year_of_assessment, noa_date, eci (Estimated Chargeable Income) and
        # the computed tax_payable are all required kwargs / unconditionally
        # rendered in the tax computation table. Company-only document — no
        # individual field. tax_computation (additional): compute_noa_tax()
        # unconditionally renders the full step-by-step breakdown (exempt
        # amounts, tax at 17%, rebate) between eci and tax_payable — worth
        # capturing as its own structured field rather than only the two
        # headline figures.
        "required": [
            "company_name", "uen", "company_address", "year_of_assessment",
            "noa_date", "eci", "tax_payable",
        ],
        "additional": ["tax_computation"],
    },
    # Fallback for documents the Classifier couldn't match to any category
    # above (document_type == "unrecognised"). Nothing is "required" since
    # the type itself is unknown — these are just candidates worth checking
    # for, same spirit as additional elsewhere. Extraction still runs
    # against these rather than being skipped, so a reviewer has *something*
    # pulled out instead of nothing — see build_extraction_prompt.
    "unrecognised": {
        "required": [],
        "additional": [
            "document_title", "company_name", "individual_name", "id_number",
            "account_number", "amount", "document_date", "address",
        ],
    },
}


# Extra, field-specific instructions appended after the required-field list
# for document types where a plain "extract this field" instruction isn't
# enough to get well-structured output — a field whose value is naturally a
# list of line items in the source document, not a single scalar. Each entry
# spells out the exact per-item keys for that field, grounded in what the
# matching doc_gen_scripts generator actually renders.
EXTRA_FIELD_GUIDANCE: dict[str, str] = {
    "bank_statement": (
        'For "transactions", its "value" should be an array of objects — one per '
        'transaction line visible in the statement — each with these keys: "date" '
        '(as shown in the document), "description", "amount" (numeric; negative for '
        "debits/withdrawals, positive for credits/deposits), and \"balance\" (the "
        "running balance after that transaction, if shown, else null). Include "
        "every transaction you can find in the text — do not summarise, truncate, "
        "or only include a sample. Its \"confidence\" should reflect your overall "
        "confidence in the transaction list as a whole."
    ),
    "credit_report": (
        'For "accounts", its "value" should be an array of objects — one per '
        'credit account/facility listed — each with these keys: "product_type" '
        '(e.g. "HDB Loan", "Unsecured Credit Card"), "grantor" (the lender), '
        '"account_type" (e.g. "Single"/"Joint"), "date_opened", "date_closed" '
        '(null if still open), "current_balance" (numeric, null if not shown), '
        'and "monthly_instalment" (numeric, null if not shown). Include every '
        "account you can find — do not summarise or sample.\n\n"
        'For "defaults", its "value" should be an array of objects — one per '
        'default record listed — each with these keys: "product_type", '
        '"grantor", "date_loaded", "original_amount" (numeric), "balance" '
        '(numeric), and "status" (e.g. "Negotiated Settlement"). An empty '
        "array is correct if the report's Default Records section shows none — "
        "that's the normal case, not a sign anything was missed."
    ),
    "source_of_wealth": (
        'For "notes", its "value" should be an array of objects — one per '
        'numbered note in the financial statements — each with these keys: '
        '"note_number" (as shown, e.g. "3"), "heading" (e.g. "PROPERTY, PLANT '
        'AND EQUIPMENT"), and "text" (the note\'s explanatory wording, not the '
        "figures already captured elsewhere). Include every numbered note you "
        "can find."
    ),
    "list_of_loans": (
        'For "loan_terms", its "value" should be an array of objects — one per '
        "named contractual term in the facility letter's terms table beyond "
        'interest_rate/loan_tenure — each with keys "term_name" (as labelled '
        'in the document, e.g. "Purpose", "Repayment", "Security") and "value" '
        "(that term's text as shown). Capture whatever named terms are "
        "actually present — the exact set varies by facility type (term loan, "
        "overdraft, EFS) — rather than expecting a fixed list."
    ),
    "notice_of_assessment": (
        'For "tax_computation", its "value" should be an array of objects — '
        "one per line of the tax computation table between chargeable income "
        'and tax payable — each with keys "line" (as labelled, e.g. "First '
        '10,000 @ 75.00%", "Tax Rebate") and "amount" (numeric). Include every '
        "computation line you can find, in the order shown."
    ),
}


def schema_field_names(document_type: str) -> list[str]:
    """
    Flat union of a document type's required + additional schema fields —
    what extracted_fields_repository.replace_extracted_fields wants for its
    required_field_names param (it only distinguishes "named in the schema"
    vs. "the LLM added this on its own", not the required/additional split
    within the schema itself). Kept here so callers don't each re-derive
    this union from EXTRACTION_SCHEMAS's two-tier shape themselves.
    """
    schema = EXTRACTION_SCHEMAS.get(document_type, {})
    return [*schema.get("required", []), *schema.get("additional", [])]


_FIELD_WRAPPER_INSTRUCTIONS = (
    'Each field in extracted_fields must be an object of the form '
    '{"value": <the extracted value, or null if not present>, "confidence": <float '
    "0.0-1.0, your confidence that this specific value is correct and clearly "
    "supported by the text>} — do not return a bare value, always wrap it like "
    "this, for every field regardless of which tier it came from."
)


def build_extraction_prompt(document_type: str, raw_text: str) -> str:
    schema = EXTRACTION_SCHEMAS.get(document_type)
    if schema is None:
        raise ValueError(f"No extraction schema defined for document_type: {document_type}")

    required = schema["required"]
    additional = schema["additional"]
    extra_guidance = EXTRA_FIELD_GUIDANCE.get(document_type, "")
    # Precomputed (not inlined in the f-string below) since a backslash can't
    # appear inside an f-string expression on Python <3.12.
    extra_guidance_block = f"\n{extra_guidance}\n" if extra_guidance else ""

    if document_type == "unrecognised":
        field_list = "\n".join(f"- {f}" for f in additional)
        return f"""This document could not be matched to any known KYC/onboarding category, so
there is no fixed template for it. It will still be routed for manual
review — your job here is only to pull out whatever of the following
general fields actually appear in the text below, plus any other fields
you notice that seem relevant for a loan application. These are fields
that tend to show up across loan-application documents generally, not
specific to any one type, so none of them are mandatory here.

Fields to check for:
{field_list}

Use null as the value for any of the fields above that isn't present — do
not guess or invent values. You may also add any other fields you find
relevant, using clear, descriptive snake_case keys.

{_FIELD_WRAPPER_INSTRUCTIONS}

Document text:
{raw_text}

Respond with ONLY a JSON object, no other text, of this shape:
{{"extracted_fields": {{"<field_name>": {{"value": ..., "confidence": <float 0.0-1.0>}}, ...}}, "confidence": <float 0.0-1.0>, "escalation": null}}
"""

    required_block = "\n".join(f"- {f}" for f in required) or "(none — see additional fields below)"
    additional_block = "\n".join(f"- {f}" for f in additional) or "(none)"

    return f"""You are extracting structured data from a {document_type.replace('_', ' ')} for a KYC/onboarding pipeline.

Given the document text below, extract fields in three tiers:

REQUIRED fields — every one of these must appear as a key in
extracted_fields, and you should expect to actually find them in genuine
{document_type.replace('_', ' ')} text. Use null only if truly absent after a careful
read — an absent required field is itself meaningful (see escalation note
below), not just a gap to fill with null and move on.
{required_block}

ADDITIONAL fields — known fields for this document type that legitimately
vary by subtype or format. Extract them if present; null is a normal,
expected outcome for these, not a sign anything is wrong.
{additional_block}

OPTIONAL — you may also include any OTHER fields you find in the document
that seem relevant for a loan application (e.g. additional identifiers,
dates, amounts, addresses) beyond the two lists above — there's no fixed
list for these, use your judgement about what a loan officer reviewing
this document would find useful. Add them directly into extracted_fields
using clear, descriptive snake_case keys.
{extra_guidance_block}
{_FIELD_WRAPPER_INSTRUCTIONS}

If the text does NOT actually look like a {document_type.replace('_', ' ')} (e.g. it's clearly a
different document type), do not force-fit these fields. Instead return an
escalation whose "reason" describes what the document actually appears to
be — its title/heading, who it's from or addressed to, and any dates or
reference numbers visible — so the classifier can be re-run with that
description in mind rather than just being told "it doesn't match". Also
include an "evidence" list of the specific lines or phrases copied
verbatim from the document text above (not paraphrased) that led you to
this conclusion, so the classifier can weigh the actual text rather than
just your summary of it. You should also escalate (even without a
confident alternative guess) if most or all of the REQUIRED fields above
are genuinely absent from the text despite a careful read — that pattern
is itself evidence the classification is wrong, separately from whether
you can tell what the document actually is.

Document text:
{raw_text}

Respond with ONLY a JSON object, no other text, of this shape:
{{"extracted_fields": {{"<field_name>": {{"value": ..., "confidence": <float 0.0-1.0>}}, ...}}, "confidence": <float 0.0-1.0>, "escalation": null}}

OR, if the document doesn't match:
{{"extracted_fields": {{}}, "confidence": 0.0, "escalation": {{"reason": "<what the document actually looks like>", "evidence": ["<verbatim line from the text>", ...]}}}}
"""
