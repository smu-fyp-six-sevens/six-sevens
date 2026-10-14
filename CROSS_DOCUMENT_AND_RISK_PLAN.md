# Plan: cross-document validation and risk outlook

Status: first version implemented on branch `agentic-experiments`, untested. Code is under `backend/app/case_pipeline/`.

Changes from the plan below:
- **Officer-triggered first.** A "Run case analysis" button (shown when every approved document is scanned) starts a run. The automatic gate, supersede and debounce (Step 0) are deferred. A newer run replaces an older one, and a result over changed documents is marked stale and not counted in the score.
- **Existing cases are untouched** unless an officer runs the analysis. A case with no analysis scores exactly as before.
- **Layout on Other documents runs in generic mode.** The layout check only uses per-type reference templates when the type is known and confident, so for Other it uses the document's own structure.
- **The two orphaned consistency tools** (company name, address) and the modules behind them (`company_identity.py`, `address_matching.py`) now live in `case_pipeline/cross_document/`, not in the detection agent.
- **Migration:** `backend/migrations/002_case_analysis_runs.sql` must be applied before the first run.
- **Kill switch:** `CASE_ANALYSIS_ENABLED=false`.
- **Run trace:** each run also writes a step-by-step `trace` (hypotheses tried, tool calls, verdicts, why each was kept or discarded) to its row, shown in a debug panel on the case page. It is for debugging and does not affect the score.

## Scope

| Piece | Role |
|---|---|
| `case_match.py` | Not part of this module. It checks a document was uploaded to the right case, not fraud. |
| `external_validation/` | Not part of this module. Runs per document. Its findings reach the agent through each document's detection report. |
| `company_name_consistency_tool`, `address_consistency_tool` | Currently orphaned. Become investigator tools. |
| `case_risk.py` | Stays the only source of the score (deterministic). |
| `supporting_documents.py` | Rule-based recommendations become one source in a unified list. |

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

Today an approved "Other" (unrecognised) document is never scanned and never counted. Approval itself is already allowed. After it, the approve handler hits `skips_detection` and logs "detection skipped". That changes: **an approved Other document is detected like any other and counts towards risk.** It is the catch-all for shipping, trade and similar documents, so exempting it would leave a gap.

- **Rule: only checks that need no type and no template baseline run on Other.** A bespoke invoice or shipping contract compared against a canonical template would raise false alarms.
- **Runs (type-agnostic, within the document):** font consistency, text overlay, spelling, image splicing and AI-generation, and the metadata checks that compare the document with itself (creator vs export date, edit traces).
- **Doesn't run:**
  - layout anomalies: it compares against per-type reference templates (`layout_anomalies/references/*`)
  - type-keyed numerical and external checks: they report "nothing to check" and are never scored
  - any date or metadata rule that depends on the document type
- **Audit in phase 0:** `date_anomalies`, `metadata_analysis` and `image_manipulation` reference the document type in places. Each rule gets classified "type-agnostic" or "type-keyed" before Other documents are routed to them. Also confirm with a test that passes `unrecognised` through both field-check tools.
- **Fairness:** fewer applicable checks means less coverage, not more suspicion. Generic checks on unfamiliar layouts risk false positives (e.g. spelling on unknown terms), so Other-type documents must be in the clean-case eval.
- **Provenance:** the officer has saved its fields before approval, so the dossier marks them officer-confirmed.

Code that changes:
- `routers/documents.py` approve handler: remove the `skips_detection` branch and launch detection. Existing approval prerequisites stay.
- `models.skips_detection`: narrow or remove it.
- `case_risk.counts_towards_risk` and the gate: include Other.
- `routers/detection.py`: drop the 409 (individual-check gate and retry).
- Frontend panel text, `CLAUDE.md`, `FRAUD_DETECTION_MAP.md`, and the tests asserting the old behaviour.

## Part 1: Cross-document validation

### Flow

```
dossier (code) -> plan: hypotheses + checks (LLM) -> run the checks (code) -> judge all (LLM) -> verify (code)
```

1. **Dossier (no LLM).** A digest per approved document, including derived facts so the model never does arithmetic:
   - statement period, transaction count and totals, median and max transaction size, top counterparties
   - declared business activity, SSIC, share capital, directors, addresses
   - each document's detection findings
2. **Plan (one Sonnet call).** At most 3 strong hypotheses, each with a claim, what would prove it, a **refutation test**, and up to 4 checks to run (tool plus arguments), at least one of them a refutation probe. Everything is planned up front.
3. **Run the checks (plain code, no model call).** The planned checks run against the read-only evidence tools. Unknown tools are dropped and a failed check is recorded as unavailable.
4. **Judge (one call).** A single call judges every hypothesis from its evidence and returns `conclusive`, `severity`, `category` and `citations` for each.
5. **Verify (code).** A verdict stands only if:
   - every citation is a ref id a tool returned in this run (exact match, no fuzzy matching)
   - a refutation probe ran: at least one tool call tagged `is_refutation_probe=True` on the complementary document the hypothesis named, which succeeded (`available: true`) and covers the relevant period
   - the severity cap and category rules below pass

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

### Cost

**3 model calls per case, 4 at most**: the plan, the judge, a short summary on Haiku, and the evidence-request call on Haiku only when a reducible finding exists. There is no tool loop. It runs It runs as a background job, so the constraints are provider rate limits and cost, not a request timeout.

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
| Behaviour | `transaction_stats`, `query_transactions`, `top_counterparties`, `monthly_flow`, `compare_periods` | Count, totals, median, p90, max; filtered rows; counterparty shares; monthly flow; period gaps and balance continuity |
| Agreement | `compare_company_names`, `compare_addresses`, `compare_people` | Whether names, addresses and people across documents refer to the same entity |

These reuse the existing row and amount helpers and the matching logic in the orphaned tools. No new parsing.

### Storage

Results are persisted with their input fingerprint. Only conclusive findings are stored.

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
7. `supporting_documents.py` recommendations use the same `EvidenceRequest` shape, so the officer sees one list.

## Guardrails and governance

Referenced: MAS FEAT, PDPA (and PDPC NRIC guidance), IMDA Model AI Governance Framework, NIST AI RMF. This states what the system does, not that it is certified. Confirm with GLDB compliance.

| Principle | Control |
|---|---|
| Human accountability | Advisory only. Never approves, declines or changes a status. Overrides are logged with a reason. |
| Fairness | Findings rest only on document content and consistency. Protected attributes (race, religion, gender, nationality, a person's age) are banned as inputs and filtered from outputs. Subgroup false-positive rates are measured. |
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

| # | Phase | Notes |
|---|---|---|
| 0 | Per-case trigger: gate, supersede with fence, debounce, fingerprint and stale flag. Scan and count Other documents. | No LLM. Test the "inputs change just before commit" race and an Other document through detection. |
| 1 | Contracts: `FindingCategory` on every finding with the tool-to-category map, `CrossDocFinding`, `RiskOutlook`, `CaseSummary`, run table | Existing detection findings get a category |
| 2 | Dossier and investigator tools with opaque ref ids | No LLM, unit-testable |
| 3 | Plan (3 hypotheses with checks), run the checks, judge in one call, and code verification of citations, refutation test, severity cap and category | Mocked-LLM tests |
| 4 | Fold cross-document findings into `case_risk` | Existing risk tests must pass |
| 5 | Re-scoring and unified recommendations | Pure functions. Test an unknown document type scores like a known one. |
| 6 | Officer summary | Grounding check: every claim maps to a finding |
| 7 | API and case-page panel: findings with evidence, summary, outlook, stale badge | |
| G | Guardrails, built alongside 3-7: audit record, masking, injection handling, protected-attribute filter, dismissal flow, feature flag | |
| 8 | Evals: false positives on clean cases (including Other), recall on planted contradictions, subgroup parity | Clean generated documents are the baseline |

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
