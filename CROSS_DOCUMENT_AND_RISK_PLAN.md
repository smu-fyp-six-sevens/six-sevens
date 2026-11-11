# Design: cross-document validation and risk outlook

The per-case analysis is built (`backend/app/case_pipeline/`); `HOW_IT_WORKS.md` §3 describes it step by step and `FRAUD_DETECTION_MAP.md` §1B lists the files. This document keeps the design rules and what is still open.

**Open:** the rest of the automatic trigger (Step 0 below), the evals, and a dismissal flow for cross-document findings. `case_analysis_runs` must exist (see `README.md`); `CASE_ANALYSIS_ENABLED=false` is the kill switch; every run writes a step-by-step `trace` to its row for debugging.

## Scope

| Piece | Role |
|---|---|
| `case_match.py` | Not part of this module. Checks a document was uploaded to the right case (a mismatch goes to manual review). Not fraud. |
| `external_validation/` | Not part of this module. Runs per document; its findings reach the agent through each document's detection report. |
| `cross_document/deterministic.py` | Part of this module. Comparisons that need no judgement run first on every analysis, one finding per difference, and are given to the agent as facts. |
| `case_risk.py` | The only source of the score (deterministic). |
| `adverse_media.py` | Part of the run, outside the score. A web search for recent adverse media about the company, shown to the officer as a note (`HOW_IT_WORKS.md` §3, Step 6). |
| `additional_documents.py`, `supporting_documents.py` | Rule-based recommendations merged with the agent's requests into one list. The merged list is stored on the case (`cases.recommended_additional_documents`) when a run completes and is still the newest (the run's own result no longer carries it). Planned: the rule-based recommendations also feed the agent. |

## Step 0: per-case trigger (open parts)

The trigger is built (`service.start_if_due`, `HOW_IT_WORKS.md` §3): the gate, the fingerprint, no repeat over the same inputs, a newer run replacing an outdated one, the hourly cap. Still open:
- **Cancel the outdated run.** It runs to the end and spends its calls; only its result is dropped (`superseded`).
- **A fence on the case row** (`current_run_id`, every write conditional on it). Today the newest run wins by start time, and the check before publishing a result isn't atomic, so two instances finishing at once could both publish. Two instances deciding a run is due at the same moment could also start two (each process holds a lock per case, not across instances).
- **Debounce** (5–10 s) before an automatic start, for an officer's burst of rejections.

## Approved Other documents

Scanned and counted (built). Only checks that need no type and no template baseline run: font consistency, text overlay, spelling, image splicing and AI generation, the metadata checks that compare the document with itself, and layout in generic mode. Type-keyed numerical and external checks report "nothing to check" and are never scored. Fewer applicable checks means less coverage, not more suspicion; Other-type documents belong in the clean-case eval. The officer saves an Other document's fields before approval, so the dossier marks them officer-confirmed. Still to audit: `date_anomalies`, `metadata_analysis` and `image_manipulation` reference the document type in places; classify each rule as type-agnostic or type-keyed.

## Part 1: Cross-document validation

```
code comparisons -> dossier (code) -> plan (LLM) -> run checks (code) -> judge (LLM) -> verify (code)
```

0. **Deterministic comparisons.** Company name, address, UEN, and a person's ID number, name, date of birth, race, sex and nationality, each person compared against every document that names them. One finding per difference (`xd-det-N`), merged by value pair so a wrong ID on three documents is one row, written as one plain sentence naming the documents by type and citing every document that carries either value. A differing ID, UEN or date of birth is high; the rest are medium. Differences are also shown to the agent grouped by value and capped, as facts it must not restate, and an agent finding that only restates one is dropped in code. The protected details are compared only here and are never shown to any model.
1. **Dossier (no LLM).** A digest per approved document with derived facts so the model never does arithmetic: statement period, transaction count and totals, median and max size, top counterparties, declared activity, SSIC, share capital, directors, addresses, and each document's detection findings.
2. **Plan (one Sonnet call, forced structured output).** At most 3 hypotheses, each with a claim, what would prove it, a **refutation test**, and up to 4 checks (tool plus arguments), at least one a refutation probe that must read something the claim does not already rest on. Everything is planned up front. A hypothesis that touches a protected attribute, or concerns one document with nothing computed behind it, is dropped before any check runs.
3. **Run the checks (code).** Against the read-only evidence tools. Unknown tools are dropped and a failed check is recorded as unavailable.
4. **Judge (one call).** Judges every hypothesis from its evidence: `conclusive`, `severity`, `category`, `citations`. It writes for a loan officer; code strips tool names, flags and ref ids from its wording (`verify.plain_text`).
5. **Verify (code).** A verdict stands only if:
   - every citation is a ref id a tool returned in this run (exact match);
   - a refutation probe ran: a tool call tagged `is_refutation_probe=True` on the document the hypothesis named, that succeeded (`available: true`), and that, if it was a plain field read, read a field the claim's own checks had not. A verdict citing a comparison tool's own mismatch flag needs no separate probe;
   - it cites at least two documents or one tool-computed fact;
   - nothing in its text touches a protected attribute;
   - severity and category pass the rules below.

   Anything else is discarded. Checks are fixed before any result is seen, so an investigation can't re-query with a different filter; if recall is too low in the evals, add one optional follow-up round.

### Only conclusive hypotheses exist

Hypotheses that aren't proven are discarded: not shown, not stored, not scored. The bar is "sufficient evidence to prove it", not "looks suspicious", so a wrong guess costs a few tool calls, not a false flag. (The run's trace does list dropped hypotheses and why; decide before exposing it beyond developers.)

### Guardrails

- **Structured disconfirmation.** The plan fixes the refutation test and tags its check before any result is seen, so it can't be chosen to be easy to pass; code checks it ran on the named document and could have failed.
- **Grounded evidence only.** No tool-issued citation, no conclusive verdict.
- **Seeded categories, not a checklist:** declared activity vs observed flows, scale vs capital and age, timeline contradictions, circular or related-party flows, address and identity continuity, source-of-wealth coherence.
- **Bounded:** at most 3 hypotheses of 4 checks; two Sonnet calls, a Haiku summary, a Haiku evidence-request call and the Haiku adverse-media search (ceiling 7 calls); planner input 30,000 characters (the code-found facts at most 5,000 of them), judge evidence 12,000, each tool result 900; output 3,000 and 2,500 tokens. No tool loop; it is a background job.

### Severity cap

A hypothesis alone cannot reach High.

| Evidence | Highest severity |
|---|---|
| Model judgement over retrieved facts | Medium |
| A tool result with an explicit typed `breach_flag` (e.g. `CHRONOLOGICAL_IMPOSSIBILITY`) | High |
| A registry or reference mismatch in a document's findings | High |

Which tool results may carry a breach flag is a fixed list in code; tools returning only statistics carry none, so the verifier clamps to Medium whatever the model proposes. Name, address and person mismatches stop at medium; identifier conflicts may be high.

### Investigator tools

The agent proposes a theory and the tools fetch the facts; the model never calculates. Read-only, one case (`case_id` injected from state and hidden). Every fact carries an opaque ref id issued by the tool, which the model copies and never composes. Results are small and capped, and missing data is stated (`available: false`, reason).

| Group | Tools |
|---|---|
| Reading | `list_documents`, `get_fields`, `get_business_profile` |
| Behaviour | `transaction_stats`, `query_transactions`, `top_counterparties`, `monthly_flow`, `compare_periods`, `find_repeated_transactions`, `card_usage_tool`, `recurring_payment_amounts` (patterns to test against other documents, never findings alone) |
| Agreement | `compare_identifiers`, `compare_people`, `compare_linked_people`, `company_name_consistency_tool`, `address_consistency_tool` |

### Storage

Results persist with their input fingerprint: only conclusive findings (plus the code-made ones and the "checked and consistent" lines), with the run's risk, outlook, summary and merged additional documents. A completed run writes one `risk_scores` row, and, when it is the case's newest, its findings to the `findings` table (linked to the case only).

## Part 2: Risk scoring and outlook

The agent does **not** produce the score; it stays deterministic and auditable. The LLM recommends what evidence would help and explains the result.

**Summary.** Plain language: the level and main reasons, which findings drive it, which checks came back clean, and what would change the picture. Grounded (it can only restate existing findings and cite them), never feeds the score, regenerated each run.

**Outlook: "if you get document Y, your score drops to Z".**

1. Cross-document findings enter `compute_case_risk` as a source, with corroboration when several documents back one.
2. The agent reads all findings and recommends evidence, **not limited to our 12 types**: `{title, what_it_shows, addresses: [finding ids], expected_effect: "full" | "partial"}`. A request must address at least one finding.
3. **Re-scoring is deterministic and ignores document type.** `full` removes the addressed findings' points; `partial` multiplies them by a fixed factor in code; the result goes through normal banding.
4. **Code decides what can be reduced**, from a `FindingCategory` on every finding:

| Category | Reducible by more evidence? |
|---|---|
| `tampering_forensic` (font, metadata, layout, image, overlay) | No. A new document doesn't un-forge another. |
| `registry_discrepancy` | No |
| `identity_entity_conflict` (a UEN, NRIC, passport or registration number differs between documents) | No. A further document can't explain why two documents belong to different entities. |
| `cross_document_variance` (address, name variant, an authority or borrowing limit that differs) | Yes: a tenancy agreement, change-of-address filing, deed poll or board resolution can bridge it |
| `behavioural_anomaly`, `unsupported_claim` | Yes |

   Non-reducible categories come from a static tool-to-category map and the model may only choose among the reducible ones; for cross-document findings code picks `identity_entity_conflict` or `cross_document_variance` from the class of the field that differed. `compute_case_risk` enforces it with an explicit check that drops the address and logs it.
5. **Both directions shown:** current score, and per request `score_if_supports`, `level_if_supports`, `score_if_contradicts`, plus a best case. It is a conditional estimate, not a prediction. Requests are ranked by score drop; the best case is a combined re-score, not a sum, and never goes below what unreducible findings leave.
6. Recommendations from `supporting_documents.py` are merged with the agent's requests into one `additional_documents` list. A request that cannot change the score is kept and labelled "no effect on score"; checklist items never affect the score. The list records why it is empty.
7. **The same finding on several documents scores once** (same check, same stated value, such as a UEN a registry has no record of); the copies show at 0 points. Findings without both are never merged, so identical wording on three documents still counts as three pieces of evidence toward Critical.

## Guardrails and governance

Referenced: MAS FEAT, PDPA (and PDPC NRIC guidance), IMDA Model AI Governance Framework, NIST AI RMF. This states what the system does, not that it is certified; confirm with GLDB compliance.

| Principle | Control |
|---|---|
| Human accountability | Advisory only. Never approves, declines or changes a status. |
| Fairness | Findings rest only on document content and consistency. Protected attributes (race, ethnicity, nationality, citizenship, religion, gender or sex, age or date of birth, marital status, disability and similar) and their proxies are removed from the model's input, forbidden in its prompt, and screened from its proposals and verdicts (`bias.py`). The screen over-blocks on purpose (a bank named "Indian Overseas Bank" would drop a hypothesis naming it). |
| Proportionality | A request must address a finding, and requests are capped. |
| Transparency | Evidence refs resolve to real data, the summary cites findings, AI text is labelled, the score shows its arithmetic. |
| Contestability | Planned: an officer can dismiss a cross-document finding with a reason; it leaves the score and the dismissal is kept. |
| Auditability | Each run records input fingerprint, model and prompt versions, tool calls and outputs. |
| Data protection | Minimal derived facts go to the model; NRIC and account numbers are masked unless a check needs them. |
| Security | Document text is untrusted, passed as delimited data. Tools are read-only. Outputs are schema-validated. |
| Fail-safe | `CASE_ANALYSIS_ENABLED=false` disables the module; a failed run shows "analysis unavailable". |
| Monitoring | Planned: false-positive rate, dismissal rate, cost per case, citation discard rate. |

The model is never allowed to recommend an outcome, set the score or level, introduce a claim not in tool results, reduce a tampering finding with other documents, use protected attributes, or follow instructions found in a document.

**Open questions for the sponsor:** does GLDB's model risk policy require formal validation and a model owner; what retention period applies to the audit record; is sending masked derived data to an external LLM provider acceptable, or does it need zero-data-retention terms?

## Evals (not built)

False positives on clean generated cases (including Other documents), recall on planted contradictions, and false-positive parity across subgroups. Clean generated documents are the baseline.

## Risks

- **False positives from plausible theories** (the main one): discard-unless-conclusive, citation checks, disconfirmation, the severity cap, the clean-case eval.
- **Over-fitting to the test set:** hypothesis categories stay general and aren't written around the injected tampers; recall on those is measured, not designed in.
- **Self-adjudication:** mitigated by code verification; a separate adjudicator for High is the fallback.
- **Strict citations discarding valid findings:** tool-issued ref ids make this unlikely; monitor the discard rate.
- **Synthetic-to-real gap:** theories needing outside data can't become conclusive and are discarded.
- **Non-determinism:** the models sample at their defaults (the API no longer takes `temperature`), so runs vary; stored results, the stale flag.

## Decisions

1. Non-conclusive hypotheses are never shown or stored.
2. Severity is capped: a hypothesis alone can't reach High without a calculation or registry mismatch.
3. The pipeline starts on its own once every document is decided and every approved one scanned, and a change replaces an outdated run.
4. Approved Other documents are scanned and counted towards risk.
5. A case's risk score and recommended documents come only from an analysis run; the views read them.
6. This module produces cross-document findings only. A finding about one document's own figures, with nothing computed behind it, is left to that document's scan.
7. The model is never shown a person's protected attributes; a hypothesis or verdict that touches one is dropped. Code alone compares them.
8. Deterministic comparisons always run, each difference is its own row, and the agent does not restate them.
