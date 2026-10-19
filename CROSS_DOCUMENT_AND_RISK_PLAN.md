# Plan: cross-document validation and risk outlook

Status: Parts 1 and 2 are built and merged to `main` (code in `backend/app/case_pipeline/`; how it works today is in `CLAUDE.md` and `FRAUD_DETECTION_MAP.md`). **Still open:** Step 0 (the automatic trigger: today an officer clicks "Run case analysis"), the evals (phase 8), a dismissal flow, and unit tests for the hypothesiser, `verify.py` and `summary.py`. Operating notes: the table `case_analysis_runs` must exist (see `README.md`), `CASE_ANALYSIS_ENABLED=false` is the kill switch, and every run writes a step-by-step `trace` to its row for debugging.

## Scope

| Piece | Role |
|---|---|
| `case_match.py` | Not part of this module. It checks a document was uploaded to the right case (sending a mismatch to manual review), not fraud. |
| `external_validation/` | Not part of this module. Runs per document. Its findings reach the agent through each document's detection report. |
| `cross_document/deterministic.py` | Part of this module. Case-wide comparisons that need no judgement (company name, address, UEN, a person's ID and name) run first on every analysis, one finding per difference, and are given to the agent as facts. |
| `case_risk.py` | The only source of the score (deterministic). |
| `supporting_documents.py` | Rule-based recommendations; run inside the analysis and merged with the agent's requests into one `additional_documents` list. Planned: they also feed the agent, and what the agent concludes is saved to `cases.recommended_additional_documents`. |

## Step 0: per-case trigger (build first)

**Rule:** the case pipeline runs once every approved document has been scanned, and re-runs whenever that set changes.

- **Gate:** at least one approved document, none still queued, processing, extracting or detecting. Rejected and cancelled documents don't count. Approved "Other" documents do (see below).
- **Re-evaluated** on every document completion, approval, rejection, deletion, upload or re-detection.
- **Fingerprint:** each run stores a hash of its inputs (document ids, field hashes, detection reports). A mismatch means the result is **stale**.
- **Failure:** a crashed run is recorded and retryable. A superseded run is not a failure.

### Supersede: inputs change mid-run

A run in flight has outdated inputs the moment a document is approved, uploaded, rejected or re-detected. Its result is never published.

1. Mark the in-flight run superseded **when the input changes**, not when the scan finishes.
2. **Fence token:** the case row holds `current_run_id`. Every write is conditional on it, so a superseded run's writes don't apply and it halts at the next step boundary. Cancelling the network call is a cost saving, not what makes it correct.
3. The final commit is one atomic fingerprint check and write.
4. Start the new run when the gate reopens, after a 5-10 second debounce. One run per case.
5. Keep showing the previous result, marked stale, until the new one lands.
6. Cap re-runs per case per hour. Rejecting the new document restores the old fingerprint, so no re-run is needed.

The run record lives in the database, so the fence survives a restart.

### Other documents are scanned and counted

**Built.** An approved Other document is detected like any other and counts towards risk once its detection has started. `skips_detection` now applies only to a document with no type at all. It is the catch-all for shipping, trade and similar documents, so exempting it would leave a gap.

- **Rule: only checks that need no type and no template baseline run on Other.** A bespoke invoice or shipping contract compared against a canonical template would raise false alarms.
- **Runs (type-agnostic, within the document):** font consistency, text overlay, spelling, image splicing and AI-generation, and the metadata checks that compare the document with itself (creator vs export date, edit traces).
- **Layout runs in generic mode:** it uses per-type reference templates only when the type is known and confident (`layout_anomalies/references/*`); for Other it uses the document's own structure.
- **Doesn't run:**
  - type-keyed numerical and external checks: they report "nothing to check" and are never scored
  - any date or metadata rule that depends on the document type
- **Audit in phase 0:** `date_anomalies`, `metadata_analysis` and `image_manipulation` reference the document type in places. Each rule gets classified "type-agnostic" or "type-keyed" before Other documents are routed to them. Also confirm with a test that passes `unrecognised` through both field-check tools.
- **Fairness:** fewer applicable checks means less coverage, not more suspicion. Generic checks on unfamiliar layouts risk false positives (e.g. spelling on unknown terms), so Other-type documents must be in the clean-case eval.
- **Provenance:** the officer has saved its fields before approval, so the dossier marks them officer-confirmed.

## Part 1: Cross-document validation

### Flow

```
dossier (code) -> plan: hypotheses + checks (LLM) -> run the checks (code) -> judge all (LLM) -> verify (code)
```

0. **Deterministic comparisons (no LLM).** Company name, address, UEN, and a person's ID number and name across documents. Each difference is a finding on its own (`xd-det-N`); each finding is one plain sentence naming the documents by type ("The ID number is X on the credit report, but Y on the NRIC and the certificate of incumbency") and cites every document carrying either value. They are merged with the agent's (an agent finding that only restates one is dropped in code) and are also shown to the agent, grouped by value and capped, as facts it must not restate.
1. **Dossier (no LLM).** A digest per approved document, including derived facts so the model never does arithmetic:
   - statement period, transaction count and totals, median and max transaction size, top counterparties
   - declared business activity, SSIC, share capital, directors, addresses
   - each document's detection findings
2. **Plan (one Sonnet call, forced structured output).** At most 3 strong hypotheses, each with a claim, what would prove it, a **refutation test**, and up to 4 checks to run (tool plus arguments), at least one of them a refutation probe, and that probe must read something the claim does not already rest on. Everything is planned up front. Hypotheses that touch a protected attribute, or concern one document with nothing computed behind them, are dropped before any check runs.
3. **Run the checks (plain code, no model call).** The planned checks run against the read-only evidence tools. Unknown tools are dropped and a failed check is recorded as unavailable.
4. **Judge (one call).** A single call judges every hypothesis from its evidence and returns `conclusive`, `severity`, `category` and `citations` for each.
5. **Verify (code).** A verdict stands only if:
   - every citation is a ref id a tool returned in this run (exact match, no fuzzy matching)
   - a refutation probe ran: at least one tool call tagged `is_refutation_probe=True` on the complementary document the hypothesis named, which succeeded (`available: true`) and covers the relevant period, and which, if it was a plain field read, read a field the claim's own checks had not
   - it cites at least two documents, or at least one tool-computed fact
   - nothing in its text (claim, summary, rationale, probe summary) touches a protected attribute
   - the severity cap and category rules below pass

   The judge writes for a loan officer; code strips tool names, flags and ref ids from its wording (`verify.plain_text`). A verdict that cites a comparison tool's mismatch flag needs no separate refutation probe (the flag is the proof); name, address and person mismatches stop at medium, identifier conflicts may be high.

   Anything else is discarded.

One call judges every hypothesis together, and code verifies each verdict, so there is no per-hypothesis tool loop. The trade-off is that checks are fixed before any result is seen: an investigation can't re-query with a different filter. If recall is too low in the evals, add one optional follow-up round.

### Only conclusive hypotheses exist

Hypotheses that aren't proven are **discarded**: not shown, not stored, not scored. The bar is "sufficient evidence to prove it", not "looks suspicious". A wrong guess costs a few tool calls, not a false flag. Only aggregate cost telemetry is kept.

### Guardrails

- **Structured disconfirmation.** The plan fixes the refutation test and tags the check that carries it (`refutation_probe`) before any result is seen, so it can't be chosen to be easy to pass. Code checks that the tagged check ran on the named document and could genuinely have failed (the tool returned `available: true`). The verdict must also state what the probe returned, and a sample of probes is reviewed in the evals.
- **Grounded evidence only.** No tool-issued citation, no conclusive verdict.
- **Budgets:** hypotheses, tool calls per hypothesis, tokens, concurrency.
- **Seeded categories, not a checklist:** declared activity vs observed flows, scale vs capital and age, timeline contradictions, circular or related-party flows, address and identity continuity, source-of-wealth coherence.

### Severity cap

A hypothesis alone cannot reach High.

| Evidence | Highest severity |
|---|---|
| Model judgement over retrieved facts (raw statistics, comparisons) | Medium |
| A tool result carrying an explicit typed `breach_flag` | High |
| A registry or reference mismatch in a document's findings | High |

Code can tell which case applies without trusting the model: a tool that detects a hard breach returns a typed `breach_flag` (e.g. `CHRONOLOGICAL_IMPOSSIBILITY`, with its numbers). Tools returning only statistics, such as a median or a gap in days, carry no flag, so the verifier clamps severity to Medium whatever the investigator proposes. Which tool results may carry a breach flag is a fixed list in code.

### Cost and bounds

Two Sonnet calls (plan and judge, a hard cap), one Haiku call for the summary, and one Haiku call for evidence requests whenever any finding scores. No tool loop; it runs as a background job, so the limits are provider rate limits and cost. Reads are bounded: planner input about 30,000 characters (the code-established facts at most 5,000 of those), judge evidence 12,000, each tool result 900; output 3,000 and 2,500 tokens; at most 3 hypotheses of 4 checks each.

### Investigator tools

The agent proposes a theory, then these tools fetch the facts. They do all counting and comparing. The model only decides what to look at and what it means.

Rules:
1. The model never calculates. Every number comes from a tool.
2. Read-only, one case only. `case_id` is injected from state and hidden from the model.
3. Every fact carries an opaque ref id issued by the tool (e.g. `ref:bank_stmt_1:txn_42`). The model copies it and never composes one.
4. Results are small and capped.
5. Missing data is stated (`available: false`, reason), so "nothing found" is never read as "nothing wrong".

| Group | Tools | Gives the agent |
|---|---|---|
| Reading | `list_documents`, `get_fields`, `get_business_profile` | Documents and types, field values with confidence, and the declared profile merged across documents (both values if they disagree) |
| Behaviour | `transaction_stats`, `query_transactions`, `top_counterparties`, `monthly_flow`, `compare_periods`, `find_repeated_transactions`, `card_usage_tool`, `recurring_payment_amounts` | Count, totals, median, p90, max; filtered rows; counterparty shares; monthly flow; period gaps and balance continuity; repeated payee and amount groups, card reuse, recurring amounts (patterns to test against other documents, never findings alone) |
| Agreement | `compare_identifiers`, `compare_people`, `compare_linked_people`, `company_name_consistency_tool`, `address_consistency_tool` | Whether identifiers, people, company names and addresses across documents refer to the same entity |

These reuse the existing row and amount helpers and the matching logic in `company_identity.py`, `address_matching.py` and `identity_pairs.py`. No new parsing.

### Storage

Results are persisted with their input fingerprint. Only conclusive findings are stored (plus the code-made ones and the "checked and consistent" lines), together with the run's risk, outlook, summary and the merged additional documents. A completed run also writes one `risk_scores` row whose `flag_ids` are the UUIDs of the per-document `flags` behind the score.

## Part 2: Risk scoring and outlook

The agent does **not** produce the score. The score stays deterministic and auditable. The LLM does two jobs: recommending what evidence would help, and explaining the result.

### Officer summary

A short plain-language explanation: the risk level and main reasons, which findings drive it, which checks came back clean, and what would change the picture.

- Grounded: it can only restate existing findings and cite them.
- Never feeds back into the score.
- Regenerated with each run.

### Outlook: "if you get document Y, your score drops to Z"

1. Cross-document findings enter `compute_case_risk` as a new source, with corroboration when several documents back a finding.
2. The agent reads **all** findings (per-document and cross-document) and recommends additional evidence. There is no fixed finding-to-document mapping, and recommendations are **not limited to our 12 types**:

```
EvidenceRequest {
  title:           "Signed supply agreement with Meridian Logistics"
  what_it_shows:   "A contractual basis for the S$450k of recurring payments"
  addresses:       [finding ids]
  expected_effect: "full" | "partial"
}
```

3. **Re-scoring is deterministic and ignores document type.** `full` removes the addressed findings' points. `partial` multiplies them by a fixed factor held in code. The result goes through the normal banding. An unseen document type is scored like a known one.
4. **Code decides what can be reduced**, from a `FindingCategory` on every finding:

| Category | Reducible by more evidence? |
|---|---|
| `tampering_forensic` (font, metadata, layout, image, overlay) | No. A new document doesn't un-forge another. Only a fresh original helps. |
| `registry_discrepancy` | No |
| `identity_entity_conflict` (a UEN, NRIC or passport number, or registration number differs between documents) | No. A further document can't explain why two documents belong to different entities. |
| `cross_document_variance` (address, name variant, an authority or borrowing limit that differs between documents) | Yes. A statutory bridge can resolve it: tenancy agreement or change-of-address filing, deed poll, board resolution. |
| `behavioural_anomaly` | Yes |
| `unsupported_claim` | Yes |

   - Non-reducible categories are assigned by code, from a static tool-to-category map. The model may only choose among the reducible ones.
   - For cross-document findings, code picks `identity_entity_conflict` or `cross_document_variance` from the class of the field that differed (identifier vs address, name or limit), not from the model.
   - `compute_case_risk` enforces it with an explicit check that drops the address and logs it (not an `assert`).
   - A request must address at least one finding.
5. **Both directions shown:**

```
RiskOutlook {
  current: score 62, High
  requests: [{ title: "Signed supply agreement", addresses: [f2, f4], effect: "full",
               score_if_supports: 41, level_if_supports: Medium,
               score_if_contradicts: 62 }]
  best_case_all_supported: 28, Low
}
```

6. It is a conditional estimate, not a prediction. Requests are ranked by score drop. The best case is a combined re-score, not a sum, and never goes below what unreducible findings leave.
7. `supporting_documents.py` recommendations are produced in the same run and merged with the agent's requests into one `additional_documents` list (shown in one card). A request that cannot change the score (it addresses only non-reducible findings) is kept and labelled "no effect on score"; checklist items never affect the score. The list also records why it is empty.
8. **The same finding on several documents scores once** (same check, same stated value, such as a UEN a registry has no record of); the copies show at 0 points. Findings without both are never merged, so identical wording on three documents still counts as three pieces of evidence toward Critical.

## Guardrails and governance

Referenced: MAS FEAT, PDPA (and PDPC NRIC guidance), IMDA Model AI Governance Framework, NIST AI RMF. This states what the system does, not that it is certified. Confirm with GLDB compliance.

| Principle | Control |
|---|---|
| Human accountability | Advisory only. Never approves, declines or changes a status. Overrides are logged with a reason. |
| Fairness | Findings rest only on document content and consistency. Protected attributes (race, ethnicity, nationality, citizenship, religion, gender or sex, age or date of birth, marital status, disability and similar) and their proxies are removed from the model's input, forbidden in its prompt, and screened from its proposals and verdicts (`bias.py`). Subgroup false-positive rates are measured. |
| Proportionality | A request must address a finding, and the number of requests is capped. |
| Transparency | Evidence refs resolve to real data, the summary cites findings, AI text is labelled, and the score shows its arithmetic. |
| Contestability | An officer can dismiss a cross-document finding with a reason. It leaves the score and the dismissal is kept. |
| Auditability | Each run records input fingerprint, model and prompt versions, tool calls and outputs. |
| Data protection | Minimal derived facts go to the model. NRIC and account numbers are masked unless a check needs them. No raw personal data in traces beyond the audit record. |
| Security | Document text is untrusted, passed as delimited data. Tools are read-only with no side effects. Outputs are schema-validated. |
| Fail-safe | A feature flag disables the module and falls back to deterministic scoring. A failed run shows "analysis unavailable". |
| Monitoring | Track false-positive rate, dismissal and override rate, cost per case, citation discard rate and model drift. |

The model is never allowed to: recommend an outcome, set the score or level, introduce a claim not in tool results, reduce a tampering finding with other documents, use protected attributes, or follow instructions found in a document.

Open questions for the sponsor:
1. Does GLDB's model risk policy require formal validation and a model owner?
2. What retention period applies to the audit record?
3. Is sending masked derived data to an external LLM provider acceptable, or does it need zero-data-retention terms?

## Build phases

Phases 1-7 and the guardrails (except the dismissal flow) are built.

| # | Phase | Notes |
|---|---|---|
| 0 | Per-case trigger: gate, supersede with fence, debounce, fingerprint and stale flag. Scan and count Other documents. **Open** (Other documents are done; the trigger is still a button). | No LLM. Test the "inputs change just before commit" race and an Other document through detection. |
| 1 | Contracts: `FindingCategory` on every finding with the tool-to-category map, `CrossDocFinding`, `RiskOutlook`, `CaseSummary`, run table | Existing detection findings get a category |
| 2 | Dossier and investigator tools with opaque ref ids | No LLM, unit-testable |
| 3 | Plan (3 hypotheses with checks), run the checks, judge in one call, and code verification of citations, refutation test, severity cap and category | Mocked-LLM tests |
| 4 | Fold cross-document findings into `case_risk` | Existing risk tests must pass |
| 5 | Re-scoring and unified recommendations | Pure functions. Test an unknown document type scores like a known one. |
| 6 | Officer summary | Grounding check: every claim maps to a finding |
| 7 | API and case-page panel: findings with evidence, summary, outlook, stale badge | |
| G | Guardrails, built alongside 3-7: audit record, masking, injection handling, protected-attribute filter, dismissal flow, feature flag | |
| 8 | Evals: false positives on clean cases (including Other), recall on planted contradictions, subgroup parity. **Open** | Clean generated documents are the baseline |

## Risks

- **False positives from plausible theories** (the main one): discard-unless-conclusive, citation checks, disconfirmation, severity cap, clean-case eval.
- **Over-fitting to the test set:** hypothesis categories stay general and aren't written around the injected tampers. Recall on those is measured in evals, not designed in.
- **Self-adjudication:** mitigated by code verification. A separate adjudicator for High is the fallback.
- **Strict citations discarding valid findings:** tool-issued ref ids make this unlikely. The discard rate is monitored.
- **Cost and churn:** the 3-hypothesis cap and 4 checks each, no tool loop (2 Sonnet calls), and later the debounce, supersede and a re-run cap bound it.
- **Synthetic-to-real gap:** theories needing outside data can't become conclusive and are discarded.
- **Non-determinism:** temperature 0, stored results, stale flag.

## Decisions made

1. Non-conclusive hypotheses are never shown or stored.
2. Severity is capped. A hypothesis alone can't reach High without a calculation or registry mismatch.
3. The pipeline triggers automatically when all approved documents are scanned, and supersedes on change.
4. Approved Other documents are scanned and counted towards risk.
5. A case's risk score and supporting-document recommendations come only from an analysis run; the views read them.
6. This module produces cross-document findings only. A finding about one document's own figures, with nothing computed behind it, is left to that document's scan.
7. The model is never shown a person's protected attributes, and a hypothesis or verdict that touches one is dropped.
