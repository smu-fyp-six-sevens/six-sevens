# Design: cross-document validation and risk outlook

The case analysis is built (`backend/app/case_pipeline/`). `HOW_IT_WORKS.md` §3 describes it step by step; `FRAUD_DETECTION_MAP.md` §3 lists the files. This doc keeps the design rules and what's still open. Kill switch: `CASE_ANALYSIS_ENABLED=false`. Every run writes a step-by-step `trace` to its row.

## Still open

- **Cancel the outdated run.** *Issue:* a replaced run finishes anyway and spends its calls; only its result is dropped (`superseded`).
- **A fence on the case row** (`current_run_id`). *Issue:* two instances finishing at once could both publish a result (`CODEBASE_AUDIT.md` 2.2). (Two instances *starting* the same run is stopped by a unique index, `case_analysis_runs_one_running_idx`.)
- **Debounce** (5–10 s) before an automatic start, for a burst of officer changes.
- **Audit the "Other" checks:** `date_anomalies`, `metadata_analysis` and `image_manipulation` use the document type in places; mark each rule type-agnostic or type-keyed.
- **Dismissing a cross-document finding** (officer, with a reason; leaves the score, kept on record).
- **Evals** (§ below) and **monitoring** (false-positive, dismissal and citation-discard rates, cost per case).
- **Sponsor questions:** does GLDB's model risk policy need formal validation and a model owner? How long must the audit record be kept? Is sending masked data to an external LLM acceptable, or does it need zero-data-retention terms?

## What's in the module

| Piece | Role |
|---|---|
| `cross_document/deterministic.py` | Code comparisons, run first every time; given to the agent as facts |
| `case_risk.py` | The only source of the score (code) |
| `adverse_media.py` | A web search for bad news about the company: a note for the officer, never scored |
| `additional_documents.py`, `supporting_documents.py` | Rule-based recommendations merged with the agent's requests; stored on the case when the newest run completes |

Not in the module: `case_match.py` (right case? triage, not fraud) and `external_validation/` (per document, reaches the agent through each detection report).

**Approved "Other" documents** are scanned and counted, but only type-free checks run (fonts, overlay, spelling, image splicing, AI generation, self-comparing metadata, generic layout). Fewer checks means less coverage, not more suspicion.

## Part 1: Cross-document validation

```
code comparisons -> dossier (code) -> plan (LLM) -> run checks (code) -> judge (LLM) -> verify (code)
```

0. **Code comparisons:** company name, address, UEN, and each person's ID, name, date of birth, race, sex, nationality across every document that names them. One finding per difference, merged by value pair, in one plain sentence. A differing ID, UEN or date of birth is high; the rest medium. The agent sees them as facts and mustn't restate them. Protected details are compared only here, never shown to a model.
1. **Dossier (code):** a digest per approved document with pre-computed facts (statement totals, counterparties, activity, SSIC, capital, directors, addresses, detection findings), so the model never does arithmetic.
2. **Plan (one Sonnet call):** up to 3 hypotheses, each with a claim, what would prove it, a **refutation test**, and up to 4 checks (one must be a refutation probe reading something new). A hypothesis touching a protected attribute, or about one document with nothing computed, is dropped.
3. **Run checks (code)** with read-only tools. Unknown tools dropped; failures recorded.
4. **Judge (one call):** verdict, severity, category, citations, in plain words (code strips tool names and ids).
5. **Verify (code).** A verdict stands only if every citation is a ref id a tool returned this run; its refutation probe ran on the named document and succeeded (and read a new field); it cites two documents or one computed fact; nothing touches a protected attribute; severity and category pass the rules below. Otherwise discarded.

**Only conclusive hypotheses exist:** unproven ones aren't shown, stored or scored (the developer trace does list them).

**Bounds:** 3 hypotheses × 4 checks; two Sonnet calls plus three Haiku calls (summary, evidence requests, adverse media), ceiling 7; capped input and output sizes. No tool loop.

**Severity cap.** Model judgement alone tops out at Medium. High needs a tool result with a typed breach flag (from a fixed list) or a registry mismatch. Name, address and person mismatches stop at medium; identifier conflicts may be high.

**Tools** (read-only, one case; every fact carries a ref id the model copies, never invents; missing data says so):
- Reading: `list_documents`, `get_fields`, `get_business_profile`
- Behaviour: `transaction_stats`, `query_transactions`, `top_counterparties`, `monthly_flow`, `compare_periods`, `find_repeated_transactions`, `card_usage_tool`, `recurring_payment_amounts` (patterns to test, never findings alone)
- Agreement: `compare_identifiers`, `compare_people`, `compare_linked_people`, `company_name_consistency_tool`, `address_consistency_tool`

**Storage:** results keep their input fingerprint. A completed run writes one `risk_scores` row and, if newest, its findings to `findings`.

## Part 2: Risk score and outlook

The model never sets the score. It recommends evidence and explains the result.

- **Summary:** level, main reasons, clean checks, what would change it. Restates existing findings only; never feeds the score.
- **Outlook ("get document Y and the score drops to Z"):** the agent recommends evidence (any kind, not just the 12 types), each addressing at least one finding, as `full` or `partial`. Code re-scores: `full` removes those findings' points, `partial` scales them. Shown both ways (if it supports / contradicts), plus a combined best case that never goes below what unreducible findings leave.
- **What evidence can reduce** (code decides, by category):

| Category | Reducible? |
|---|---|
| `tampering_forensic` (font, metadata, layout, image, overlay) | No: a new document doesn't un-forge another |
| `registry_discrepancy` | No |
| `identity_entity_conflict` (UEN, NRIC, passport differ) | No |
| `cross_document_variance` (address, name variant, limits) | Yes (tenancy, change of address, deed poll, board resolution) |
| `behavioural_anomaly`, `unsupported_claim` | Yes |

- Requests that can't change the score stay, labelled "no effect on score"; checklist items never affect it.
- **The same finding on several documents scores once** (same check and stated value); other findings are never merged.

## Governance

Advisory only: the system never approves, declines or changes a status. Protected attributes and proxies (race, nationality, religion, sex, age, marital status, disability…) are removed from model input and screened from proposals and verdicts (`bias.py`; it over-blocks on purpose). Document text is untrusted and fenced; tools are read-only; outputs are schema-checked. NRIC and account numbers masked unless needed. Each run records fingerprint, models, prompts, tool calls. References: MAS FEAT, PDPA, IMDA Model AI Governance, NIST AI RMF (a statement of what it does, not a certification).

The model may never recommend an outcome, set the score, invent a claim, reduce a tampering finding with other documents, use protected attributes, or follow instructions in a document.

## Evals (not built)

False positives on clean generated cases (including Other documents), recall on planted contradictions, false-positive parity across subgroups.

## Risks

- **Plausible but wrong theories** (the main one): conclusive-only, citation checks, refutation probes, severity cap, clean-case eval.
- **Over-fitting to the test set:** categories stay general.
- **Marking its own homework:** code verifies; a separate adjudicator for High if needed.
- **Strict citations discarding real findings:** watch the discard rate.
- **Synthetic vs real:** theories needing outside data can't become conclusive.
- **Runs vary** (models sample at defaults): results are stored, with a stale flag.

## Decisions made

1. Unproven hypotheses are never shown or stored.
2. A hypothesis alone can't reach High.
3. The analysis starts on its own once every document is decided and scanned; a change replaces an outdated run.
4. Approved Other documents are scanned and counted.
5. Score and recommendations come only from a run; views just read them.
6. This module makes cross-document findings only.
7. No model sees protected attributes; only code compares them.
8. Code comparisons always run, one row per difference, and the agent doesn't restate them.
