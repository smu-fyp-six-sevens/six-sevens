# Field-driven detection checks: plan

Status: draft for feedback. Nothing here is implemented yet.

## Goal

Stop the detection agent relying on keyword and label matching. The specialists see which fields
a document has, pick the ones a check needs, and a tool does the calculation and returns the result.

## 1. Lead

Remove `extracted_field_names` from the lead's context in `load_context_node`. The lead keeps the
document type, filename, metadata flag and file extension, and writes briefs as before. The lead
does not need the field list.

--feedback: what metadata flag?
- Answer: `has_embedded_metadata` in `load_context_node` is `bool(document.metadata)`. It is true when
  the stored file has any ExifTool output (`ingestion/metadata_extractor.py`), for example a PDF's author
  or creation date. It tells the lead whether the metadata check has anything to read. It is not a field
  check, so it stays out of the field catalogue. Since the lead no longer gets field names, this flag is
  the only file-level hint it gets.

## 2. Field catalogue, by document type

A new module, `backend/app/fraud_detection/field_catalogue.py`, declares what the specialists can
see for each document type. It is built from the document's actual extracted fields, not from the
extraction spec. Each array has an entry saying how it is shown:

| Shown as | Used for |
|---|---|
| Scalars: name and type | every document type |
| Array, row labels with indices | structural arrays: `income_statement_items`, `balance_sheet_items`, `tax_computation`, `notes` (heading only) |
| Array, row count and column names | long arrays: `transactions`, `accounts`, `defaults`, `loan_terms`, `subscribers`, `holdings` |

Row labels are listed with their index, for example:

`income_statement_items[0] "Cost of sales"`

The tool refers to rows by index, not by label, so it never has to match labels itself.

The catalogue shows names and types only, not values. Labels and names come from the document, so
they are untrusted data and the prompt must say so.

Per-type entries are declared explicitly and reviewed. A new format is a one-line change, not a
keyword change.

## 3. Who sees what

- **Lead:** nothing from the catalogue.
- **Layout & Rendering:** nothing. Its tools work on the file.
- **Content Validation:** the catalogue for its numerical, date and spelling tools.
- **Digital Authenticity:** nothing. Its tool works on the file.

The catalogue goes in the dynamic part of the specialist prompt, after the cached static part, so
the prompt cache is unaffected.

## 4. Tool contract

Replace the all-or-nothing `numerical_validation_tool` with one batched tool, `numerical_check_tool`:

```json
{"checks": [
  {"check_code": "FS.IS_CAST",
   "inputs": {"revenue": "revenue", "profit": "profit_for_the_year",
              "lines": [{"field": "income_statement_items", "row": 0, "role": "expense"},
                        {"field": "income_statement_items", "row": 2, "role": "expense"},
                        {"field": "income_statement_items", "row": 5, "role": "expense"}]}}
]}
```

- `check_code` is an enum of all numerical checks. The enum is the same for every document, so the
  tool schema stays static and cacheable.
- Roles are `income`, `expense`, or `skip` (for subtotals). The tool takes the magnitude for
  expenses, so the bracket convention in the source document does not matter.
- The tool checks every field name and row index against the document. A bad reference returns
  `unable_to_assess` for that check, with the reason.
- Rows the specialist did not assign are returned as `unassigned`, not as findings.
- The response has the same shape as today, plus a `breakdown` for each check.
- One call covers all of a specialist's checks, so the LLM makes one call per document, not one per
  check.

## 5. Phasing

Decision: everything ships in one pass, so the steps below are ordering for the work, not separate releases.

1. **Shell and cast checks.** Remove the lead's field list, add the catalogue, add
   `numerical_check_tool`, and move `FS.IS_CAST` and `FS.BS_CAST` onto it. Keep the old tool
   alongside for everything else.
2. **Other statement and table checks.** Move `BS.RUNNING_BALANCE`, the `STAT.*` checks (transactions),
   `CR.*` (accounts), `LOAN.*`, `NOA.*`, `ACRA.*` and `CONST.*`. Each keeps its pure calculation
   function, which has its own tests.
3. **Remove the old tool** once every check has moved.
4. **Spelling and date tools** take field references later, if needed. Spelling already reads
   fields only to exclude known values.

## 6. Tests

- Keep the existing pure-function tests in `backend/tests/test_numerical_validation.py`.
- Add tool-level tests: an unknown field, an out-of-range row, a duplicated row, unassigned rows,
  and a wrong role showing in the trace.
- Record each check's inputs in the specialist's trace event, so a bad selection can be traced later.

## 7. Risks

- The LLM now chooses which rows to include. A wrong choice can still produce a false finding, so
  the breakdown must be visible in the report.
- The tool schema is static, so the cache holds. But the batched call is a larger input and costs
  more tokens than a single call with no arguments.
- Row labels are untrusted data. They appear in the prompt, and the system prompt must treat them as
  data.

## Open questions

1. Is one batched `numerical_check_tool` right, or do you want one tool per check family?
- Decision: pending. Explanation:
  - **One batched tool** (`numerical_check_tool`): one tool, one call per specialist, one `checks` list.
    Fewest tool calls and one place for validation. The weakness is that `inputs` is a free-form
    object, so the model can get the shape wrong and the tool has to catch it at runtime.
  - **One tool per check family** (for example `income_statement_cast`, `bank_transactions_check`,
    `credit_report_check`): about eight tools, each with a typed input schema. The model gets clearer
    guidance and bad inputs are rejected by the schema. The cost is more tools to choose from, and a
    specialist may need more than one call.
  - Recommendation: families, because the inputs differ a lot between checks and typed schemas catch
    mistakes earlier. The call-count increase is small, since most specialists use one or two families.
2. Which arrays should show row labels? The proposal is the structural arrays in section 2.
- Decision: approved.
3. Should phase 1 cover only the cast checks, as proposed?
- Decision: all at once, no phasing.

## Implementation summary (what was built)

This describes what is in the code now, in plain language. It replaces the plan above where they differ.

### The short version

The detection agent sees which fields a document has and which checks apply. It tells the system which field fills each input a check needs. The checks then run on exactly those fields. Nothing is guessed: if a field or row is missing, the check is skipped and the reason is recorded.

### Step by step, for one document

1. **Lead.** The lead planner sees only the document type, the filename, whether fields exist, and the file extension. It writes one brief per specialist.
2. **Content Validation specialist.** Its prompt contains the field list (names and types; statement lines with row numbers; long lists as a count and columns, never values), and the checks that apply with their inputs. An input marked `(matched)` already has a field of the same name; `(bind)` means the model must say which field fills it.
3. **One numerical call.** The model calls `numerical_checks_tool` once with a list of bindings (input, field, and row index for row inputs). The document type comes from the run state.
4. **The runner.** For each applicable check it passes the bound values as the check's arguments. Numeric inputs are converted to numbers first. A required input that is missing skips the check with the reason.
5. **Statement casts.** For source-of-wealth documents: income statement lines (income or expense, by row) must add up to profit for the year, both years if bound. Balance sheet sections must add up to their totals, and assets must equal liabilities plus equity. Unbound rows are reported as unassigned, not flagged.
6. **Result.** Findings, skipped checks with reasons, the statement breakdown, unassigned rows, the bindings used, and binding problems. The trace records each specialist's tool calls and arguments.

### Where the code lives

- `fraud_detection/numerical_validation/registry.py`: the `@check` decorator. A check's inputs are its keyword arguments: plain names are fields, `Num` arguments are converted to numbers, row inputs are rows, `fields` gives every field, `today` gives the run date. Required arguments (no default) skip the check when missing.
- `identity_company.py`: general identifier checks (every type), NRIC, passport MRZ, company documents, FATCA.
- `statements.py`: bank statement, source of wealth, notice of assessment.
- `records.py`: payslip, credit report, loan list, proof of address.
- `casts.py`: the statement arithmetic, with no knowledge of documents.
- `common.py`: shared helpers and reference tables (empty until real tables exist).
- `models.py`: result types.
- `runner.py`: applies bindings to the checks and builds the result.
- `langgraph/detection/field_tools.py`: the batched tool the content specialist calls.
- `langgraph/detection/binder.py`: one Haiku call that fills in the bindings for the officer's numerical-validation endpoint. If the call fails, the endpoint still runs with exact-name matches and returns a `binding_error`.
- `fraud_detection/date_anomalies.py`: a separate module with its own checks and its own small context class. It has not been moved to the new style yet.

### Checks

- All 59 numerical checks are registered from their signatures, and no check takes the old context object.
- Tests cover the checks through the runner, plus the statement casts, explicit bindings, and the binder.
- About 91% of the package's lines run under the tests. The rest are mostly untested branches.

### Open items

1. **Date module.** `date_anomalies.py` still uses the old context style. Moving it is the next step.
2. **Exact-name fallback.** A check's input with no binding falls back to a field of the same name, in code. This is deterministic, but it means the model only has to bind the mismatched names. Confirm this is acceptable.
3. **`has_extracted_fields`** is still in the lead's context. It's a boolean, but it is the same kind of context that was removed earlier.
4. **Live model.** Tests cover the code paths, not how the model chooses bindings. Run a real source-of-wealth document before relying on it.
5. **Message wording.** A value that is present but not a number now reports "Required field(s) not extracted" rather than "(unparseable)". Only the reason text changed.

## Feedback

Add comments below, or next to the section they apply to.

-
