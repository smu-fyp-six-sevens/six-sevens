"""
Per-category extraction prompts and expected field lists for the
Extraction stage. Kept in Python (not Dify) so field lists, prompt
wording, and escalation logic are testable and version-controlled.

Each document type's schema is split into three tiers rather than one flat
list, since "expected but missing" means different things depending on
which tier a field belongs to:

- required: must be present for this to genuinely be the classified type
  (e.g. nric needs individual_name + id_number). Missing one is real
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
        #
        # company_name is the account holder only on a CORPORATE statement
        # (doc_gen_scripts/corporate_bank_statement). A PERSONAL statement
        # (personal_proof_of_residence/docgen_bankstatement_ocbc_personal.py)
        # names an individual account holder and their residential address
        # instead, with no company at all — so company_name moved out of
        # required, and individual_name/individual_address are here, since
        # models.compute_fulfilled_requirements uses exactly this
        # company-vs-individual split to tell the two apart.
        "required": ["bank_name", "account_number", "transactions"],
        # statement_period_* through holds (not doc_gen-verified — added for
        # fraud_detection.numerical_validation's BS.* checks, e.g.
        # BS.PERIOD_BOUNDS/BS.OVERDRAFT_LIMIT): only extract these where the
        # statement actually prints them; omit rather than guess.
        # total_credits/total_debits only exist on templates that print a
        # single whole-statement total — many print per-period totals only,
        # and summing those is a calculation, not a transcription.
        # extract._drop_unprinted_numbers enforces that in code. No
        # transaction_count: statements don't print one, so it would only
        # ever be a count the LLM made itself. BS.TOTALS skips whichever of
        # these are absent.
        # opening_balance is doc_gen-verified only for the OCBC-style
        # statement (docgen_bankstatement_ocbc.py prints an explicit
        # "Opening Balance" header field, ctx['opening']) — the other
        # template (docgen_bankstatement.py) computes the same value
        # internally but never prints it as its own labelled field, so
        # extract it only where the statement actually shows it. There's
        # no separate "closing balance" field: both templates print that
        # same figure under the "Ledger Balance"/"Available Balance"
        # labels already captured below.
        # bank_registration_no / bank_gst_no / bic_code: the issuing BANK's
        # own Co. Reg. No., GST Reg. No. and SWIFT BIC as printed on the
        # statement (header or footer) — not the account holder's. Read by
        # BS.BANK_IDENTIFIERS.
        "additional": [
            "company_name", "individual_name", "individual_address",
            "statement_period_start", "statement_period_end", "overdraft_limit",
            "opening_balance", "ledger_balance", "available_balance", "holds",
            "total_credits", "total_debits", "bank_registration_no", "bank_gst_no",
            "bic_code",
        ],
    },
    "certificate_of_incorporation": {
        # generate_from_mastersheet.py actually calls coi_generator.py, not
        # certificate_of_incorporation_generator.py (dead code, never
        # imported) — company_status ("Limited Exempt Private Company") is
        # hardcoded in coi_generator.py's DEFAULT_CONTEXT and never
        # overridden by the caller, so it's always present. former_names is
        # genuinely probabilistic there (rng.random() >= 0.4 -> no former
        # name ~60% of the time), a real subtype-dependent field.
        "required": ["company_name", "uen", "incorporation_date", "company_type", "incorporating_act", "certificate_date"],
        # company_former_name_change_date (not doc_gen-verified — added for
        # COI.EVENT_AFTER_ISSUE/COI.DATE_LOGIC): the date a former-name
        # change took effect, where the certificate shows one at all.
        "additional": ["company_former_name", "company_former_name_change_date"],
    },
    "payslip": {
        # employee_name/employer_name/net figures are all rendered by every
        # one of build_payslip_1/2/3 in generate_payslips.py. company_address
        # (the employer's address, always populated via the mastersheet's
        # Employer Address or a company_facts() fallback) is only actually
        # rendered by build_payslip_1/3, not build_payslip_2.
        "required": ["individual_name", "company_name", "net_pay"],
        # earnings_items through pay_date (not doc_gen-verified — added for
        # fraud_detection.numerical_validation's PAY.* checks): the itemised
        # breakdown behind net_pay, where the payslip shows one.
        # employer_cpf / date_of_birth / additional_wages: read by PAY.CPF_RATE
        # (CPF Board's age-band rates). additional_wages is a bonus or other
        # non-monthly pay, only where the payslip shows one.
        "additional": [
            "company_address", "earnings_items", "deductions_items",
            "total_earnings", "total_deductions", "employee_cpf",
            "ordinary_wages", "basic_pay", "pay_period_end", "pay_date",
            "employer_cpf", "date_of_birth", "additional_wages",
        ],
    },
    # Split from a single former "id_document" type — passport and NRIC
    # need separate schemas because passport_generator.py's own comment
    # says it plainly: "unlike nric_generator.py, this page carries no
    # address field". individual_name/id_number/date_of_birth/nationality
    # are rendered on both (id_number holds the NRIC number even on a
    # passport page — see passport_generator.py's national_id_no comment).
    # They still fulfil the same case-checklist requirement though — see
    # models.IDENTITY_DOCUMENT_TYPES/IDENTITY_DOCUMENT_REQUIREMENT.
    "passport": {
        "required": ["individual_name", "passport_number", 
                     "date_of_birth", "nationality", "mrz", "date_of_expiry",
                     "date_of_issue", "place_of_birth", "sex",],
        "additional": ["nric_number",],
    },
    "nric": {
        # Grounded directly in nric_generator.py's _draw_front/_draw_back,
        # not guessed: FRONT renders nric_number (the "IDENTITY CARD NO."
        # strip), name, race, date_of_birth, sex, and country/place of
        # birth; BACK renders nric_number again, date_of_issue, and the
        # address (plus a fingerprint/barcode, neither extractable as
        # text). date_of_expiry and nationality used to be listed here but
        # neither is ever actually rendered on either side by the
        # generator — a real Singapore NRIC has no printed expiry date at
        # all — so asking for them just guaranteed an always-null field.
        #
        # Only nric_number is unconditionally required — front and back
        # are near-always two separate uploads (see EXTRA_FIELD_GUIDANCE
        # below), so nothing else is guaranteed present regardless of
        # which side this is. individual_name/date_of_birth/country
        # (front-only) and individual_address/date_of_issue (back-only)
        # go in "additional" instead — genuinely subtype-varying by side,
        # exactly what that tier is for — but EXTRA_FIELD_GUIDANCE makes
        # them conditionally required once document_side is known: e.g. a
        # front page missing individual_name is still worth escalating on.
        "required": ["nric_number"],
        "additional": [
            "individual_name", "date_of_birth", "country","race", "sex",
            "individual_address", "date_of_issue", "document_side",
        ],
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
        # tax_residences through form_version_date (not doc_gen-verified —
        # added for fraud_detection.numerical_validation's FATCA.* checks):
        # the form's own multi-jurisdiction/entity-classification sections,
        # which this schema didn't previously capture beyond the single
        # `tin` field above.
        "additional": [
            "tax_residences", "entity_type", "controlling_persons",
            "specified_us_person", "us_tin", "form_version_date",
        ],
    },
    "certificate_of_incumbency": {
        # The people are arrays, not flat fields: shareholders is mandatory, and there are no
        # single-shareholder fields (shareholder_name/address, nric_number).
        "required": [
            "company_name", "uen", "incorporation_date", "company_type",
            "company_address", "share_capital", "share_class", "shareholders",
        ],
        # shareholders/officers feed fraud_detection.numerical_validation's ACRA.* checks.
        "additional": [
            "officers", "company_former_name",
            "document_date", "last_agm_date", "last_annual_return_date", "financial_year_end",
        ],
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
        #
        # "subscribers" (not individual_name/individual_address/id_number):
        # the holders list is a list, always with at least one entry, often
        # more — flat singular fields forced the model to either drop every
        # subscriber but one or squash them into one joined string (e.g.
        # "Ho Chloe; Vikram s/o Ramesh"), which then can't be matched back
        # to either person's own id_document identity (see
        # mapper._named_addresses_from_document, which reads this array).
        "required": [
            "company_name", "uen", "incorporation_date", "company_address",
            "share_capital", "issued_shares", "subscribers",
        ],
        # quorum/adoption_date/execution_date (not doc_gen-verified — added
        # for fraud_detection.numerical_validation's CONST.QUORUM/
        # CONST.DATE_LOGIC checks).
        "additional": ["company_type", "notes", "quorum", "adoption_date", "execution_date"],
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
        # billing_period_end through contract_end_date (not doc_gen-verified
        # — added for fraud_detection.numerical_validation's POA.* checks):
        # the utility-bill figures a genuine bill itemises.
        # gst_amount / charges_before_gst: read by POA.GST (IRAS's rate for
        # the bill date), only where the bill prints a GST line.
        "additional": [
            "individual_name", "individual_address", "company_name", "company_address",
            "billing_period_end", "bill_date", "usage_this_period", "consumption_trend",
            "previous_balance", "payment_received", "outstanding_balance",
            "current_charges", "total_payable", "due_date", "contract_end_date",
            "gst_amount", "charges_before_gst",
        ],
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
        #
        # Not doc_gen-verified: a personal brokerage/investment account
        # statement (e.g. Moomoo, a margin account) is just as legitimate a
        # source_of_wealth document as a company financial statement, but
        # has no company_name/uen at all — see EXTRA_FIELD_GUIDANCE below,
        # which tells the model not to escalate on that basis alone when
        # it's clearly this individual-account variant instead.
        "required": ["company_name", "uen"],
        # A personal account statement (no UEN, a person's name) is
        # required to name its account holder instead of a company.
        "required_when_individual": ["account_holder_name"],
        # income_statement_items through equity_items/total_equity (not
        # doc_gen-verified — added for fraud_detection.numerical_validation's
        # FS.* checks): the full income-statement/balance-sheet line items
        # behind the headline revenue/net_profit/total_assets figures above.
        # Only extract where the statement actually itemises to this level.
        #
        # cost_of_sales/operating_expenses/tax_expense/profit_before_tax/
        # finance_costs/depreciation_expense deliberately do NOT have their
        # own flat fields here — every income-statement line other than
        # revenue and profit_for_the_year (the two headline figures kept
        # flat) belongs inside income_statement_items instead, so a
        # two-column (current/prior year) statement doesn't explode into a
        # dozen near-duplicate *_prior_year fields. See EXTRA_FIELD_GUIDANCE
        # below for its row shape, and numerical_validation._is_item for how
        # the FS.* checks look a specific line up by label.
        #
        # balance_sheet_items is the same idea applied to the balance sheet:
        # ONE array covering every section (current/non-current assets,
        # current/non-current liabilities, equity) tagged by its "section"
        # key, rather than 5 separate near-identical arrays — see
        # numerical_validation._bs_section_rows for how FS.BS_CAST groups
        # rows back out by section to check each section's own subtotal.
        #
        # note_references is gone as its own field — a note citation only
        # means something next to the line it was cited on, so it lives as
        # each row's own "note" column in income_statement_items/
        # balance_sheet_items instead (see FS.NOTE_REFS).
        "additional": [
            "individual_address", "account_holder_name", "revenue", "net_profit",
            "total_assets", "total_liabilities", "share_capital", "notes",
            "broker_name", "account_number", "account_holder_name",
            "cash_balance", "portfolio_value", "net_asset_value", "holdings", "margin_loan",
            "income_statement_items", "balance_sheet_items",
            "profit_for_the_year", "profit_for_the_year_prior_year",
            "retained_earnings_opening", "retained_earnings_closing",
            "retained_earnings_closing_prior_year", "ppe_opening", "ppe_closing",
            "borrowings_current", "borrowings_non_current",
            "loan_tenure", "financial_year_end", "signing_date",
            "total_current_assets", "total_non_current_assets",
            "total_current_liabilities", "total_non_current_liabilities", "total_equity",
            # *_prior_year (not doc_gen-verified — added for FS.BALANCES to
            # also check the comparative column, same reasoning as
            # profit_for_the_year_prior_year/retained_earnings_closing_prior_year
            # above): a two-column statement (FY26/FY25 side by side) states
            # a full prior-year balance sheet, not just its own headline
            # figures — extract it where shown.
            "total_assets_prior_year", "total_liabilities_prior_year", "total_equity_prior_year",
        ],
    },
    "credit_report": {
        # credit_bureau_report_generator.py: all five identity fields are
        # unconditional (address falls back to the case's registered company
        # address rather than ever being left blank).
        #
        # "accounts" stays additional: _pick_accounts(rng) always returns at
        # least one row from SAMPLE_ACCOUNTS (never genuinely empty), but
        # it's a random-sized subset each case, not a fixed list to check
        # off — nothing about its count or membership is itself meaningful.
        #
        # "defaults"/"bankruptcy_proceedings" are REQUIRED despite usually
        # having zero rows: build_persona()'s PDF always renders both
        # section headers/tables (_section_bar("Default Records") /
        # _section_bar("Bankruptcy Proceedings") are unconditional calls,
        # not behind an if-branch), but only actually populates a case's
        # rows for a small per-case chance — default_records only when
        # rng.random() < 0.15, bankruptcy_rows only when < 0.08, else `[]`
        # for that case. So the KEY should always appear in extracted_fields
        # even though an empty array is the normal, expected value most of
        # the time (see EXTRA_FIELD_GUIDANCE below) — same "always present,
        # content genuinely varies" grounding as any other required field.
        "required": [
            "individual_name", "id_number", "date_of_birth", "nationality",
            "individual_address", "defaults", "bankruptcy_proceedings",
        ],
        # An empty list is a valid answer for these (no defaults/bankruptcies on record).
        "allow_empty": ["defaults", "bankruptcy_proceedings"],
        # account_count through bureau_score (not doc_gen-verified — added
        # for fraud_detection.numerical_validation's CR.* checks): the
        # report's own summary counters and score block.
        "additional": [
            "accounts", "account_count", "enquiry_count", "default_count",
            "earliest_account_date", "total_outstanding_balance", "report_date", "bureau_score",
            "previous_enquiries","narratives",
        ],
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
        # monthly_instalment through offer_validity_days (not doc_gen-
        # verified — added for fraud_detection.numerical_validation's
        # LOAN.* checks). offer_date IS doc_gen-verified though — every
        # template's letterhead() helper (loan_letter_common.py) renders
        # it as the letter's own "Date:" line, right after "Our Ref:".
        # review_date is doc_gen-verified for the overdraft variant only
        # (overdraft_facility_generator.py; term_loan/EFS use a fixed
        # tenure instead, see loan_tenure above) — see EXTRA_FIELD_GUIDANCE
        # below for how to tell "Date:" apart from Review Date and the
        # Validity-of-Offer window, which are not the same field.
        "additional": [
            "interest_rate", "loan_tenure", "loan_terms",
            "monthly_instalment", "repayment_schedule", "fee_percentage", "fee_amount",
            "facility_type", "scheme_name", "offer_date", "acceptance_date", "offer_validity_days",
            "review_date",
        ],
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
            "company_name", "tax_reference_number", "company_address", "year_of_assessment",
            "noa_date", "eci", "tax_payable",
        ],
        # previous_assessment_date (not doc_gen-verified — added for
        # fraud_detection.numerical_validation's NOA.DATE_LOGIC check):
        # only present when this notice refers back to an earlier one.
        "additional": ["tax_computation", "previous_assessment_date"],
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
    "company_constitution": (
        'For "subscribers", its "value" should be an array of objects — '
        "one per subscriber/shareholder/director named in the "
        'constitution — each with these keys: "name", "address" (their '
        'residential address as shown, or null if not given), "id_number" '
        '(their NRIC/passport number as shown, or null if not given), and '
        '"shares" (numeric, the number of shares this subscriber holds, or '
        "null if not shown). Extract each subscriber as a SEPARATE entry "
        "in this array — never combine multiple people's names, addresses, "
        'or id_numbers into one joined string (e.g. never "Jane Tan; John '
        'Lim" as a single name value) — that makes it impossible to tell '
        "the individuals apart afterwards. Include every subscriber you "
        "can find, even if some of their fields are null."
    ),
    "certificate_of_incumbency": (
        "An ACRA Business Profile (also called a BizFile, or ACRA BizFile+ "
        "extract) IS a certificate_of_incumbency for this pipeline's "
        "purposes — Singapore companies don't typically issue a document "
        'literally titled "Certificate of Incumbency"; an ACRA Business '
        "Profile serves the same function (an official snapshot of the "
        "company's registration, officers, and shareholders) and is what "
        "this type's own generator (docgen_acra_profile.py) actually "
        "produces. Do NOT escalate just because the document is titled "
        '"ACRA Business Profile", "BizFile", "Business Profile", or '
        'similar rather than "Certificate of Incumbency" — that\'s the '
        "expected, normal title for this type in practice, not evidence "
        "of a mismatch.\n\n"
        'For "shareholders", its "value" should be an array of objects, one '
        "per shareholder listed, each with these keys: "
        '"name", "identification_no" (the ID or UEN as printed), "address", '
        '"shares" (the number held, as printed) and, where shown, '
        '"share_class". There are no separate single-shareholder fields: '
        "even a sole shareholder is a one-item array."
    ),
    "payslip": (
        'For "earnings_items", its "value" should be an array of objects, '
        "one per earnings line exactly as printed, each with these keys: "
        '"description" (the line\'s label), "amount" (as printed), and '
        '"cpf_wage_type", how Singapore CPF treats the line: "ordinary" for '
        "pay wholly for this month's work (basic salary, allowances — CPF "
        "treats allowances as ordinary wages — overtime, shift pay); "
        '"additional" for pay that is not (annual or '
        "performance bonus, AWS / 13th month, variable pay, incentives, leave "
        'pay or encashment, back pay); "not_wages" for payments CPF does not '
        "treat as wages (expense reimbursements or claims, benefits in kind, "
        'termination or retrenchment benefits); "uncertain" when the label '
        'alone cannot tell (e.g. "Commission", "Benefits"). Do not guess: use '
        '"uncertain" rather than pick a type the label does not support.\n\n'
        'For "deductions_items", the same shape with "description", "amount" '
        '(as printed, keeping a minus sign if shown) and "category": '
        '"employee_cpf" for the employee\'s own CPF contribution (labelled '
        'CPF, Employee CPF, Employee contribution, EE CPF and the like); '
        '"employer_cpf" for the employer\'s share where a payslip shows it; '
        '"self_help_group" for CDAC, SINDA, MBMF or ECF; "income_tax" for '
        'tax withheld; "other" for anything else.\n\n'
        'Only extract "employee_cpf", "employer_cpf", "ordinary_wages" and '
        '"additional_wages" as separate fields when the payslip prints that '
        "figure on its own (e.g. a \"CPF wages\" or \"Ordinary wages\" box); "
        "never add up or work out a figure that is not printed. Leave them "
        "out otherwise: the lines above already carry them."
    ),
    "nric": (
        'For "document_side", its "value" should be one of "front", '
        '"back", "single", or "unknown". FRONT typically shows a face '
        "photo and the person's name, race, date of birth, sex, and "
        "country/place of birth; BACK typically shows a residential "
        'address, thumbprint, and/or issue date, with no name or photo. '
        'Use "single" instead when the document is self-contained and has '
        "no separate other side to expect at all — a single image that "
        "already shows both the identity details and the address in one "
        "capture.\n\n"
        "You only have OCR text, not the image, so you cannot see a face "
        "photo or thumbprint — decide the side from which text is "
        "present in the OCR text itself, using these rules:\n"
        '- "front": the text contains the person\'s name and date of '
        "birth (or race/sex) and NO residential address.\n"
        '- "back": the text contains a residential address and/or a date '
        "of issue and NO person's name.\n"
        '- "single": the text contains both the person\'s name and a '
        "residential address.\n"
        '- "unknown": none of the above fits, or the text is too '
        "fragmentary to tell.\n"
        'When exactly one of these rules clearly matches, report the '
        '"document_side" confidence as 0.95 or higher — a clean match '
        "against these rules IS strong evidence, so do not hedge just "
        "because you cannot see the photo or thumbprint. Report lower "
        "only when the signals genuinely conflict or the text is "
        "garbled or partial.\n\n"
        "Which of the ADDITIONAL fields above actually count as expected "
        "depends on document_side — only extract, and only treat as "
        "missing-is-suspicious, the fields that side actually shows:\n"
        '- "front": individual_name, date_of_birth, and country are '
        "expected — escalate if most of these are genuinely absent "
        "despite a careful read, same as a REQUIRED field would. "
        "individual_address and date_of_issue are NOT expected here — "
        "leave them null without escalating on that basis.\n"
        '- "back": individual_address and date_of_issue are expected — '
        "escalate if both are genuinely absent. individual_name, "
        "date_of_birth, and country are NOT expected here — a name not "
        "being visible on the back of an NRIC is normal, not evidence the "
        "classification is wrong.\n"
        '- "single": all five (individual_name, date_of_birth, country, '
        "individual_address, date_of_issue) are expected together, same "
        "as a genuinely combined front+back capture would show.\n"
        '- "unknown": treat none of the five as expected either way.\n\n'
        "Regardless of side, still escalate if the text doesn't look like "
        "any part of an NRIC at all, or if nric_number itself (the one "
        "field REQUIRED above) is absent."
    ),
    "bank_statement": (
        'For "transactions", its "value" should be an array of objects — one per '
        'transaction line visible in the statement — each with these keys: "date" '
        '(as shown in the document), "description", "amount" (numeric; negative for '
        "debits/withdrawals, positive for credits/deposits), \"balance\" (the "
        "running balance after that transaction, if shown, else null), and "
        '"counterparty" (the name of the other party as printed in the description '
        "— who was paid or who paid, e.g. a supplier or customer company — without "
        "reference numbers, payment channel words like FAST/GIRO/PayNow, or the "
        "amount; null when the line names no other party, e.g. bank charges or "
        "interest). Include "
        "every transaction you can find in the text — do not summarise, truncate, "
        "or only include a sample. Its \"confidence\" should reflect your overall "
        "confidence in the transaction list as a whole.\n\n"
        "Bank statements come in two kinds, told apart by who holds the account. "
        'For a CORPORATE statement (account held by a company), set "company_name" '
        "to the account-holding company and leave \"individual_name\" and "
        '"individual_address" null. For a PERSONAL statement (account held by a '
        'named individual), set "individual_name" to the account holder and '
        '"individual_address" to their address as printed in the statement\'s '
        'customer block, and leave "company_name" null. "company_name" is never '
        'the bank\'s own name — that belongs in "bank_name" — and the address '
        "of the bank itself (e.g. in the letterhead or footer) is never the "
        "account holder's \"individual_address\"."
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
        "that's the normal case, not a sign anything was missed.\n\n"
        'For "bankruptcy_proceedings", its "value" should be an array of '
        'objects — one per row listed in the Bankruptcy Proceedings section — '
        'each with these keys: "bankruptcy_number", "order_date", '
        '"petition_date", "original_order_date", and "gazette_date". Extract '
        "each column verbatim as shown — the last column sometimes holds a "
        'status phrase like "Bankruptcy Order" instead of an actual date, '
        "which is normal for this report, not a sign of misreading. An empty "
        "array is correct if the section shows no rows — most applicants have "
        "none, so that's the normal case, not a sign anything was missed."
    ),
    "source_of_wealth": (
        'For "income_statement_items", its "value" should be an array of '
        'objects — one per line of the income statement OTHER than Revenue '
        'and Profit for the year (those two stay their own top-level '
        'fields) — each with these keys: "label" (as printed, e.g. "Cost '
        'of sales", "Staff costs", "Other operating expenses", "Total '
        'operating expenses", "Finance costs", "Profit before taxation", '
        '"Tax expense"), "amount" (numeric, this year\'s column; negative '
        'for a figure shown in brackets, e.g. an expense), "amount_prior_year" '
        '(numeric, the comparative column if the statement shows one, else '
        'null), and "note" (the note number shown next to that line, e.g. '
        '"3", or null if no note reference is printed next to it). Include '
        'every line between Revenue and Profit for the year, in the order '
        'shown — this means both subtotals (Gross profit, Total operating '
        'expenses, Profit from operations, Profit before taxation) AND the '
        'individual expense line items making up Operating expenses (e.g. '
        'Staff costs, Rental expenses, Other operating expenses — the exact '
        'set varies by company). Do not also create separate flat fields '
        'like "operating_expenses" or "operating_expenses_prior_year" for '
        'anything that belongs in this array.\n\n'
        'For "balance_sheet_items", its "value" should be a SINGLE array '
        'covering every line of the balance sheet, across all five sections '
        '— do not split it into separate per-section fields. Each entry is '
        'an object with keys "section" (exactly one of "current_assets", '
        '"non_current_assets", "current_liabilities", "non_current_liabilities", '
        '"equity" — whichever heading that row is printed under), "label" '
        '(as printed, e.g. "Property, plant and equipment", "Trade '
        'payables", "Share capital"), "amount" (numeric, this year\'s column), '
        '"amount_prior_year" (numeric, the comparative column if the balance '
        'sheet shows one, else null), and "note" (the note number shown next '
        'to that line, or null if none). Include every row actually shown under each section '
        'heading, not just one summary row per section — a section with '
        'three line items contributes three array entries, all with the '
        'same "section" value.\n\n'
        'For "notes", its "value" should be an array of objects — one per '
        'numbered note in the financial statements — each with these keys: '
        '"note_number" (as shown, e.g. "3"), "heading" (e.g. "PROPERTY, PLANT '
        'AND EQUIPMENT"), and "text" (the note\'s explanatory wording, not the '
        "figures already captured elsewhere). Include every numbered note you "
        "can find. Only applies to a company financial statement — see below "
        "for the other kind of source_of_wealth document.\n\n"
        "A personal brokerage/investment account statement (e.g. a margin "
        "account, a securities portfolio statement) is just as legitimate a "
        "source_of_wealth document as a company financial statement, but "
        "belongs to an individual rather than a company — it will have no "
        "company_name or uen at all, and that absence is expected, NOT "
        "evidence the classification is wrong. If the text is clearly this "
        "kind of document, do not escalate just because company_name/uen "
        "(REQUIRED fields above) are missing; instead extract whichever of "
        'these apply: "broker_name", "account_number", "account_holder_name" '
        '(the individual\'s name), "cash_balance", "portfolio_value", '
        '"net_asset_value". For "holdings", its "value" should be an array '
        'of objects — one per position held — each with these keys: "name", '
        '"symbol", "quantity", "closing_price", and "market_value" (in '
        "whatever currency the statement shows — include a separate "
        '"currency" key if stated). Include every holding you can find.'
    ),
    "list_of_loans": (
        'For "loan_terms", its "value" should be an array of objects — one per '
        "named contractual term in the facility letter's terms table beyond "
        'interest_rate/loan_tenure — each with keys "term_name" (as labelled '
        'in the document, e.g. "Purpose", "Repayment", "Security") and "value" '
        "(that term's text as shown). Capture whatever named terms are "
        "actually present — the exact set varies by facility type (term loan, "
        "overdraft, EFS) — rather than expecting a fixed list.\n\n"
        '"offer_date" is the letter\'s own dateline — the "Date:" line near '
        'the top, right after "Our Ref:" — not any other date on the page. '
        'Do not confuse it with "Review Date" (a future annual-review '
        "checkpoint, its own separate field, review_date) or the wording "
        "under \"Validity of Offer\" (a relative window like \"30 days from "
        "the date of this letter\", not an absolute date — leave "
        'offer_validity_days as that number of days, not a date). '
        '"acceptance_date" is the date next to the signatory\'s own '
        'signature in the "Acceptance" section at the end, not the '
        "letter's own dateline."
    ),
    "notice_of_assessment": (
        'For "eci", use the line "Estimated Chargeable Income before deducting '
        'Exempt Amount" (it may also read "Chargeable Income" or "ECI"): that '
        "figure is the ECI, even though the label is longer than \"ECI\". It is "
        "printed as a standalone amount, so it is not an inference. The separate "
        'line "Estimated Chargeable Income after deducting Exempt Amount" is '
        'not "eci"; extract it, if you want it, as "chargeable_income_after_exemption". '
        "Do not add fields such as \"issuing_authority\" whose value only follows from "
        "the letterhead, website, portal or the Comptroller's title: extract only what is "
        "printed as its own labelled field.\n\n"
        'For "tax_computation", its "value" should be an array of objects — '
        "one per line of the tax computation table between chargeable income "
        'and tax payable — each with keys "line" (as labelled, e.g. "First '
        '10,000 @ 75.00%", "Tax Rebate") and "amount" (numeric). Include every '
        "computation line you can find, in the order shown."
    ),
}


# Hardcoded on purpose rather than derived from EXTRACTION_SCHEMAS.keys() —
# this is what gets shown to the model when a document was classified
# "unrecognised", asking it to suggest a specific canonical type if the
# content actually looks like one of these. Keep in sync with
# EXTRACTION_SCHEMAS's keys (minus "unrecognised" itself) by hand; a
# mismatch here means the model could suggest a type the langgraph pipeline can't
# actually build an extraction prompt for.
KNOWN_DOCUMENT_TYPES = [
    "bank_statement", "certificate_of_incorporation", "payslip", "passport", "nric",
    "fatca_crs_declaration", "certificate_of_incumbency", "company_constitution",
    "proof_of_address", "source_of_wealth", "credit_report", "list_of_loans",
    "notice_of_assessment",
]


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


def drop_empty_optional_fields(document_type: str | None, fields: dict) -> list[str]:
    """
    Removes, in place, every field whose value is None or a blank string,
    unless the document type requires it. An optional field the document
    doesn't print (a statement with per-month totals only has no
    total_credits) is absent, not a field with nothing in it: stored as an
    empty row, it shows up in manual review as a field to fill in, and the
    reviewer can't make it go away.

    A required field stays even when empty, so it still counts as missing
    (case_service.missing_required_fields). Empty lists and objects are
    left alone: some checks read [] as "none", which is not the same as
    "not extracted". Returns the names removed.
    """
    schema = EXTRACTION_SCHEMAS.get(document_type or "", {})
    required = {*schema.get("required", []), *schema.get("required_when_individual", [])}
    dropped = [
        name for name, value in fields.items()
        if name not in required
        and (value is None or (isinstance(value, str) and not value.strip()))
    ]
    for name in dropped:
        del fields[name]
    return dropped


# The "0.9" below mirrors settings.EXTRACTION_CONFIDENCE_THRESHOLD (the point past which a field
# needs no manual review) — kept as a literal since this is prompt text, not code.
_FIELD_WRAPPER_INSTRUCTIONS = (
    "A field that is one of this document type's required fields (listed above) but genuinely "
    "isn't present should still be returned, wrapped with \"value\": null — that gap is real and "
    "needs a human to see it. An ADDITIONAL field (also listed above) that doesn't apply to this "
    "document, or isn't printed on it, should be left out of extracted_fields entirely — do not "
    'include it with a null value just to show you considered it.\n\n'
    'Each field you do include in extracted_fields must be an object of the form '
    '{"value": <the extracted value>, "confidence": <float '
    "0.0-1.0, your confidence that this specific value is correct and clearly "
    'supported by the text>, "low_confidence_reason": <a short phrase for why, e.g. '
    '"handwriting is hard to read", "figure is partially cut off", "value is implied '
    'rather than printed directly" — required whenever confidence is below 0.9, omit '
    "or use null otherwise> — do not return a bare value, always wrap it like "
    "this, for every field regardless of which tier it came from.\n\n"
    "Every value must be a literal transcription of what the document itself "
    "states — never your own commentary, correction, or judgement about "
    "whether a value looks wrong, inconsistent, or anomalous. If a date, "
    "figure, or other value looks like a typo, a miscalculation, or doesn't "
    "match something else in the document, extract it exactly as printed "
    "anyway (lower confidence is fine if the print quality itself is poor, "
    "but not because the value looks suspicious) — do not silently correct "
    "it to what you think was intended, and do not add a separate field "
    "describing the discrepancy. Spotting and explaining anomalies is a "
    "separate, later, deterministic step; this step is transcription only.\n\n"
    "Never calculate a value. Do not add, subtract, multiply, count, average, "
    "or convert figures to produce a field — every number you return must be "
    "printed in the document exactly as a figure. If a field only exists as "
    "something you would have to work out (e.g. a whole-statement total when "
    "the statement only prints per-month totals, a fee amount when only a "
    "percentage is printed), omit it. Any numeric value that does not appear "
    "in the document text is discarded automatically."
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
        known_types_block = "\n".join(f"- {t}" for t in KNOWN_DOCUMENT_TYPES)
        return f"""This document could not be matched to any known KYC/onboarding category, so
there is no fixed template for it. It will still be routed for manual
review — your job here is only to pull out whatever of the following
general fields actually appear in the text below, plus any other fields
you notice that seem relevant for a loan application. These are fields
that tend to show up across loan-application documents generally, not
specific to any one type, so none of them are mandatory here.

Fields to check for:
{field_list}

Extract a field ONLY if you actually find it in the text — do not guess or
invent values. If a field genuinely isn't present, OMIT its key from
extracted_fields entirely rather than including it with a null value. You
may also add any other fields you find relevant, using clear, descriptive
snake_case keys.

{_FIELD_WRAPPER_INSTRUCTIONS}

While reading the text, also check whether it actually fits one of these
known document categories — the classifier may simply have missed it:
{known_types_block}

If — and only if — you're confident the text genuinely matches one of these
(not just superficially similar), include an escalation suggesting that
type, using the exact category name from the list above as
"suggested_document_type", plus a "reason" and an "evidence" list of
verbatim lines from the text that support it. Otherwise leave escalation
null — a low-confidence guess here is worse than none, since a wrong
suggestion sends this document on another classification round-trip for
nothing.

Document text:
{raw_text}

Respond with ONLY a JSON object, no other text, of this shape:
{{"extracted_fields": {{"<field_name>": {{"value": ..., "confidence": <float 0.0-1.0>}}, ...}}, "confidence": <float 0.0-1.0>, "escalation": null}}

OR, if the text clearly matches one of the known categories above:
{{"extracted_fields": {{"<field_name>": {{"value": ..., "confidence": <float 0.0-1.0>}}, ...}}, "confidence": <float 0.0-1.0>, "escalation": {{"suggested_document_type": "<one of the categories above>", "reason": "<why it matches>", "evidence": ["<verbatim line from the text>", ...]}}}}
"""

    required_block = "\n".join(f"- {f}" for f in required) or "(none — see additional fields below)"
    additional_block = "\n".join(f"- {f}" for f in additional) or "(none)"
    known_types_block = "\n".join(f"- {t}" for t in KNOWN_DOCUMENT_TYPES if t != document_type)

    return f"""You are extracting structured data from a {document_type.replace('_', ' ')} for a KYC/onboarding pipeline.

Given the document text below, extract fields in three tiers:

REQUIRED fields — every one of these must appear as a key in
extracted_fields, and you should expect to actually find them in genuine
{document_type.replace('_', ' ')} text. Use null only if truly absent after a careful
read — an absent required field is itself meaningful (see escalation note
below), not just a gap to fill with null and move on.
{required_block}

ADDITIONAL fields — known fields for this document type that legitimately
vary by subtype or format. Extract a field ONLY if you actually find it in
the text. If an additional field is genuinely not present, OMIT its key
from extracted_fields entirely — do NOT include it with a null value just
to acknowledge it was checked. This is different from REQUIRED fields
above, which must always appear as a key even when absent.
{additional_block}

OPTIONAL — you may also include any OTHER fields you find in the document
that seem relevant for a loan application (e.g. additional identifiers,
dates, amounts, addresses) beyond the two lists above — there's no fixed
list for these, use your judgement about what a loan officer reviewing
this document would find useful. Add them directly into extracted_fields
using clear, descriptive snake_case keys. Only add one whose value is printed
in the document as its own labelled field; never one you would infer from
context (a letterhead, logo, website, document title or signatory's role).
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
just your summary of it. If — and only if — you're confident it's actually
one of these other known categories, also name it as
"suggested_document_type" (using the exact category name below); otherwise
omit that key rather than guessing weakly:
{known_types_block}
You should also escalate (even without a confident alternative guess) if
most or all of the REQUIRED fields above are genuinely absent from the
text despite a careful read — that pattern is itself evidence the
classification is wrong, separately from whether you can tell what the
document actually is.

Document text:
{raw_text}

Respond with ONLY a JSON object, no other text, of this shape:
{{"extracted_fields": {{"<field_name>": {{"value": ..., "confidence": <float 0.0-1.0>}}, ...}}, "confidence": <float 0.0-1.0>, "escalation": null}}

OR, if the document doesn't match:
{{"extracted_fields": {{}}, "confidence": 0.0, "escalation": {{"reason": "<what the document actually looks like>", "evidence": ["<verbatim line from the text>", ...], "suggested_document_type": "<one of the categories above, or omit>"}}}}
"""
